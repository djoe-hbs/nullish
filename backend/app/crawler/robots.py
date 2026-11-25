"""robots.txt handling (stdlib parser, our own fetcher so the SSRF guard applies)."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.crawler.fetcher import Fetcher

UA_TOKEN = "NullishBot"


@dataclass
class Robots:
    parser: RobotFileParser | None  # None = no robots.txt / unreadable -> allow all
    sitemaps: list[str] = field(default_factory=list)
    crawl_delay: float | None = None

    def allowed(self, url: str) -> bool:
        return self.parser is None or self.parser.can_fetch(UA_TOKEN, url)


def parse_robots(text: str) -> Robots:
    rp = RobotFileParser()
    rp.parse(text.splitlines())
    delay = rp.crawl_delay(UA_TOKEN)
    return Robots(rp, list(rp.site_maps() or []), float(delay) if delay else None)


async def load_robots(fetcher: Fetcher, root_url: str) -> Robots:
    p = urlsplit(root_url)
    url = urlunsplit((p.scheme, p.netloc, "/robots.txt", "", ""))
    try:
        res = await fetcher.fetch(url)
    except Exception:  # noqa: BLE001 - unreachable robots.txt: treat as allow-all
        return Robots(None)
    if res.status == 200:
        return parse_robots(res.text())
    if res.status in (401, 403):
        # Convention: access-restricted robots.txt means "disallow everything".
        return parse_robots("User-agent: *\nDisallow: /")
    return Robots(None)
