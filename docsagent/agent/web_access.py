from __future__ import annotations

import hashlib
import html
import ipaddress
import socket
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_PAGE_CHARS = 16000
USER_AGENT = "DocsAgent/0.1 (+local project research)"


def _validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute HTTP or HTTPS address")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise PermissionError("local network addresses cannot be fetched")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise RuntimeError(f"could not resolve public host: {host}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise PermissionError("local or reserved network addresses cannot be fetched")
    return url


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        _validate_public_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def _fetch(url: str) -> tuple[str, str]:
    opener = build_opener(_SafeRedirect())
    request = Request(_validate_public_url(url), headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=15) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise ValueError(f"unsupported web content type: {content_type}")
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError("web page exceeds the 2 MB response limit")
            return response.geturl(), body.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"web request failed ({exc.code})") from exc
    except URLError as exc:
        raise RuntimeError(f"web request failed: {exc.reason}") from exc


class _PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = False
        self.skip_depth = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.title = True
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "tr"} and not self.skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.title = False
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "tr"} and not self.skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self.title:
            self.title_parts.append(text)
        if not self.skip_depth:
            self.parts.append(text)


class _SearchResults(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._anchor = False
        self._snippet = False
        self._url = ""
        self._title: list[str] = []
        self._snippet_parts: list[str] = []
        self._in_result = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "div" and "result" in classes:
            self._in_result = True
            self._title = []
            self._snippet_parts = []
        elif self._in_result and tag == "a" and "result__a" in classes:
            self._anchor = True
            self._url = values.get("href") or ""
        elif self._in_result and "result__snippet" in classes:
            self._snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor:
            self._anchor = False
        if tag in {"a", "td", "div"} and self._snippet:
            self._snippet = False
        if tag == "div" and self._in_result:
            if self._url and self._title:
                parsed = urlparse(self._url)
                query = parse_qs(parsed.query)
                target = query.get("uddg", [self._url])[0]
                self.results.append({"title": " ".join(self._title), "url": unquote(target), "snippet": " ".join(self._snippet_parts)})
            self._in_result = False

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._anchor:
            self._title.append(text)
        elif self._snippet:
            self._snippet_parts.append(text)


def search_web(query: str, limit: int = 5) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query) > 400:
        raise ValueError("query must contain 1 to 400 characters")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query.strip())
    _, source = _fetch(url)
    parser = _SearchResults()
    parser.feed(source)
    items = [item for item in parser.results if item["url"].startswith(("http://", "https://"))][:limit]
    return {"query": query.strip(), "items": items, "retrieved_at": time.time()}


def read_web_page(url: str) -> dict:
    final_url, source = _fetch(url)
    parser = _PageText()
    parser.feed(source)
    content = " ".join(" ".join(parser.parts).split())
    truncated = len(content) > MAX_PAGE_CHARS
    content = content[:MAX_PAGE_CHARS]
    return {
        "url": final_url,
        "title": html.unescape(" ".join(parser.title_parts))[:500],
        "content": content,
        "truncated": truncated,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "retrieved_at": time.time(),
    }
