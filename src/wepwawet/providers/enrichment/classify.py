"""Network classification: residential, mobile, hosting, CDN, education, business.

It drives the caveats shown with the estimate ("you are locating the exit server"...). Inputs:
the published IP ranges of the big clouds and CDNs, PeeringDB's network type, the AS name and
the naming scheme of PTR records. Each signal is listed in the evidence, so the user can judge.
"""

import ipaddress
import re
from typing import Any

from wepwawet.core.http import ProviderError
from wepwawet.core.models import Evidence, EvidenceKind, IPAddress, NetworkClass, TargetReport
from wepwawet.providers.base import EnrichmentProvider, EnrichmentResult, ProviderContext

RANGES_TTL = 86_400
CLOUD_RANGES = {
    "Amazon Web Services": "https://ip-ranges.amazonaws.com/ip-ranges.json",
    "Google Cloud": "https://www.gstatic.com/ipranges/cloud.json",
    "Oracle Cloud": "https://docs.oracle.com/iaas/tools/public_ip_ranges.json",
    "Cloudflare": "https://api.cloudflare.com/client/v4/ips",
}
CDN_PROVIDERS = {"Cloudflare"}

# Well-known origin ASNs, for networks whose ranges are not published in a fetchable file.
KNOWN_ASNS: dict[int, tuple[NetworkClass, str]] = {
    8075: (NetworkClass.HOSTING, "Microsoft Azure"),
    14061: (NetworkClass.HOSTING, "DigitalOcean"),
    16276: (NetworkClass.HOSTING, "OVHcloud"),
    24940: (NetworkClass.HOSTING, "Hetzner"),
    63949: (NetworkClass.HOSTING, "Akamai Connected Cloud (Linode)"),
    20473: (NetworkClass.HOSTING, "Vultr"),
    51167: (NetworkClass.HOSTING, "Contabo"),
    12876: (NetworkClass.HOSTING, "Scaleway"),
    9009: (NetworkClass.HOSTING, "M247 (often used by VPN services)"),
    60068: (NetworkClass.HOSTING, "Datacamp / CDN77 (often used by VPN services)"),
    16265: (NetworkClass.HOSTING, "Leaseweb"),
    60781: (NetworkClass.HOSTING, "Leaseweb"),
    45102: (NetworkClass.HOSTING, "Alibaba Cloud"),
    132203: (NetworkClass.HOSTING, "Tencent Cloud"),
    20940: (NetworkClass.CDN, "Akamai"),
    16625: (NetworkClass.CDN, "Akamai"),
    54113: (NetworkClass.CDN, "Fastly"),
    15133: (NetworkClass.CDN, "Edgio / Edgecast"),
    13335: (NetworkClass.CDN, "Cloudflare"),
}

PEERINGDB_TYPES = {
    "cable/dsl/isp": NetworkClass.RESIDENTIAL,
    "content": NetworkClass.HOSTING,
    "educational/research": NetworkClass.EDUCATION,
    "enterprise": NetworkClass.BUSINESS,
    "government": NetworkClass.BUSINESS,
}

INTERNETDB_TAGS = {
    "vpn": NetworkClass.VPN,
    "proxy": NetworkClass.VPN,
    "tor": NetworkClass.VPN,
    "cloud": NetworkClass.HOSTING,
    "cdn": NetworkClass.CDN,
}

_WORD = r"(?:^|[^a-z0-9]){}(?:[^a-z0-9]|$)"
PTR_HINTS: list[tuple[NetworkClass, re.Pattern[str]]] = [
    (cls, re.compile("|".join(_WORD.format(w) for w in words)))
    for cls, words in [
        (NetworkClass.MOBILE, ["mobile", "mob", "lte", "4g", "5g", "3g", "gprs", "umts", "cellular", "wap", "wwan"]),
        (
            NetworkClass.RESIDENTIAL,
            [
                "dsl",
                "adsl",
                "vdsl",
                "xdsl",
                "ftth",
                "fttx",
                "fttc",
                "fiber",
                "fibre",
                "cable",
                "dyn",
                "dynamic",
                "dynip",
                "pool",
                "dhcp",
                "cust",
                "customer",
                "home",
                "broadband",
                "ppp",
                "pppoe",
                "bras",
                "bng",
                "res",
                "residential",
                "client",
                "user",
                "subscriber",
                "dialup",
                "cpe",
            ],
        ),
        (NetworkClass.HOSTING, ["server", "srv", "vps", "vm", "dedicated", "cloud", "colo", "hosting", "hosted"]),
    ]
]
AS_NAME_HINTS: list[tuple[NetworkClass, re.Pattern[str]]] = [
    (NetworkClass.MOBILE, re.compile(r"mobile|wireless|cellular|\bgsm\b|\blte\b", re.IGNORECASE)),
    (NetworkClass.HOSTING, re.compile(r"hosting|cloud|datacent|data cent|\bvps\b|colocation", re.IGNORECASE)),
    (NetworkClass.VPN, re.compile(r"\bvpn\b|proxy", re.IGNORECASE)),
    (NetworkClass.EDUCATION, re.compile(r"universit|college|\bedu\b|research network|\bnren\b", re.IGNORECASE)),
]

CAVEATS = {
    NetworkClass.RESIDENTIAL: "Residential network: the realistic ceiling is the city of the provider's PoP or BNG, "
    "not the subscriber's address.",
    NetworkClass.MOBILE: "Mobile network: traffic leaves through a gateway that can be hundreds of kilometres "
    "from the user.",
    NetworkClass.HOSTING: "Hosting or cloud network: you are locating the server, which may be a VPN or proxy exit.",
    NetworkClass.VPN: "VPN or proxy network: you are locating the exit server, not the user.",
    NetworkClass.CDN: "CDN network: the address may be served from many places at once (anycast).",
}


