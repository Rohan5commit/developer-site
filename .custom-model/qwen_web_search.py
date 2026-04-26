#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import os
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import modal

APP_NAME = os.getenv("MODAL_WEB_SEARCH_APP_NAME", "qwen-web-search")
SEARCH_TIMEOUT_SECONDS = float(os.getenv("CUSTOM_MODEL_WEB_SEARCH_TIMEOUT_SECONDS", "12"))
SEARCH_MAX_RESULTS = int(os.getenv("CUSTOM_MODEL_WEB_SEARCH_MAX_RESULTS", "10"))
SEARCH_MAX_FETCH_PAGES = int(os.getenv("CUSTOM_MODEL_WEB_SEARCH_MAX_FETCH_PAGES", "8"))
SEARCH_FETCH_PAGES = os.getenv("CUSTOM_MODEL_WEB_SEARCH_FETCH_PAGES", "1") == "1"
SEARXNG_BASE_URL = str(os.getenv("CUSTOM_MODEL_WEB_SEARCH_URL", "") or "").strip().rstrip("/")
DEFAULT_USER_AGENT = os.getenv(
    "CUSTOM_MODEL_WEB_SEARCH_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

app = modal.App(APP_NAME)
image = modal.Image.debian_slim().pip_install(
    "beautifulsoup4>=4.12",
    "requests>=2.32",
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


def _build_session():
    import requests

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def _searxng_search(query: str, max_results: int) -> list[dict[str, Any]]:
    session = _build_session()
    response = session.get(
        f"{SEARXNG_BASE_URL}/search",
        params={
            "q": query,
            "format": "json",
            "language": "en-US",
            "safesearch": 0,
            "categories": "general",
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        return []
    results: list[dict[str, Any]] = []
    for item in raw_results[:max_results]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        results.append(
            {
                "title": _clean_text(str(item.get("title") or "Untitled"), 180),
                "url": url,
                "snippet": _clean_text(str(item.get("content") or ""), 320),
                "source": ", ".join(str(x) for x in item.get("engines", []) if isinstance(x, str)),
            }
        )
    return results


def _duckduckgo_html_search(query: str, max_results: int) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    session = _build_session()
    response = session.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "kl": "us-en"},
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict[str, Any]] = []
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
    from bs4 import BeautifulSoup

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if parsed.path.lower().endswith(".pdf"):
        return ""

    session = _build_session()
    response = session.get(url, timeout=min(8.0, SEARCH_TIMEOUT_SECONDS), stream=False)
    response.raise_for_status()
    content_type = str(response.headers.get("content-type") or "").lower()
    if "html" not in content_type and "text" not in content_type:
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        text = _clean_text(str(meta.get("content") or ""), 420)
        if text:
            return text

    chunks: list[str] = []
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


def _augment_results(results: list[dict[str, Any]], max_fetch_pages: int) -> list[dict[str, Any]]:
    if not results or max_fetch_pages <= 0 or not SEARCH_FETCH_PAGES:
        return results

    selected = results[: max_fetch_pages]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(12, len(selected)))) as pool:
        future_map = {
            pool.submit(_fetch_page_excerpt, str(result.get("url") or "")): result
            for result in selected
        }
        for future in concurrent.futures.as_completed(future_map):
            result = future_map[future]
            try:
                excerpt = str(future.result() or "").strip()
            except Exception:
                excerpt = ""
            if excerpt:
                result["content_excerpt"] = excerpt
    return results


@app.function(image=image, timeout=120, scaledown_window=60)
def search_web(
    query: str,
    max_results: int = SEARCH_MAX_RESULTS,
    max_fetch_pages: int = SEARCH_MAX_FETCH_PAGES,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    normalized_query = " ".join(str(query or "").split()).strip()
    if not normalized_query:
        raise ValueError("query must not be empty")

    result_limit = max(1, min(int(max_results), 20))
    page_limit = max(0, min(int(max_fetch_pages), result_limit))
    errors: list[str] = []
    backend = ""
    results: list[dict[str, Any]] = []
    search_started_at = time.perf_counter()

    if SEARXNG_BASE_URL:
        try:
            results = _searxng_search(normalized_query, result_limit)
            backend = "searxng"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"searxng: {exc}")

    if not results:
        try:
            results = _duckduckgo_html_search(normalized_query, result_limit)
            backend = "duckduckgo-html"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"duckduckgo-html: {exc}")

    search_seconds = time.perf_counter() - search_started_at
    augment_started_at = time.perf_counter()
    if results:
        results = _augment_results(results, page_limit)
    augment_seconds = time.perf_counter() - augment_started_at
    total_seconds = time.perf_counter() - started_at

    return {
        "query": normalized_query,
        "backend": backend or "unavailable",
        "results": results,
        "errors": errors,
        "fetched_at": _now_iso(),
        "configured_searxng": bool(SEARXNG_BASE_URL),
        "fetched_pages": min(page_limit, len(results)),
        "timing": {
            "search_seconds": round(search_seconds, 3),
            "fetch_seconds": round(augment_seconds, 3),
            "total_seconds": round(total_seconds, 3),
        },
    }


@app.local_entrypoint()
def main(query: str, max_results: int = SEARCH_MAX_RESULTS) -> None:
    import json

    print(json.dumps(search_web.remote(query, max_results=max_results), indent=2))
