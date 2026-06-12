import sys

from edgar import Company, set_identity

set_identity("Activist Ownership Research dmitrythegoat0315@gmail.com")

from activist_parser import get_13dg_holders
from build_excel import build_workbook
from holdings_parser import get_13f_holders

TICKERS: dict[str, str] = {
    "ONL": "Orion Properties",
    "STHO": "Star Holdings",
    "VRA": "Vera Bradley",
    "THRY": "Thryv",
    "MRSN": "Mersana Therapeutics",
    "SEG": "Seaport Entertainment Group",
}

# CIK overrides for tickers edgartools can't resolve by symbol
_CIK_OVERRIDES: dict[str, int] = {
    "MRSN": 1442836,
}


def run(tickers: dict[str, str] = TICKERS) -> None:
    all_holdings: dict[str, list[dict]] = {}

    for ticker, company_label in tickers.items():
        print(f"\n{'=' * 55}")
        print(f"  {ticker}  —  {company_label}")
        print("=" * 55)

        try:
            lookup = _CIK_OVERRIDES.get(ticker) or ticker
            edgar_co = Company(lookup)
            cik = str(edgar_co.cik).zfill(10)
            print(f"[{ticker}] CIK: {cik}  |  {edgar_co.name}")
        except Exception as e:
            print(f"[{ticker}] ERROR resolving CIK: {e}")
            all_holdings[ticker] = []
            continue

        holdings: list[dict] = []

        try:
            holdings += get_13f_holders(ticker, cik)
        except Exception as e:
            print(f"[{ticker}] ERROR in 13F fetch: {e}")

        try:
            holdings += get_13dg_holders(ticker, cik)
        except Exception as e:
            print(f"[{ticker}] ERROR in 13D/G fetch: {e}")

        all_holdings[ticker] = holdings
        print(f"[{ticker}] Total holders: {len(holdings)}")

    build_workbook(list(tickers.keys()), all_holdings)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        subset = {t: TICKERS.get(t, t) for t in sys.argv[1:]}
        run(subset)
    else:
        run()
