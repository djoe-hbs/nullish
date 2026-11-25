"""Headless Chromium rendering for JavaScript-rendered sites (worker crawls + api live checks).

One browser process is launched lazily and kept warm; each render gets its own context.
Requests are intercepted so the SSRF guard also applies to everything the page loads.
"""

import asyncio
import re

from app.crawler.ssrf import UnsafeURLError, assert_public_url

SKIP_RESOURCES = {"image", "media", "font", "stylesheet", "texttrack", "eventsource", "websocket",
                  "manifest"}
TRACKERS = re.compile(
    r"google-analytics\.com|googletagmanager\.com|doubleclick\.net|facebook\.net|hotjar\.com|"
    r"clarity\.ms|segment\.(io|com)|mixpanel\.com|intercom(cdn)?\.(io|com)|crisp\.chat|"
    r"tawk\.to|hubspot\.com|hs-scripts\.com|linkedin\.com/(px|li)|snap\.licdn\.com|"
    r"youtube\.com/embed|recaptcha|gstatic\.com/recaptcha"
)
SETTLE_MS = 2500  # max extra wait for client-side data fetches after "load"


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
        ctx = await browser.new_context(user_agent=self.user_agent, java_script_enabled=True)
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
            resp = await page.goto(url, wait_until="load", timeout=timeout_ms or self.timeout_ms)
            # Let client-side data fetches finish, but don't wait for chatty widgets/analytics
            # that never go idle.
            try:
                await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            except Exception:  # noqa: BLE001 - settle timeout is fine, the DOM is usable
                pass
            return await page.content(), (resp.status if resp else 200), page.url
        finally:
            await ctx.close()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None
