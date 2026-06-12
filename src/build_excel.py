from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "output"

_FILL_ORANGE  = PatternFill("solid", fgColor="FFA500")  # SC 13D activist
_FILL_YELLOW  = PatternFill("solid", fgColor="FFFF00")  # >10% or cross-holder
_FILL_STALE   = PatternFill("solid", fgColor="D3D3D3")  # filing >18 months old
_FILL_HEADER  = PatternFill("solid", fgColor="D9E1F2")

DETAIL_COLS = [
    "Holder Name",
    "Filing Type",
    "Ownership %",
    "Shares Held",
    "Market Value ($)",
    "Filing Date",
    "Filing Age",
    "Period",
    "CIK",
    "EDGAR Link",
]


def _filing_age(filing_date: str) -> str:
    """Return human-readable age string: '3 months ago', '2.1 years ago'."""
    if not filing_date:
        return ""
    try:
        filed = datetime.strptime(filing_date[:10], "%Y-%m-%d").date()
        days = (date.today() - filed).days
        if days < 30:
            return f"{days}d ago"
        if days < 365:
            return f"{days // 30}mo ago"
        years = days / 365
        return f"{years:.1f}yr ago"
    except ValueError:
        return ""


def _is_stale(filing_date: str, months: int = 18) -> bool:
    if not filing_date:
        return False
    try:
        filed = datetime.strptime(filing_date[:10], "%Y-%m-%d").date()
        return (date.today() - filed).days > months * 30
    except ValueError:
        return False


def _apply_header(ws) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = _FILL_HEADER
        cell.alignment = Alignment(horizontal="center", wrap_text=False)
    ws.freeze_panes = ws.cell(row=2, column=1)


def _autofit(ws) -> None:
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=8)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 2, 60)


def _row_fill(ws, row_idx: int, filing_type: str, ownership_pct: float | None, filing_date: str) -> None:
    # Priority: 13D activist > stale > >10%
    if "13D" in filing_type.upper():
        fill = _FILL_ORANGE
    elif _is_stale(filing_date):
        fill = _FILL_STALE
    elif ownership_pct is not None and ownership_pct > 10:
        fill = _FILL_YELLOW
    else:
        return
    for cell in ws[row_idx]:
        cell.fill = fill


def add_ticker_sheet(wb: Workbook, ticker: str, holdings: list[dict]) -> None:
    ws = wb.create_sheet(title=ticker)
    ws.append(DETAIL_COLS)
    _apply_header(ws)

    # Sort: 13D first, then 13G, then 13F; within type by holder name
    type_order = {"SC 13D": 0, "SC 13D/A": 0, "SC 13G": 1, "SC 13G/A": 1, "13F": 2}
    sorted_h = sorted(
        holdings,
        key=lambda x: (type_order.get(x["filing_type"], 9), x["holder_name"]),
    )

    for h in sorted_h:
        pct = h["ownership_pct"]
        fd  = h["filing_date"]
        ws.append([
            h["holder_name"],
            h["filing_type"],
            f"{pct:.1f}%" if pct is not None else "",
            h["shares_held"],
            h["market_value"],
            fd,
            _filing_age(fd),
            h["period"],
            h["cik"],
            h["edgar_link"],
        ])
        _row_fill(ws, ws.max_row, h["filing_type"], pct, fd)

    _autofit(ws)


def add_summary_sheet(wb: Workbook, tickers: list[str], all_holdings: dict[str, list[dict]]) -> None:
    ws = wb.active
    ws.title = "Summary"

    holder_map:  dict[str, dict[str, str]] = defaultdict(dict)
    holder_type: dict[str, str]            = {}
    holder_date: dict[str, str]            = {}

    type_rank = {"SC 13D": 3, "SC 13D/A": 3, "SC 13G": 2, "SC 13G/A": 2, "13F": 1}

    for ticker, holdings in all_holdings.items():
        for h in holdings:
            name   = h["holder_name"]
            pct    = h["ownership_pct"]
            shares = h["shares_held"]
            display = (
                f"{pct:.1f}%"
                if pct is not None
                else (f"{shares:,} sh" if shares else "✓")
            )
            holder_map[name][ticker] = display

            cur_rank = type_rank.get(holder_type.get(name, ""), 0)
            new_rank = type_rank.get(h["filing_type"], 0)
            if new_rank > cur_rank:
                holder_type[name] = h["filing_type"]

            # Track most recent filing date across tickers
            fd = h.get("filing_date", "")
            if fd > holder_date.get(name, ""):
                holder_date[name] = fd

    ws.append(["Holder", "Type", "Latest Filing", "Age"] + tickers + ["# Names"])
    _apply_header(ws)

    sorted_holders = sorted(
        holder_map.items(),
        key=lambda x: (-len(x[1]), x[0]),
    )

    for holder, ticker_vals in sorted_holders:
        count   = len(ticker_vals)
        h_type  = holder_type.get(holder, "")
        fd      = holder_date.get(holder, "")
        row = [holder, h_type, fd, _filing_age(fd)] + [ticker_vals.get(t, "") for t in tickers] + [count]
        ws.append(row)
        row_idx = ws.max_row

        if "13D" in h_type.upper():
            fill = _FILL_ORANGE
        elif _is_stale(fd):
            fill = _FILL_STALE
        elif count >= 3:
            fill = _FILL_YELLOW
        else:
            continue
        for cell in ws[row_idx]:
            cell.fill = fill

    _autofit(ws)


def build_workbook(tickers: list[str], all_holdings: dict[str, list[dict]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    add_summary_sheet(wb, tickers, all_holdings)
    for ticker in tickers:
        add_ticker_sheet(wb, ticker, all_holdings.get(ticker, []))

    out_path = OUTPUT_DIR / f"activist_ownership_{date.today().isoformat()}.xlsx"
    wb.save(out_path)
    print(f"\nSaved: {out_path}")
    return out_path
