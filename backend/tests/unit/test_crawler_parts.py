import gzip

import httpx
import pytest

from app.crawler.extract import content_hash, extract
from app.crawler.fetcher import Fetcher
from app.crawler.robots import parse_robots
from app.crawler.sitemap import parse_sitemap
from app.crawler.ssrf import UnsafeURLError, assert_public_url
from app.crawler.urls import canonicalize, in_scope

# ---- URLs ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "base", "expected"),
    [
        ("HTTPS://Example.COM:443/a//b?utm_source=x&b=2&a=1#frag", None,
         "https://example.com/a/b?a=1&b=2"),
        ("/shoes?gclid=1", "https://shop.com/x/", "https://shop.com/shoes"),
        ("http://site.com", None, "http://site.com/"),
        ("http://site.com:8080/p", None, "http://site.com:8080/p"),
        ("mailto:a@b.com", None, None),
        ("javascript:void(0)", None, None),
        ("ftp://site.com/file", None, None),
    ],
)
def test_canonicalize(raw, base, expected):
    assert canonicalize(raw, base) == expected


def test_in_scope_rules():
    root = "https://www.shop.com/"
    assert in_scope("https://shop.com/products/1", root, [], [])  # www-insensitive
    assert not in_scope("https://other.com/", root, [], [])
    assert not in_scope("https://shop.com/logo.png", root, [], [])
    assert not in_scope("https://shop.com/cart", root, [], ["/cart*"])
    assert in_scope("https://shop.com/blog/x", root, ["/blog/*"], [])
    assert not in_scope("https://shop.com/about", root, ["/blog/*"], [])
    assert not in_scope("https://shop.com/p?id=9", root, [], [r"re:id=\d+$"])


# ---- SSRF ----------------------------------------------------------------------------------


async def fake_resolver_public(host):
    return ["93.184.216.34"]


async def fake_resolver_private(host):
    return ["10.0.0.5"]


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.1.2.3/",
     "http://[::1]/", "http://localhost:8000/", "http://db.internal/", "file:///etc/passwd",
     "http://100.64.0.1/"],
)
async def test_ssrf_blocks_internal(url):
    with pytest.raises(UnsafeURLError):
        await assert_public_url(url, resolver=fake_resolver_public)


async def test_ssrf_resolves_names():
    await assert_public_url("https://example.com/", resolver=fake_resolver_public)
    with pytest.raises(UnsafeURLError):
        await assert_public_url("https://evil.example/", resolver=fake_resolver_private)


async def test_ssrf_allow_private_for_local_testing():
    await assert_public_url("http://127.0.0.1:9000/", allow_private=True)


async def test_fetcher_blocks_redirect_to_internal_address():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    f = Fetcher("t", transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeURLError):
        await f.fetch("http://93.184.216.34/start")  # public IP literal: no DNS needed
    await f.close()


async def test_fetcher_size_cap_and_conditional_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=b"x" * 2000, headers={"content-type": "text/html"})

    f = Fetcher("t", max_bytes=1000, allow_private=True, transport=httpx.MockTransport(handler))
    from app.crawler.fetcher import TooLargeError
    with pytest.raises(TooLargeError):
        await f.fetch("http://site.test/", etag='"v1"', last_modified="Mon, 01 Jan 2024")
    assert seen["if-none-match"] == '"v1"' and seen["if-modified-since"]
    await f.close()


# ---- robots / sitemap ----------------------------------------------------------------------


def test_robots():
    r = parse_robots(
        "User-agent: *\nDisallow: /private\nCrawl-delay: 2\nSitemap: https://s.com/sm.xml\n"
    )
    assert r.allowed("https://s.com/public") and not r.allowed("https://s.com/private/x")
    assert r.sitemaps == ["https://s.com/sm.xml"] and r.crawl_delay == 2.0


SITEMAP = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://s.com/a</loc><lastmod>2026-11-01</lastmod></url>
  <url><loc> https://s.com/b </loc></url>
</urlset>"""
INDEX = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://s.com/sm1.xml</loc></sitemap></sitemapindex>"""


def test_sitemap_urlset_index_and_gzip():
    pages, nested = parse_sitemap(SITEMAP)
    assert [(p.loc, p.lastmod) for p in pages] == [("https://s.com/a", "2026-11-01"),
                                                   ("https://s.com/b", None)]
    assert parse_sitemap(INDEX) == ([], ["https://s.com/sm1.xml"])
    assert len(parse_sitemap(gzip.compress(SITEMAP))[0]) == 2
    assert parse_sitemap(b"not xml") == ([], [])


def test_sitemap_rejects_external_entities():
    evil = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            b'<urlset><url><loc>&e;</loc></url></urlset>')
    pages, _ = parse_sitemap(evil)
    assert all("root:" not in p.loc for p in pages)


# ---- extraction ----------------------------------------------------------------------------

HTML = """<html><head><title>Returns | Shop</title>
<link rel="canonical" href="/returns"></head><body>
<nav><a href="/">Home</a> <a href="/shoes?utm_source=nav">Shoes</a></nav>
<main><h1>Return policy</h1>
<p>You can return any item within 30 days of delivery for a full refund. Items must be unused
and in their original packaging. Refunds are issued to the original payment method.</p>
<h2>Exchanges</h2><p>Exchanges are free for size changes within 30 days of delivery.</p></main>
<footer>© Shop</footer><script>var tracking = 1;</script></body></html>"""


def test_extract_main_content_links_and_canonical():
    ex = extract(HTML, "https://shop.com/returns?ref=x")
    assert "30 days" in ex.markdown and "tracking" not in ex.markdown
    assert ex.title and "Return" in ex.title
    assert ex.canonical == "/returns"
    assert "https://shop.com/shoes?utm_source=nav" in ex.links


def test_content_hash_ignores_whitespace_and_case():
    assert content_hash("Hello   World\n") == content_hash("hello world")
    assert content_hash("price 10") != content_hash("price 12")


def test_spa_shell_detection():
    from app.crawler.extract import looks_js_rendered

    shell = ('<html><head><script src="/assets/app.js"></script></head><body><div id="root">'
             + "Loading our accessibility services and software solutions. " * 20
             + '</div><a href="/">Home</a></body></html>')
    assert looks_js_rendered(shell, extract(shell, "https://s.com/about"))  # lots of text, 1 link
    plain = HTML.replace("<script>var tracking = 1;</script>", "")
    assert not looks_js_rendered(plain, extract(plain, "https://shop.com/returns"))  # no scripts
    nav = "".join(f'<a href="/p{i}">P{i}</a>' for i in range(6))
    more = "<p>" + "Our support team answers questions about orders and refunds. " * 8 + "</p>"
    rich = HTML.replace("<footer>", f"{more}<nav>{nav}</nav><script>x()</script><footer>")
    assert not looks_js_rendered(rich, extract(rich, "https://shop.com/returns"))  # real page


def test_live_discovery_url_scoring():
    from app.freshness.live import terms, url_score

    q = terms("Do you offer internships for students?")
    assert "internships" in q and "you" not in q and "for" not in q
    assert url_score("https://s.com/careers/internships", q) == 1
    assert url_score("https://s.com/careers/internship-program", q) == 1  # prefix match
    assert url_score("https://s.com/about", q) == 0
    assert url_score("https://s.com/student-internships", terms("student internship")) == 2
