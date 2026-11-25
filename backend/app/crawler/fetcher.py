"""HTTP fetching with conditional GET, size cap, and an SSRF check on every redirect hop."""

from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from app.crawler.ssrf import assert_public_url

MAX_REDIRECTS = 5


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def is_html(self) -> bool:
        return self.content_type in ("text/html", "application/xhtml+xml", "")

    def text(self) -> str:
        enc = None
        ct = self.headers.get("content-type", "")
        if "charset=" in ct:
            enc = ct.split("charset=")[-1].split(";")[0].strip().strip('"')
        try:
            return self.body.decode(enc or "utf-8", errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class TooLargeError(Exception):
    pass


class Fetcher:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 15.0,
        max_bytes: int = 5_000_000,
        allow_private: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.max_bytes = max_bytes
        self.allow_private = allow_private
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
            timeout=timeout,
            follow_redirects=False,  # handled manually so each hop is SSRF-checked
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        for _ in range(MAX_REDIRECTS + 1):
            await assert_public_url(url, allow_private=self.allow_private)
            async with self.client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                    headers = {}  # validators belong to the original resource
                    url = urljoin(url, resp.headers["location"])
                    continue
                body = b""
                if resp.status_code == 200:
                    declared = int(resp.headers.get("content-length") or 0)
                    if declared > self.max_bytes:
                        raise TooLargeError(f"{declared} bytes")
                    chunks = []
                    size = 0
                    async for part in resp.aiter_bytes():
                        size += len(part)
                        if size > self.max_bytes:
                            raise TooLargeError(f">{self.max_bytes} bytes")
                        chunks.append(part)
                    body = b"".join(chunks)
                return FetchResult(
                    url=str(resp.url),
                    status=resp.status_code,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    body=body,
                )
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=None)
