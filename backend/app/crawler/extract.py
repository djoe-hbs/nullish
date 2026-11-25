"""Main-content extraction (Trafilatura -> markdown) plus title, links and canonical URL."""

import hashlib
import re
from dataclasses import dataclass, field

import lxml.html
import trafilatura


@dataclass
class Extracted:
    title: str | None
    markdown: str
    links: list[str] = field(default_factory=list)
    canonical: str | None = None
    published: str | None = None

    @property
    def text_length(self) -> int:
        return len(self.markdown)


def _fallback_text(doc: lxml.html.HtmlElement) -> str:
    for bad in doc.xpath("//script|//style|//noscript|//nav|//footer|//header|//svg"):
        bad.drop_tree()
    body = doc.find("body")
    text = (body if body is not None else doc).text_content()
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def extract(html: str, url: str) -> Extracted:
    try:
        doc = lxml.html.fromstring(html, base_url=url)
    except (lxml.etree.ParserError, ValueError):
        return Extracted(title=None, markdown="")

    title_el = doc.find(".//title")
    title = title_el.text_content().strip() if title_el is not None else None
    canonical = None
    for el in doc.xpath('//link[@rel="canonical"][@href]'):
        canonical = el.get("href")
        break
    doc.make_links_absolute(url, resolve_base_href=True)
    links = [el.get("href") for el in doc.xpath("//a[@href]") if el.get("href")]

    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_images=False,
        include_comments=False,
        favor_recall=True,
        with_metadata=False,
    ) or ""
    meta = trafilatura.extract_metadata(html, default_url=url)
    published = getattr(meta, "date", None) if meta else None
    if meta and getattr(meta, "title", None):
        title = meta.title

    if len(markdown) < 200:
        # Trafilatura can be too aggressive on small/odd pages; fall back to visible text.
        fallback = _fallback_text(lxml.html.fromstring(html))
        if len(fallback) > len(markdown):
            markdown = fallback

    return Extracted(title=title, markdown=markdown.strip(), links=links,
                     canonical=canonical, published=published)


def content_hash(markdown: str) -> str:
    """Hash of the extracted text (whitespace-normalized), so markup/ads churn isn't a 'change'."""
    norm = re.sub(r"\s+", " ", markdown).strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()


def looks_js_rendered(html: str, extracted: Extracted) -> bool:
    """Probably a client-rendered app (React/Vue/... SPA) whose static HTML is only a shell.

    Signals: scripts present AND either very little text, or almost no links. A real
    server-rendered page nearly always has navigation links; SPA shells usually have 0-2.
    (Seen in practice: an SPA returning the same 1 kB shell, with 1 link, for every URL.)
    """
    if "<script" not in html.lower():
        return False
    return extracted.text_length < 300 or len(extracted.links) <= 2
