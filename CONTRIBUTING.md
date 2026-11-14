# Contributing

Thank you for considering a contribution. This file explains how changes are made in this repository.

## Ground rules

- Everything in the repository is in English: code, comments, commit messages, documentation.
- Wepwawet uses only public data and measurement platforms within their terms of service. Changes that
  scan ports, exploit services or try to identify individuals are not accepted.
- Never commit secrets, API keys or personal data. Keys are read from the environment or from a file
  readable only by the user, and never logged.

## Development setup

See [Development](README.md#development) in the README. Install the pre-commit hooks once:

```sh
uv run pre-commit install
```

## Making a change

1. Open an issue first for anything larger than a fix, so the approach can be agreed.
2. Create a branch: `feat/<slug>`, `fix/<slug>`, `docs/<slug>` or `chore/<slug>`.
3. Add tests for new behaviour. Python tests live in `tests/`, interface tests next to the module
   (`*.test.js`, run with `node --test`).
4. Update the README, the documentation site in `docs/` and `CHANGELOG.md` (under `Unreleased`) in the
   same change when behaviour changes.
5. Run the checks listed in the README; CI runs the same ones.
6. Open a pull request describing the scope, the reason and how to test it.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <summary>`, with type one
of `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`. A breaking change
gets a `!` after the type and a `BREAKING CHANGE:` footer.

## Versioning

[Semantic Versioning](https://semver.org/). The version lives in `pyproject.toml` only; releases are
tagged `vX.Y.Z`, and CI publishes the packages for each tag.
