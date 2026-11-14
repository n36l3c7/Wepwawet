"""Turn user input into targets: IP literals as they are, hostnames resolved to every A and AAAA record.

Addresses that cannot be located on the public Internet (private, loopback, documentation,
CGNAT, multicast...) are rejected with a message that names the range and the reason.
"""

import ipaddress
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from wepwawet.core.models import IPAddress, Target

_HOSTNAME = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$")

# Special-purpose ranges (IANA registries) with the reason shown to the user, most specific first.
_SPECIAL: list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]] = [
    (ipaddress.ip_network(net), reason)
    for net, reason in [
        ("0.0.0.0/8", "'this network' (RFC 791)"),
        ("10.0.0.0/8", "a private address (RFC 1918)"),
        ("100.64.0.0/10", "shared address space for carrier-grade NAT (RFC 6598)"),
        ("127.0.0.0/8", "a loopback address"),
        ("169.254.0.0/16", "a link-local address"),
        ("172.16.0.0/12", "a private address (RFC 1918)"),
        ("192.0.0.0/24", "IETF protocol assignments (RFC 6890)"),
        ("192.0.2.0/24", "a documentation address (RFC 5737)"),
        ("192.88.99.0/24", "the deprecated 6to4 relay anycast range (RFC 7526)"),
        ("192.168.0.0/16", "a private address (RFC 1918)"),
        ("198.18.0.0/15", "a benchmarking address (RFC 2544)"),
        ("198.51.100.0/24", "a documentation address (RFC 5737)"),
        ("203.0.113.0/24", "a documentation address (RFC 5737)"),
        ("224.0.0.0/4", "a multicast address"),
        ("240.0.0.0/4", "a reserved address (RFC 1112)"),
        ("255.255.255.255/32", "the broadcast address"),
        ("::/128", "the unspecified address"),
        ("::1/128", "a loopback address"),
        ("::ffff:0:0/96", "an IPv4-mapped address; enter the IPv4 address instead"),
        ("64:ff9b:1::/48", "a local-use NAT64 address (RFC 8215)"),
        ("100::/64", "a discard-only address (RFC 6666)"),
        ("2001:db8::/32", "a documentation address (RFC 3849)"),
        ("2002::/16", "a 6to4 address; enter the embedded IPv4 address instead"),
        ("fc00::/7", "a unique local address (RFC 4193)"),
        ("fe80::/10", "a link-local address"),
        ("ff00::/8", "a multicast address"),
    ]
]


class TargetError(ValueError):
    """The input cannot be located; the message explains why and what to do."""


def rejection_reason(address: IPAddress) -> str | None:
    """Why ``address`` cannot be located, or ``None`` when it is a public address."""
    for network, reason in _SPECIAL:
        if address.version == network.version and address in network:
            return reason
    if not address.is_global:
        return "not a globally routable address"
    return None


def check_public(address: IPAddress) -> None:
    reason = rejection_reason(address)
    if reason is not None:
        raise TargetError(f"{address} is {reason}. Wepwawet only locates public addresses.")


def parse_ip(text: str) -> IPAddress | None:
    candidate = text.strip().strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


Resolver = Callable[[str], Awaitable[list[IPAddress]]]


@dataclass(frozen=True)
class ResolvedInput:
    """The parsed input: the targets to locate and the addresses that were skipped, with the reason."""

    query: str
    hostname: str | None
    targets: tuple[Target, ...]
    skipped: tuple[tuple[str, str], ...] = ()


async def resolve_input(text: str, resolver: Resolver) -> ResolvedInput:
    """Parse ``text`` as an IP address or a hostname. Raises ``TargetError`` when nothing can be located."""
    query = text.strip()
    if not query:
        raise TargetError("Enter an IP address or a hostname.")
    address = parse_ip(query)
    if address is not None:
        check_public(address)
        return ResolvedInput(query=query, hostname=None, targets=(Target(address=address),))

    hostname = query.rstrip(".").lower()
    if not _HOSTNAME.match(hostname) or "." not in hostname:
        raise TargetError(f"{query!r} is neither an IP address nor a valid hostname.")
    addresses = await resolver(hostname)
    if not addresses:
        raise TargetError(f"{hostname} did not resolve to any address. Check the name or enter an IP address.")
    targets: list[Target] = []
    skipped: list[tuple[str, str]] = []
    for resolved in sorted(set(addresses), key=lambda a: (a.version, int(a))):
        reason = rejection_reason(resolved)
        if reason is None:
            targets.append(Target(address=resolved, hostname=hostname))
        else:
            skipped.append((str(resolved), reason))
    if not targets:
        detail = "; ".join(f"{addr} is {reason}" for addr, reason in skipped)
        raise TargetError(f"{hostname} resolves only to addresses that cannot be located: {detail}.")
    return ResolvedInput(query=query, hostname=hostname, targets=tuple(targets), skipped=tuple(skipped))
