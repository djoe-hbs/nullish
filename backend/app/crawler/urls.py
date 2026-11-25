"""URL canonicalization and crawl-scope rules."""

import fnmatch
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z]+|gclid|fbclid|msclkid|mc_cid|mc_eid|_ga|_gl|ref|ref_src|igshid|yclid)$", re.I
)

# Non-HTML resources the crawler skips (PDFs are a later addition).
SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tif", ".tiff", ".avif",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom", ".txt",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
    ".zip", ".gz", ".tgz", ".rar", ".7z", ".tar", ".dmg", ".exe", ".msi", ".apk", ".iso",
    ".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".webm", ".mov", ".avi", ".mkv",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
)


def canonicalize(url: str, base: str | None = None) -> str | None:
    """Absolute, normalized http(s) URL without fragment or tracking params; None if unusable."""
    url = url.strip()
    if not url or url.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return None
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    port = parts.port
    netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else (
        f"{host}:{port}"
    )
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
               if not TRACKING_PARAMS.match(k))
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def same_site(url: str, root_url: str) -> bool:
    """Same host as the root, treating `www.` as equivalent."""
    a = (urlsplit(url).hostname or "").removeprefix("www.")
    b = (urlsplit(root_url).hostname or "").removeprefix("www.")
    return a == b and a != ""


def _match(url: str, pattern: str) -> bool:
    """Glob against the full URL or just the path (`/blog/*`); `re:` prefix for a regex."""
    if pattern.startswith("re:"):
        return re.search(pattern[3:], url) is not None
    path = urlsplit(url).path
    return fnmatch.fnmatch(url, pattern) or fnmatch.fnmatch(path, pattern)


def in_scope(url: str, root_url: str, include: list[str], exclude: list[str]) -> bool:
    if not same_site(url, root_url):
        return False
    if urlsplit(url).path.lower().endswith(SKIP_EXTENSIONS):
        return False
    if any(_match(url, p) for p in exclude):
        return False
    return not include or any(_match(url, p) for p in include)
