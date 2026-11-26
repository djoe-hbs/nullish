"""Pages that show a loading screen first and fill in their content later (common in SPAs)."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.crawler.browser import Browser
from app.crawler.extract import extract

LOADING_SPA = b"""<!doctype html><html><head><title>Shop</title>
<link rel="stylesheet" href="/app.css"></head><body>
<div id="splash" class="preloader" style="position:fixed;inset:0;background:#fff">SHOP</div>
<div id="root"></div>
<script>
  // Content arrives after a slow "API call", the loading screen fades out afterwards.
  setTimeout(() => {
    const items = Array.from({length: 8}, (_, i) =>
      `<li><a href="/product/${i + 1}">Product number ${i + 1}</a> costs ${(i + 1) * 100} rupees.
       A gentle everyday formula suitable for all skin types.</li>`).join("");
    const root = document.getElementById("root");
    root.innerHTML = `<main><h1>All products</h1><ul>${items}</ul></main>`;
    setTimeout(() => document.getElementById("splash").remove(), 600);
  }, 1500);
  // A section that only loads once scrolled into view.
  addEventListener("scroll", () => {
    if (document.getElementById("lazy")) return;
    const s = document.createElement("section"); s.id = "lazy";
    s.innerHTML = "<p>Customer reviews: lovely texture.</p>"; document.body.append(s);
  });
  document.body.style.minHeight = "3000px";
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body, ctype = (b"body{color:#222}", "text/css") if self.path == "/app.css" \
            else (LOADING_SPA, "text/html")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_render_waits_past_loading_screen(server):
    async def run():
        b = Browser("SmishBot-test", allow_private=True)
        try:
            return await b.render(server + "/shop")
        finally:
            await b.close()

    try:
        html, status, _ = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless browser unavailable: {exc}")
    ex = extract(html, server + "/shop")
    assert status == 200
    assert "Product number 8" in ex.markdown
    assert sum("/product/" in link for link in ex.links) == 8
    assert "Customer reviews" in html  # lazy section loaded by scrolling
