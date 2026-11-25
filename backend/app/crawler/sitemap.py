"""sitemap.xml discovery (including sitemap indexes and .gz sitemaps)."""

import gzip
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from lxml import etree

from app.crawler.fetcher import Fetcher


@dataclass
class SitemapEntry:
    loc: str
    lastmod: str | None = None


def parse_sitemap(xml: bytes) -> tuple[list[SitemapEntry], list[str]]:
    """Returns (page entries, nested sitemap URLs)."""
    if xml[:2] == b"\x1f\x8b":
        xml = gzip.decompress(xml)
    try:
        # No entity resolution / network access: sitemaps are untrusted XML (XXE).
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(xml, parser=parser)
    except etree.XMLSyntaxError:
        return [], []
    tag = etree.QName(root).localname
    pages: list[SitemapEntry] = []
    nested: list[str] = []
    for node in root:
        if not isinstance(node.tag, str):
            continue
        loc = lastmod = None
        for child in node:
            if not isinstance(child.tag, str):
                continue
            name = etree.QName(child).localname
            if name == "loc" and child.text:
                loc = child.text.strip()
            elif name == "lastmod" and child.text:
                lastmod = child.text.strip()
        if not loc:
            continue
        if tag == "sitemapindex":
            nested.append(loc)
        else:
            pages.append(SitemapEntry(loc, lastmod))
    return pages, nested


async def discover_sitemap_urls(
    fetcher: Fetcher, root_url: str, robots_sitemaps: list[str], *, limit: int, max_files: int = 10
) -> list[SitemapEntry]:
    p = urlsplit(root_url)
    queue = list(robots_sitemaps) or [urlunsplit((p.scheme, p.netloc, "/sitemap.xml", "", ""))]
    seen: set[str] = set()
    out: list[SitemapEntry] = []
    while queue and len(seen) < max_files and len(out) < limit:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            res = await fetcher.fetch(url)
        except Exception:  # noqa: BLE001 - a missing/broken sitemap is normal
            continue
        if res.status != 200:
            continue
        pages, nested = parse_sitemap(res.body)
        out.extend(pages)
        queue.extend(nested)
    return out[:limit]
