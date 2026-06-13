"""
13D/13G beneficial ownership filers.

Discovery uses two complementary sources:
  1. EDGAR company browse (owner=include) — authoritative subject-company index,
     returns ALL filers who have filed 13D/G on the target company.
  2. EDGAR EFTS full-text search — catches any remaining filers via multiple passes.

Parsing uses edgartools for XBRL-structured filings (post-Dec 2024) and falls
back to regex for older HTML-only filings.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

from edgar import Company, Filing
from edgar_fetcher import _get, parse_display_name, search_efts

_ATOM = "http://www.w3.org/2005/Atom"

# Cover-page patterns for SC 13D/G (Row 11/13 on the form)
_PCT_PATTERNS = [
    re.compile(r"percent\s+of\s+class[^\d%]{0,150}?(\d{1,2}\.?\d*)\s*%", re.I | re.S),
    re.compile(r"row\s*\(?\s*1[13]\s*\)?[^\d%]{0,80}?(\d{1,2}\.?\d*)\s*%", re.I | re.S),
    re.compile(r"13[\.\s]*[|:\t ]+\s*(\d{1,2}\.?\d*)\s*%", re.I),
]


def _extract_pct_from_text(text: str) -> float | None:
    for pat in _PCT_PATTERNS:
        m = pat.search(text)
        if m:
            val = float(m.group(1))
            if 0 < val <= 100:
                return round(val, 2)
    for m in re.finditer(r"\b(\d{1,2}\.?\d*)\s*%", text):
        val = float(m.group(1))
        if 1.0 <= val <= 80.0:
            return round(val, 2)
    return None


def _edgar_browse_13dg(cik: str) -> list[dict]:
    """
    Fetch 13D/G filings directly from EDGAR's subject-company index.
    Uses the company browse Atom feed — more complete than EFTS text search
    because it's EDGAR's own cross-reference, not a relevance-ranked text index.
    """
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik}&type=SC+13"
        "&dateb=&owner=include&count=400&output=atom"
    )
    try:
        xml_text = _get(url, f"browse13dg_{cik}")
        root = ET.fromstring(xml_text)
    except Exception as e:
        print(f"  EDGAR browse error: {e}")
        return []

    results: list[dict] = []
    for entry in root.findall(f"{{{_ATOM}}}entry"):
        content = entry.find(f"{{{_ATOM}}}content")
        if content is None:
            continue

        def _tag(name: str) -> str:
            el = content.find(f"{{{_ATOM}}}{name}")
            return el.text.strip() if el is not None and el.text else ""

        form        = _tag("filing-type")
        file_date   = _tag("filing-date")
        accession   = _tag("accession-number")   # e.g. "0000905148-24-003186"
        filing_href = _tag("filing-href")        # e.g. ".../data/1495320/000090514824003186/..."

        if not accession or not any(x in form.upper() for x in ["13D", "13G"]):
            continue

        # CIK from filing-href = where EDGAR stored the file (may be subject company)
        path_cik_m = re.search(r"/data/(\d+)/", filing_href)
        access_cik = path_cik_m.group(1).zfill(10) if path_cik_m else cik.zfill(10)

        # CIK from accession prefix = filer identity (used for deduplication)
        filer_cik  = accession.split("-")[0].zfill(10)

        results.append({
            "adsh":          accession,
            "form":          form,
            "file_date":     file_date,
            "period_ending": "",
            "ciks":          [filer_cik, cik],   # filer_cik used for dedup
            "display_names": [f"(CIK {filer_cik})", ""],
            "_access_cik":   access_cik,          # correct path for file fetch
            "_from_browse":  True,
        })

    print(f"  EDGAR browse returned {len(results)} 13D/G entries for CIK {cik}")
    return results


def _hit_is_about_company(hit: dict, target_cik: str) -> bool:
    target_int = int(target_cik) if target_cik.strip("0") else 0
    return any(
        c.strip("0") and int(c) == target_int
        for c in (hit.get("ciks") or [])
    )


def _filer_from_hit(hit: dict, target_cik: str) -> tuple[str, str]:
    raw_names = hit.get("display_names") or []
    raw_ciks  = hit.get("ciks") or []
    target_int = int(target_cik) if target_cik.strip("0") else 0

    for c, n in zip(raw_ciks, raw_names):
        if c.strip("0") and int(c) != target_int:
            return parse_display_name(n), c

    if raw_names:
        return parse_display_name(raw_names[0]), raw_ciks[0] if raw_ciks else "0"
    return "Unknown", "0"


def get_13dg_holders(ticker: str, cik: str) -> list[dict]:
    print(f"[{ticker}] Fetching 13D/13G filings...")

    try:
        company = Company(ticker)
        company_name = company.name or ticker
    except Exception:
        company = Company(int(cik))
        company_name = company.name or ticker

    query_name = re.sub(
        r"\s+(INC|LLC|CORP|LTD|GROUP|CO|REIT)\.?$", "", company_name, flags=re.I
    ).strip().rstrip(",")
    word1 = query_name.split()[0] if query_name.split() else query_name

    forms = "SC 13D,SC 13G,SC 13D/A,SC 13G/A"

    # --- Pass 1: EDGAR subject-company index (authoritative, no text-search limits) ---
    raw = _edgar_browse_13dg(cik)

    # --- Passes 2-4: EFTS full-text (catches filers using different name variants) ---
    # Note: EFTS 13D/G hits only carry the *filer's* CIK, not the subject company's CIK,
    # so _hit_is_about_company incorrectly drops all of them. Trust the text-search
    # relevance for quoted name/ticker queries instead.
    efts = (
        search_efts(query_name, forms, max_results=400) +
        search_efts(ticker,     forms, max_results=200) +
        search_efts(word1,      forms, max_results=200)
    )
    raw += efts

    # Drop filings older than 3 years
    cutoff = f"{date.today().year - 3}-{date.today().strftime('%m-%d')}"
    raw = [h for h in raw if h.get("file_date", "") >= cutoff]

    # Deduplicate by accession
    seen_acc: set[str] = set()
    unique: list[dict] = []
    for h in raw:
        acc = h.get("adsh", "")
        if acc and acc not in seen_acc:
            seen_acc.add(acc)
            unique.append(h)

    # Keep most recent filing per filer (sorted so amendment beats original)
    unique.sort(key=lambda h: h.get("file_date", ""), reverse=True)
    latest_per_filer: dict[str, dict] = {}
    for h in unique:
        filer_name, filer_cik_tmp = _filer_from_hit(h, cik)
        key = filer_cik_tmp or filer_name.upper()
        if key not in latest_per_filer:
            latest_per_filer[key] = h
    unique = list(latest_per_filer.values())

    print(f"[{ticker}] {len(unique)} 13D/13G filers to inspect")

    holders: list[dict] = []
    for hit in unique:
        filer_name, filer_cik = _filer_from_hit(hit, cik)
        form_type  = hit.get("form", (hit.get("root_forms") or [""])[0])
        accession  = hit.get("adsh", "")
        file_date  = hit.get("file_date", "")

        if not accession:
            continue

        ownership_pct: float | None = None
        filer_cik_int = int(filer_cik) if filer_cik.strip("0") else 0

        # For browse-sourced hits, use the access CIK (file path CIK) not the filer CIK
        access_cik = hit.get("_access_cik", filer_cik)
        access_cik_int = int(access_cik) if access_cik.strip("0") else filer_cik_int

        try:
            filing = Filing(
                cik=access_cik_int,
                company=filer_name,
                form=form_type,
                filing_date=file_date,
                accession_no=accession,
            )

            obj = filing.obj()

            # Resolve filer name — covers empty string (browse hits), CIK placeholder, Unknown
            if not filer_name or filer_name.startswith("(CIK ") or filer_name == "Unknown":
                persons = getattr(obj, "reporting_persons", None) or [] if obj else []
                if persons:
                    filer_name = getattr(persons[0], "name", None) or filer_name
                if not filer_name or filer_name.startswith("(CIK "):
                    filer_name = filing.company or filer_name
                # Final fallback: EDGAR company lookup by CIK
                if (not filer_name or filer_name.startswith("(CIK ")) and filer_cik_int:
                    try:
                        filer_name = Company(filer_cik_int).name or filer_name
                    except Exception:
                        pass

            # 1. Try edgartools structured data (XBRL, Dec 2024+)
            if obj is not None:
                for person in (getattr(obj, "reporting_persons", None) or []):
                    pct = getattr(person, "percent_of_class", None)
                    try:
                        val = float(pct) if pct is not None else 0.0
                    except (ValueError, TypeError):
                        val = 0.0
                    if val > 0:
                        ownership_pct = round(val, 2)
                        break

            # 2. Fallback: regex on raw filing text (pre-2024 HTML filings)
            if not ownership_pct:
                try:
                    ownership_pct = _extract_pct_from_text(filing.text())
                except Exception:
                    pass

        except Exception as e:
            print(f"  [{ticker}] Could not parse {form_type} for {filer_name}: {e}")

        holders.append({
            "holder_name":   filer_name,
            "filing_type":   form_type,
            "ownership_pct": ownership_pct,
            "shares_held":   None,
            "market_value":  None,
            "filing_date":   file_date,
            "period":        hit.get("period_ending", ""),
            "cik":           filer_cik,
            "accession_no":  accession,
            "edgar_link": (
                f"https://www.sec.gov/Archives/edgar/data/{filer_cik_int}"
                f"/{accession.replace('-', '')}/{accession}-index.htm"
            ),
        })

    print(f"[{ticker}] {len(holders)} 13D/13G holders found.")
    return holders
