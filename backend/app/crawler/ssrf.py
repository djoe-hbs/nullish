"""SSRF guard: never let a configured site (or a redirect) point the crawler at internal services.

Checked after DNS resolution and again on every redirect hop (see fetcher.py).
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeURLError(ValueError):
    pass


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        or ip in ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT
    )


async def resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def assert_public_url(url: str, *, allow_private: bool = False, resolver=resolve) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"only http/https URLs are allowed: {url}")
    host = parts.hostname
    if not host:
        raise UnsafeURLError(f"URL has no host: {url}")
    if allow_private:
        return
    if host in ("localhost",) or host.endswith((".localhost", ".internal", ".local")):
        raise UnsafeURLError(f"internal host not allowed: {host}")
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(a) for a in await resolver(host)]
        except OSError as exc:
            raise UnsafeURLError(f"cannot resolve {host}: {exc}") from exc
    if not addrs:
        raise UnsafeURLError(f"cannot resolve {host}")
    for ip in addrs:
        if not _is_public(ip):
            raise UnsafeURLError(f"{host} resolves to a non-public address ({ip})")
