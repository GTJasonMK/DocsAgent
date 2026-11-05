from __future__ import annotations

from .web_access import _validate_public_url


class BrowserSession:
    """Use a fresh Playwright context; never attach to the user's browser profile."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("browser support requires the playwright package") from exc
        if self._context is not None:
            return
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(ignore_https_errors=False)
        except Exception:
            self.close()
            raise

    def read_page(self, url: str, max_chars: int = 16000) -> dict:
        if not 1 <= max_chars <= 50000:
            raise ValueError("max_chars must be between 1 and 50000")
        _validate_public_url(url)
        self.start()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = page.title()[:500]
            text = " ".join(page.locator("body").inner_text(timeout=10000).split())
            return {"url": page.url, "title": title, "content": text[:max_chars], "truncated": len(text) > max_chars}
        finally:
            page.close()

    def close(self) -> None:
        context, browser, playwright = self._context, self._browser, self._playwright
        self._context = self._browser = self._playwright = None
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass
