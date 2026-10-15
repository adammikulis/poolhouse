"""Bounded requests to numeric loopback endpoints, without redirects or ambient credentials."""

import ipaddress
import math
import ssl

from poolhouse import httpguard


def fetch(url: str, *, headers: dict[str, str] | None = None, method: str = "GET",
          context: ssl.SSLContext | None = None, timeout: float = 10) -> httpguard.Fetched:
    """Return the first answer from a literal loopback address; never follow its Location."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 10):
        raise httpguard.Refused("a loopback request timeout must be finite and between zero and ten seconds")
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise httpguard.Refused("a loopback URL has no whitespace or control characters")
    try:
        parts, host, _port = httpguard.split(url)
        address = ipaddress.ip_address(parts.hostname or "")
    except ValueError:
        raise httpguard.Refused("a loopback request needs a numeric loopback address") from None
    if (not address.is_loopback or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None)
            or '%' in host or not host.isascii() or '@' in parts.netloc
            or parts.fragment):
        raise httpguard.Refused("a loopback request needs a numeric loopback address without credentials or a fragment")
    if context is not None and (not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED):
        raise httpguard.Refused("loopback TLS verifies both the certificate and its server name")

    def exact(destination: str) -> None:
        if destination != url:
            raise httpguard.Refused("a loopback request cannot change its destination")

    limits = httpguard.Limits(allow_hosts=frozenset({host}), context=context, vet=exact,
                              timeout=timeout, deadline_s=timeout, follow_redirects=False)
    return httpguard.fetch(url, method=method, headers=headers, limits=limits)
