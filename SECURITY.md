# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub security advisories](https://github.com/n36l3c7/Wepwawet/security/advisories/new), not in a
public issue. Include the version, the steps to reproduce and the impact you expect. You will get an
answer within seven days.

## Security model

Wepwawet is a personal desktop tool:

- The local server listens on `127.0.0.1` by default and refuses other addresses unless
  `server.allow_remote = true` is set.
- Each start creates a random session token (`wepwawet serve` accepts a fixed one in `WEPWAWET_TOKEN`). The window receives it once and keeps it in an
  `HttpOnly`, `SameSite=Strict` cookie; every API call needs it.
- Requests whose `Host` header is not a loopback name are rejected, which blocks DNS-rebinding pages.
- The interface runs under a Content Security Policy that allows scripts from the application only.
- API keys are read from the environment or the configuration directory (created with mode `0700`) and
  are redacted from every log line.
- The application never needs root. To measure from this machine, give `CAP_NET_RAW` to the traceroute
  tool (for example `scamper`), never to Wepwawet.
