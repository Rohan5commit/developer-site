#!/usr/bin/env python3

import concurrent.futures
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

HOST = os.getenv("CUSTOM_MODEL_WEBSEARCH_HOST", "127.0.0.1")
PORT = int(os.getenv("CUSTOM_MODEL_WEBSEARCH_PORT", "18781"))
SEARCH_TIMEOUT_SECONDS = float(os.getenv("CUSTOM_MODEL_WEBSEARCH_TIMEOUT_SECONDS", "12"))
SEARCH_MAX_RESULTS = int(os.getenv("CUSTOM_MODEL_WEBSEARCH_MAX_RESULTS", "10"))
SEARCH_MAX_FETCH_PAGES = int(os.getenv("CUSTOM_MODEL_WEBSEARCH_MAX_FETCH_PAGES", "8"))
SEARCH_FETCH_PAGES = os.getenv("CUSTOM_MODEL_WEBSEARCH_FETCH_PAGES", "1") == "1"
DEFAULT_USER_AGENT = os.getenv(
    "CUSTOM_MODEL_WEBSEARCH_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clean_text(text: str, max_chars: int = 600) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max(0, max_chars - 1)].rstrip() + "…"


def _hostname(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


def _decode_duckduckgo_redirect(url: str) -> str:
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        target = query.get("uddg", [""])[0]
        if target:
            return unquote(target)
    except Exception:
        pass
    return url


def _request(url: str, method: str = "GET", data: Optional[bytes] = None, accept: str = "text/html") -> str:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": accept,
    }
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _duckduckgo_html_search(query: str, max_results: int) -> List[Dict[str, Any]]:
    html = _request(
        "https://html.duckduckgo.com/html/",
        method="POST",
        data=urlencode({"q": query, "kl": "us-en"}).encode("utf-8"),
    )
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []
    for block in soup.select(".result"):
        anchor = block.select_one("a.result__a")
        if anchor is None:
            continue
        href = _decode_duckduckgo_redirect(str(anchor.get("href") or "").strip())
        if not href:
            continue
        snippet_node = block.select_one(".result__snippet")
        snippet = _clean_text(snippet_node.get_text(" ", strip=True) if snippet_node else "", 320)
        results.append(
            {
                "title": _clean_text(anchor.get_text(" ", strip=True), 180),
                "url": href,
                "snippet": snippet,
                "source": _hostname(href),
            }
        )
        if len(results) >= max_results:
            break
    return results


def _fetch_page_excerpt(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if parsed.path.lower().endswith(".pdf"):
        return ""
    try:
        request = Request(
            url,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            },
            method="GET",
        )
        with urlopen(request, timeout=min(8.0, SEARCH_TIMEOUT_SECONDS)) as response:
            content_type = str(response.headers.get("content-type") or "").lower()
            if "html" not in content_type and "text" not in content_type:
                return ""
            charset = response.headers.get_content_charset() or "utf-8"
            html = response.read().decode(charset, errors="replace")
    except Exception:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        text = _clean_text(str(meta.get("content") or ""), 420)
        if text:
            return text
    chunks: List[str] = []
    total = 0
    for node in soup.find_all(["h1", "h2", "h3", "p", "li"], limit=40):
        text = _clean_text(node.get_text(" ", strip=True), 260)
        if len(text) < 32:
            continue
        chunks.append(text)
        total += len(text) + 1
        if total >= 720:
            break
    return _clean_text(" ".join(chunks), 720)


def _augment_results(results: List[Dict[str, Any]], max_fetch_pages: int) -> List[Dict[str, Any]]:
    if not results or max_fetch_pages <= 0 or not SEARCH_FETCH_PAGES:
        return results
    selected = results[: max_fetch_pages]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(12, len(selected)))) as pool:
        future_map = {pool.submit(_fetch_page_excerpt, str(result.get("url") or "")): result for result in selected}
        for future in concurrent.futures.as_completed(future_map):
            result = future_map[future]
            try:
                excerpt = str(future.result() or "").strip()
            except Exception:
                excerpt = ""
            if excerpt:
                result["content_excerpt"] = excerpt
    return results


def _search_payload(query: str, max_results: int, max_fetch_pages: int) -> Dict[str, Any]:
    started_at = time.perf_counter()
    normalized_query = " ".join(str(query or "").split()).strip()
    if not normalized_query:
        raise ValueError("query must not be empty")
    result_limit = max(1, min(int(max_results), 20))
    page_limit = max(0, min(int(max_fetch_pages), result_limit))
    errors: List[str] = []
    backend = ""
    results: List[Dict[str, Any]] = []
    search_started_at = time.perf_counter()
    try:
        results = _duckduckgo_html_search(normalized_query, result_limit)
        backend = "duckduckgo-html"
    except Exception as exc:
        errors.append("duckduckgo-html: %s" % exc)
    search_seconds = time.perf_counter() - search_started_at
    augment_started_at = time.perf_counter()
    if results:
        results = _augment_results(results, page_limit)
    fetch_seconds = time.perf_counter() - augment_started_at
    total_seconds = time.perf_counter() - started_at
    return {
        "query": normalized_query,
        "backend": backend or "unavailable",
        "results": results,
        "errors": errors,
        "fetched_at": _now_iso(),
        "configured_searxng": False,
        "fetched_pages": min(page_limit, len(results)),
        "timing": {
            "search_seconds": round(search_seconds, 3),
            "fetch_seconds": round(fetch_seconds, 3),
            "total_seconds": round(total_seconds, 3),
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CustomModelSearch/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json(
                200,
                {
                    "ok": True,
                    "fetched_at": _now_iso(),
                    "backend": "duckduckgo-html",
                    "defaults": {
                        "max_results": SEARCH_MAX_RESULTS,
                        "max_fetch_pages": SEARCH_MAX_FETCH_PAGES,
                    },
                },
            )
            return
        if parsed.path != "/search":
            self._write_json(404, {"error": "not_found"})
            return
        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0]
        max_results = params.get("max_results", [SEARCH_MAX_RESULTS])[0]
        max_fetch_pages = params.get("max_fetch_pages", [SEARCH_MAX_FETCH_PAGES])[0]
        try:
            payload = _search_payload(query, int(max_results), int(max_fetch_pages))
        except ValueError as exc:
            self._write_json(400, {"error": str(exc)})
            return
        except Exception as exc:
            self._write_json(500, {"error": str(exc)})
            return
        self._write_json(200, payload)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
