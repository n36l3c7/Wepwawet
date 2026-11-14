"""Run management for the API: start a run in the background, look it up, cancel it."""

import asyncio
import functools
import logging
import secrets
from collections import OrderedDict
from collections.abc import Callable

from wepwawet.core.events import EventBus
from wepwawet.core.history import History
from wepwawet.core.models import RunReport, RunStatus
from wepwawet.core.pipeline import Pipeline

log = logging.getLogger(__name__)

PipelineFactory = Callable[[EventBus], Pipeline]


class RunManager:
    """Keeps the most recent runs in memory and at most ``max_active`` running at once."""

    def __init__(
        self,
        bus: EventBus,
        pipeline_factory: PipelineFactory,
        keep: int = 50,
        max_active: int = 1,
        history: History | None = None,
    ) -> None:
        self._bus = bus
        self._factory = pipeline_factory
        self._runs: OrderedDict[str, RunReport] = OrderedDict()
        self._tasks: dict[str, asyncio.Task[RunReport]] = {}
        self._keep = keep
        self._max_active = max_active
        self.history = history
        self.busy_elsewhere: Callable[[], bool] = lambda: False

    def active(self) -> list[str]:
        return [run_id for run_id, task in self._tasks.items() if not task.done()]

    def start(self, query: str) -> RunReport:
        if len(self.active()) >= self._max_active or self.busy_elsewhere():
            raise RuntimeError("Another run is in progress. Wait for it to finish or cancel it.")
        run = RunReport(id=secrets.token_hex(6), query=query.strip())
        self._runs[run.id] = run
        while len(self._runs) > self._keep:
            oldest, _ = self._runs.popitem(last=False)
            self._tasks.pop(oldest, None)
        task = asyncio.create_task(self._factory(self._bus).run(run), name=f"run-{run.id}")
        task.add_done_callback(functools.partial(self._finished, run.id))
        self._tasks[run.id] = task
        return run

    def _finished(self, run_id: str, task: asyncio.Task[RunReport]) -> None:
        run = self._runs.get(run_id)
        if run is not None and self.history is not None and run.status is not RunStatus.RUNNING:
            try:
                self.history.save(run)
            except Exception:  # the run itself succeeded; a full disk must not hide it
                log.exception("Could not save run %s to the history", run_id)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("Run %s failed", run_id, exc_info=error)
            run = self._runs.get(run_id)
            if run is not None and run.status is RunStatus.RUNNING:
                run.status, run.error = RunStatus.FAILED, f"internal error: {error.__class__.__name__}"
                self._bus.publish("run", run.model_dump(mode="json"))

    def get(self, run_id: str) -> RunReport | None:
        run = self._runs.get(run_id)
        if run is None and self.history is not None:
            run = self.history.get(run_id)
        return run

    def list(self) -> list[RunReport]:
        return list(reversed(self._runs.values()))

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def wait(self, run_id: str) -> RunReport | None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self._runs.get(run_id)
