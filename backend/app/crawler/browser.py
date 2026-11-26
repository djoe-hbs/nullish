"""Headless Chromium rendering for JavaScript-rendered sites (worker crawls + api live checks).

One browser process is launched lazily and kept warm; each render gets its own context.
Requests are intercepted so the SSRF guard also applies to everything the page loads.
"""

import asyncio
import re

import structlog

from app.crawler.ssrf import UnsafeURLError, assert_public_url

log = structlog.get_logger()

# Stylesheets are NOT skipped: app bundles (Vite, Next, webpack) load CSS together with route
# code, and a blocked stylesheet makes the import fail, so the page stays on its loading screen.
SKIP_RESOURCES = {"image", "media", "font", "texttrack", "eventsource", "websocket", "manifest"}
TRACKERS = re.compile(
    r"google-analytics\.com|googletagmanager\.com|doubleclick\.net|facebook\.net|hotjar\.com|"
    r"clarity\.ms|segment\.(io|com)|mixpanel\.com|intercom(cdn)?\.(io|com)|crisp\.chat|"
    r"tawk\.to|hubspot\.com|hs-scripts\.com|linkedin\.com/(px|li)|snap\.licdn\.com|"
    r"youtube\.com/embed|recaptcha|gstatic\.com/recaptcha"
)
LOAD_MS = 5000  # max wait for the "load" event after the DOM is ready
SETTLE_MAX_MS = 9000  # max wait for the real content to replace a loading screen
QUIET_MS = 800  # content unchanged this long = finished rendering

# Runs inside the page. Resolves once the visible text and links stop changing, no loading
# screen is covering the page and there's real content; scrolls down so lazy sections load.
WAIT_FOR_CONTENT = """
async ({maxMs, quietMs}) => {
  const start = performance.now();
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const LOADER = '[aria-busy="true"],[class*="preload" i],[id*="preload" i],[class*="loader" i],' +
    '[id*="loader" i],[class*="spinner" i],[id*="spinner" i],[class*="loading" i],' +
    '[id*="loading" i],[class*="splash" i],[id*="splash" i],[class*="skeleton" i]';
  const covering = () => {
    const area = innerWidth * innerHeight;
    for (const el of document.querySelectorAll(LOADER)) {
      const r = el.getBoundingClientRect();
      if (r.width * r.height < area * 0.25) continue;
      const cs = getComputedStyle(el);
      const shown = cs.display !== 'none' && cs.visibility !== 'hidden';
      if (shown && parseFloat(cs.opacity) > 0.05) return true;
    }
    return false;
  };
  const snap = () =>
    ((document.body && document.body.innerText) || '').length + ':' + document.links.length;
  const settle = async (budget) => {
    const until = Math.min(start + maxMs, performance.now() + budget);
    let last = snap(), since = performance.now();
    while (performance.now() < until) {
      await sleep(200);
      const now = snap();
      if (now !== last) { last = now; since = performance.now(); continue; }
      const [text, links] = now.split(':').map(Number);
      const real = text >= 200 || links > 2;
      if (performance.now() - since >= quietMs && real && !covering()) return true;
    }
    return false;
  };
  const ready = await settle(maxMs);
  // Lazy sections (infinite lists, "load on scroll") only appear once scrolled into view.
  for (let i = 0; i < 12 && performance.now() - start < maxMs; i++) {
    const before = document.documentElement.scrollHeight;
    scrollBy(0, innerHeight);
    await sleep(150);
    if (innerHeight + scrollY >= document.documentElement.scrollHeight - 2 &&
        document.documentElement.scrollHeight === before) break;
  }
  scrollTo(0, 0);
  await settle(1500);
  return ready;
}
"""


class BrowserUnavailable(RuntimeError):
    pass


class Browser:
    def __init__(self, user_agent: str, *, allow_private: bool = False, timeout_ms: int = 20_000):
        self.user_agent = user_agent
        self.allow_private = allow_private
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._lock: asyncio.Lock | None = None

    async def _ensure(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:  # many live fetches may need the browser at the same moment
            if self._browser is None or not self._browser.is_connected():
                try:
                    from playwright.async_api import async_playwright
                except ImportError as exc:
                    raise BrowserUnavailable("playwright not installed") from exc
                if self._pw is None:
                    self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(args=["--no-sandbox"])
            return self._browser

    async def render(self, url: str, *, timeout_ms: int | None = None) -> tuple[str, int, str]:
        """Returns (rendered HTML, HTTP status, final URL)."""
        browser = await self._ensure()
        ctx = await browser.new_context(user_agent=self.user_agent, java_script_enabled=True,
                                        viewport={"width": 1280, "height": 900})
        try:
            page = await ctx.new_page()

            async def guard(route):
                req = route.request
                # Never needed for text content; skipping them makes renders much faster.
                if req.resource_type in SKIP_RESOURCES or TRACKERS.search(req.url):
                    return await route.abort()
                try:
                    await assert_public_url(req.url, allow_private=self.allow_private)
                except UnsafeURLError:
                    return await route.abort()
                await route.continue_()

            await page.route("**/*", guard)
            timeout = timeout_ms or self.timeout_ms
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # "load" can hang on a slow third-party resource; the DOM is usable without it.
            try:
                await page.wait_for_load_state("load", timeout=min(LOAD_MS, timeout // 3))
            except Exception:  # noqa: BLE001
                pass
            # Wait for the real content (loading screens, client-side data fetches, lazy
            # sections) rather than a fixed delay.
            try:
                ready = await page.evaluate(
                    WAIT_FOR_CONTENT,
                    {"maxMs": min(SETTLE_MAX_MS, timeout // 2), "quietMs": QUIET_MS},
                )
                if not ready:
                    log.info("render_not_settled", url=url)
            except Exception as exc:  # noqa: BLE001 - e.g. the page navigated itself; use what's there
                log.info("render_wait_failed", url=url, error=str(exc))
            return await page.content(), (resp.status if resp else 200), page.url
        finally:
            await ctx.close()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None
