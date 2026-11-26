"""Which database hosts Smish may connect to.

Smish's own Postgres and Redis are always refused: a user-added "data source" must never be a
way into Smish's internal data (sessions, password hashes, stored secrets). Private and internal
addresses are refused unless the operator sets ALLOW_PRIVATE_DB_HOSTS=true.
"""

import ipaddress
from urllib.parse import urlsplit

from app.config import Settings
from app.crawler.ssrf import _is_public, resolve


class HostNotAllowed(ValueError):
    pass


def _internal_hosts(settings: Settings) -> set[str]:
    hosts = set()
    for url in (settings.database_url, settings.redis_url):
        host = urlsplit(url.replace("+asyncpg", "")).hostname
        if host:
            hosts.add(host.lower())
    return hosts


async def resolve_db_host(host: str, settings: Settings, *, resolver=resolve) -> str:
    """Checks the host and returns the IP address to connect to (pinned, so a DNS change between
    the check and the connection can't redirect it)."""
    host = (host or "").strip().lower()
    if not host or any(c in host for c in "/@?# "):
        raise HostNotAllowed("enter a host name or IP address only")
    internal = _internal_hosts(settings)
    if host in internal:
        raise HostNotAllowed("that's Smish's own database server; it can't be added")
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(a) for a in await resolver(host)]
        except OSError as exc:
            raise HostNotAllowed(f"can't find the host {host}") from exc
    if not addrs:
        raise HostNotAllowed(f"can't find the host {host}")

    internal_ips: set[str] = set()
    for h in internal:
        try:
            internal_ips.update(await resolver(h))
        except OSError:
            pass
    for ip in addrs:
        if str(ip) in internal_ips:
            raise HostNotAllowed("that's Smish's own database server; it can't be added")
        if not settings.allow_private_db_hosts and not _is_public(ip):
            raise HostNotAllowed(
                f"{host} is a private/internal address. The server operator can allow these "
                "with ALLOW_PRIVATE_DB_HOSTS=true.")
    return str(addrs[0])
