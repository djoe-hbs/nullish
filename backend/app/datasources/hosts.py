"""Which database hosts Smish may connect to.

Smish's own Postgres and Redis are always refused: a user-added "data source" must never be a
way into Smish's internal data (sessions, password hashes, stored secrets). Private and internal
addresses are refused unless the operator sets ALLOW_PRIVATE_DB_HOSTS=true.
"""

import ipaddress
import re
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


HOST_LABEL = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")


def clean_host(raw: str) -> str:
    """Validates what the user typed in the Host field, with a hint for common mistakes."""
    host = (raw or "").strip().lower()
    if not host:
        raise HostNotAllowed("enter the database server's host name or IP address")
    if "://" in host or "@" in host or "/" in host:
        raise HostNotAllowed("enter only the host name (like db.example.com), not a URL or "
                             "connection string")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]  # [::1] style IPv6
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        raise HostNotAllowed("put the port in the Port field, not after the host name")
    labels = host.rstrip(".").split(".")
    if len(host) > 253 or not all(HOST_LABEL.match(label) for label in labels):
        raise HostNotAllowed(f"\"{raw.strip()[:80]}\" isn't a valid host name")
    return host.rstrip(".")


async def resolve_db_host(host: str, settings: Settings, *, resolver=resolve) -> str:
    """Checks the host and returns the IP address to connect to (pinned, so a DNS change between
    the check and the connection can't redirect it)."""
    host = clean_host(host)
    internal = _internal_hosts(settings)
    if host in internal:
        raise HostNotAllowed("that's Smish's own database server; it can't be added")
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(a) for a in await resolver(host)]
        except (OSError, UnicodeError, ValueError) as exc:
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
