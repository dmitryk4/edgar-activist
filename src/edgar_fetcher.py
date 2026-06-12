"""
Thin wrapper around EDGAR's full-text search (EFTS).
All document fetching and parsing is handled by edgartools.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date as _date

import requests

_HEADERS = {
    "User-Agent": "Activist Ownership Research dmitrythegoat0315@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
_last_req: float = 0.0


def _get(url: str, params: dict | None = None) -> str:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < 0.13:
        time.sleep(0.13 - elapsed)
    resp = requests.get(url, headers=_HEADERS, params=params, timeout=30)
    _last_req = time.monotonic()
    resp.raise_for_status()
    return resp.text


def search_efts(
    query: str,
    forms: str,
    start_date: str = "",
    max_results: int = 200,
) -> list[dict]:
    """
    Search EDGAR full-text search. Returns list of _source dicts.
    Key fields: adsh, display_names, ciks, period_ending, file_date, form.

    Note: dateRange=custom + enddt is required when using date filters —
    omitting enddt triggers 500 errors on some queries.
    """
    results: list[dict] = []
    from_idx = 0
    size = 10
    today = _date.today().strftime("%Y-%m-%d")

    while len(results) < max_results:
        params: dict = {
            "q": f'"{query}"',
            "forms": forms,
            "from": from_idx,
            "size": min(size, max_results - len(results)),
        }
        if start_date:
            params["dateRange"] = "custom"
            params["startdt"] = start_date
            params["enddt"] = today

        try:
            data = json.loads(_get("https://efts.sec.gov/LATEST/search-index", params))
        except Exception as e:
            print(f"  EFTS search error for '{query}': {e}")
            break

        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        results.extend(h["_source"] for h in hits)
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        from_idx += size
        if from_idx >= total:
            break

    return results[:max_results]


def parse_display_name(raw: str) -> str:
    """'BlackRock Inc.  (BLK)  (CIK 0001364742)' → 'BlackRock Inc.'"""
    return re.sub(r"\s*\(.*", "", raw).strip()