def _cidrs(provider: str, data: Any) -> list[str]:
    """CIDR strings from each provider's published file."""
    if not isinstance(data, dict):
        return []
    if provider == "Amazon Web Services":
        return [p["ip_prefix"] for p in data.get("prefixes", []) if "ip_prefix" in p] + [
            p["ipv6_prefix"] for p in data.get("ipv6_prefixes", []) if "ipv6_prefix" in p
        ]
    if provider == "Google Cloud":
        return [p.get("ipv4Prefix") or p.get("ipv6Prefix") for p in data.get("prefixes", []) if p]
    if provider == "Oracle Cloud":
        return [c["cidr"] for region in data.get("regions", []) for c in region.get("cidrs", []) if "cidr" in c]
    if provider == "Cloudflare":
        result = data.get("result") or {}
        return list(result.get("ipv4_cidrs", [])) + list(result.get("ipv6_cidrs", []))
    return []


def match_ranges(address: IPAddress, cidrs: list[str]) -> str | None:
    for cidr in cidrs:
        if not cidr:
            continue
        network = ipaddress.ip_network(cidr, strict=False)
        if network.version == address.version and address in network:
            return cidr
    return None


def ptr_classes(names: list[str]) -> list[tuple[NetworkClass, str]]:
    found: list[tuple[NetworkClass, str]] = []
    for name in names:
        lowered = name.lower()
        for cls, pattern in PTR_HINTS:
            match = pattern.search(lowered)
            if match and cls not in [c for c, _ in found]:
                found.append((cls, match.group(0).strip(".-")))
    return found


class ClassifyProvider(EnrichmentProvider):
    name = "classify"
    label = "Network classification"
    stage = 3  # reads the routing, PeeringDB and PTR findings

    async def enrich(self, report: TargetReport, ctx: ProviderContext) -> EnrichmentResult:
        address = report.target.address
        votes: list[tuple[NetworkClass, str]] = []
        result = EnrichmentResult(cached=True)

        for provider, url in CLOUD_RANGES.items():
            try:
                data, cached = await ctx.http.get_json("cloud-ranges", url, ttl=RANGES_TTL)
            except ProviderError as exc:
                ctx.log("info", self.name, f"{provider} ranges unavailable: {exc.message}")
                continue
            result.cached &= cached
            cidr = match_ranges(address, _cidrs(provider, data))
            if cidr:
                cls = NetworkClass.CDN if provider in CDN_PROVIDERS else NetworkClass.HOSTING
                votes.append((cls, f"inside {provider}'s published range {cidr}"))

        network = report.network
        if network and network.asn in KNOWN_ASNS:
            cls, owner = KNOWN_ASNS[network.asn]
            votes.append((cls, f"AS{network.asn} belongs to {owner}"))
        if network and network.as_name:
            for cls, pattern in AS_NAME_HINTS:
                if pattern.search(network.as_name):
                    votes.append((cls, f"AS name '{network.as_name}'"))

        for evidence in report.evidence:
            if evidence.source == "peeringdb" and evidence.details.get("info_types"):
                for info_type in evidence.details["info_types"]:
                    pdb_class = PEERINGDB_TYPES.get(str(info_type).lower())
                    if pdb_class:
                        votes.append((pdb_class, f"PeeringDB type '{info_type}'"))
            if evidence.source == "rdns" and evidence.details.get("ptr"):
                for cls, word in ptr_classes(evidence.details["ptr"]):
                    votes.append((cls, f"PTR contains '{word}'"))
            if evidence.source == "internetdb":
                for tag in evidence.details.get("tags", []):
                    tag_class = INTERNETDB_TAGS.get(str(tag).lower())
                    if tag_class:
                        votes.append((tag_class, f"InternetDB tag '{tag}'"))
            if evidence.source == "rdns" and evidence.details.get("examples"):
                for cls, word in ptr_classes(evidence.details["examples"]):
                    votes.append((cls, f"neighbour PTRs contain '{word}'"))

        classes = _decide(votes)
        result.classes = classes
        result.caveats = [CAVEATS[c] for c in classes if c in CAVEATS]
        result.evidence.append(
            Evidence(
                source=self.name,
                kind=EvidenceKind.INFO,
                summary="Network looks "
                + (" + ".join(c.value for c in classes) if classes else "unclassified")
                + (f" ({'; '.join(reason for _, reason in votes[:4])})" if votes else ""),
                details={
                    "classes": [c.value for c in classes],
                    "signals": [{"class": c.value, "reason": r} for c, r in votes],
                },
            )
        )
        return result


def _decide(votes: list[tuple[NetworkClass, str]]) -> list[NetworkClass]:
    """Strong signals (published ranges, known ASNs) win; otherwise the most voted classes."""
    if not votes:
        return []
    counts: dict[NetworkClass, int] = {}
    for cls, reason in votes:
        strong = "published range" in reason or "belongs to" in reason or "InternetDB tag" in reason
        counts[cls] = counts.get(cls, 0) + (3 if strong else 1)
    best = max(counts.values())
    chosen = [cls for cls, count in counts.items() if count == best]
    # A mobile signal matters even when outvoted: it changes how far the user may be.
    if NetworkClass.MOBILE in counts and NetworkClass.MOBILE not in chosen:
        chosen.append(NetworkClass.MOBILE)
    return chosen
