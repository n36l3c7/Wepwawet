import ipaddress

import pytest

from wepwawet.core.models import IPAddress
from wepwawet.core.targets import TargetError, rejection_reason, resolve_input


def resolver(table: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    async def resolve(name: str) -> list[IPAddress]:
        return [ipaddress.ip_address(a) for a in table.get(name, [])]

    return resolve


NO_DNS = resolver({})


@pytest.mark.parametrize(
    ("address", "fragment"),
    [
        ("10.1.2.3", "private"),
        ("192.168.1.1", "private"),
        ("172.20.0.1", "private"),
        ("100.64.1.1", "carrier-grade NAT"),
        ("127.0.0.1", "loopback"),
        ("169.254.10.10", "link-local"),
        ("192.0.2.5", "documentation"),
        ("198.51.100.23", "documentation"),
        ("203.0.113.9", "documentation"),
        ("198.18.0.1", "benchmarking"),
        ("224.0.0.1", "multicast"),
        ("240.0.0.1", "reserved"),
        ("2001:db8::1", "documentation"),
        ("fd00::1", "unique local"),
        ("fe80::1", "link-local"),
        ("::1", "loopback"),
        ("::ffff:8.8.8.8", "IPv4-mapped"),
    ],
)
def test_special_ranges_are_rejected(address: str, fragment: str) -> None:
    reason = rejection_reason(ipaddress.ip_address(address))
    assert reason is not None and fragment in reason


@pytest.mark.parametrize("address", ["193.0.14.129", "1.1.1.1", "2001:7fd::1", "2606:4700:4700::1111"])
def test_public_addresses_are_accepted(address: str) -> None:
    assert rejection_reason(ipaddress.ip_address(address)) is None


async def test_ip_literal() -> None:
    result = await resolve_input(" 193.0.14.129 ", NO_DNS)
    assert [str(t.address) for t in result.targets] == ["193.0.14.129"]
    assert result.hostname is None


async def test_bracketed_ipv6() -> None:
    result = await resolve_input("[2001:7fd::1]", NO_DNS)
    assert result.targets[0].version == 6


async def test_private_literal_message() -> None:
    with pytest.raises(TargetError, match=r"10\.0\.0\.5 is a private address \(RFC 1918\)"):
        await resolve_input("10.0.0.5", NO_DNS)


async def test_hostname_resolves_to_all_records() -> None:
    dns = resolver({"k.example.net": ["2001:7fd::1", "193.0.14.129", "10.0.0.1"]})
    result = await resolve_input("K.Example.NET.", dns)
    assert result.hostname == "k.example.net"
    assert [str(t.address) for t in result.targets] == ["193.0.14.129", "2001:7fd::1"]
    assert all(t.hostname == "k.example.net" for t in result.targets)
    assert result.skipped[0][0] == "10.0.0.1"


async def test_hostname_with_only_private_records() -> None:
    with pytest.raises(TargetError, match="cannot be located"):
        await resolve_input("intranet.example.com", resolver({"intranet.example.com": ["192.168.0.10"]}))


async def test_unknown_hostname() -> None:
    with pytest.raises(TargetError, match="did not resolve"):
        await resolve_input("nothing.example.com", NO_DNS)


@pytest.mark.parametrize("text", ["", "   ", "not a host", "-bad.example", "printer", "a..b.com"])
async def test_invalid_input(text: str) -> None:
    with pytest.raises(TargetError):
        await resolve_input(text, NO_DNS)
