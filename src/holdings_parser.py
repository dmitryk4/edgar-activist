"""
13F institutional holders — discovery via EDGAR EFTS (multiple passes), parsing via edgartools.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from edgar import Filing, Company

from edgar_fetcher import parse_display_name, search_efts

_STOP = {"INC", "LLC", "THE", "AND", "REIT", "CORP", "LTD", "GROUP", "CO", "HOLDINGS"}


def _multi_pass_search(queries: list[tuple[str, str | None]], form: str, max_per_query: int = 200) -> list[dict]:
    """
    Run multiple EFTS searches and deduplicate by accession number.
    Each tuple is (query_string, start_date_or_None).
    """
    seen: set[str] = set()
    combined: list[dict] = []

    for query, start_date in queries:
        hits = search_efts(query, form, start_date=start_date or "", max_results=max_per_query)
        for h in hits:
            acc = h.get("adsh", "")
            if acc and acc not in seen:
                seen.add(acc)
                combined.append(h)

    return combined


def get_13f_holders(ticker: str, cik: str) -> list[dict]:
    print(f"[{ticker}] Fetching 13F filings...")

    try:
        company = Company(ticker)
        company_name = company.name or ticker
    except Exception:
        company = Company(int(cik))
        company_name = company.name or ticker

    words = [
        w.strip(".,") for w in company_name.split()
        if len(w.strip(".,")) > 2 and w.strip(".,").upper() not in _STOP
    ]
    name_query  = " ".join(words[:2]) if len(words) >= 2 else words[0] if words else ticker
    word1       = words[0] if words else ticker
    name_variants = [ticker.upper()] + [w.upper() for w in words[:3]]

    recent  = (datetime.today() - timedelta(days=120)).strftime("%Y-%m-%d")
    broader = (datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d")

    # Four passes — each surfaces a different slice of filers before EFTS errors out:
    #   1. Full name + recent window  → current-quarter filers, ranked by recency
    #   2. Full name + broader window → late filers / prior quarter
    #   3. Full name + no date        → different relevance ranking, surfaces others
    #   4. First word only + no date  → catches filers using shortened name
    #                                    (no dateRange on word1 — short words + dateRange cause 500s)
    queries = [
        (name_query, recent),
        (name_query, broader),
        (name_query, None),
        (word1,      None),
    ]

    hits = _multi_pass_search(queries, "13F-HR", max_per_query=400)

    if not hits:
        print(f"[{ticker}] No 13F results.")
        return []

    periods = sorted(
        {h.get("period_ending", "") for h in hits if h.get("period_ending")},
        reverse=True,
    )
    latest_period = periods[0] if periods else ""
    # Sort by file_date desc so amendments (most recent) are processed before originals
    recent_hits = sorted(
        [h for h in hits if h.get("period_ending") == latest_period],
        key=lambda h: h.get("file_date", ""),
        reverse=True,
    )
    print(f"[{ticker}] {len(recent_hits)} 13F filers for period {latest_period}")

    holders: list[dict] = []
    seen_filers: set[str] = set()  # deduplicate filers across passes

    for hit in recent_hits:
        raw_names = hit.get("display_names") or ["Unknown"]
        filer_name = parse_display_name(raw_names[0])
        filer_cik_str = (hit.get("ciks") or ["0"])[0]
        accession = hit.get("adsh", "")

        if not accession or filer_cik_str in seen_filers:
            continue
        seen_filers.add(filer_cik_str)

        try:
            filer_cik_int = int(filer_cik_str) if filer_cik_str.strip("0") else 0

            filing = Filing(
                cik=filer_cik_int,
                company=filer_name,
                form=hit.get("form", "13F-HR"),
                filing_date=hit.get("file_date", ""),
                accession_no=accession,
            )

            thirteenf = filing.obj()
            if thirteenf is None:
                continue

            holdings_df: pd.DataFrame | None = getattr(thirteenf, "holdings", None)
            if holdings_df is None or holdings_df.empty:
                continue

            mask = pd.Series(False, index=holdings_df.index)

            if "Ticker" in holdings_df.columns:
                mask |= holdings_df["Ticker"].fillna("").str.upper() == ticker.upper()

            if "Issuer" in holdings_df.columns and len(name_variants) > 1:
                issuer_up = holdings_df["Issuer"].fillna("").str.upper()
                sig_words = [v for v in name_variants if v != ticker.upper()][:3]
                if sig_words:
                    all_present = pd.Series(True, index=holdings_df.index)
                    for word in sig_words:
                        all_present &= issuer_up.str.contains(word, regex=False)
                    mask |= all_present

            matched = holdings_df[mask]
            if matched.empty:
                continue

            for _, row in matched.iterrows():
                def _int(val):
                    try:
                        return int(val) if val is not None and not pd.isna(val) else None
                    except (ValueError, TypeError):
                        return None

                holders.append({
                    "holder_name":  filer_name,
                    "filing_type":  "13F",
                    "ownership_pct": None,
                    "shares_held":  _int(row.get("SharesPrnAmount")),
                    "market_value": _int(row.get("Value")),
                    "filing_date":  hit.get("file_date", ""),
                    "period":       latest_period,
                    "cik":          filer_cik_str,
                    "accession_no": accession,
                    "edgar_link": (
                        f"https://www.sec.gov/Archives/edgar/data/{filer_cik_int}"
                        f"/{accession.replace('-', '')}/{accession}-index.htm"
                    ),
                })

        except Exception:
            continue

    print(f"[{ticker}] {len(holders)} 13F holders found.")
    return holders
