"""Public Taiwan market data (TWSE / TPEx open data, no API key) -> metrics.

Pure parsing functions take the raw JSON rows so they can be tested offline; apply_* functions write
to the database as created_by='system:mops'. Official facts are written directly (not via proposals):
they carry a source URL and can be re-fetched, so there is nothing for a human to judge.

Units: the APIs report thousands of TWD for revenue/costs; shares as whole shares (we store thousands).
Quarterly income statements are YEAR-TO-DATE; see core.derive_quarterly_metrics.
"""
from __future__ import annotations

import json
import ssl
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import core

CREATED_BY = "system:mops"

URLS = {
    "monthly_listed": "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    "monthly_otc": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
    "quarterly_listed": "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
    "quarterly_otc": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci",
    "shares_listed": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "shares_otc": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
}


# ---------------------------------------------------------------- http

def get_json(url: str, timeout: int = 30) -> list[dict]:
    """GET JSON. Falls back to curl when the Python SSL store cannot verify the chain
    (seen on Windows Store Python behind inspection proxies)."""
    req = urllib.request.Request(url, headers={"User-Agent": "stockcrm/0.2", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (ssl.SSLError, urllib.error.URLError) as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
    out = subprocess.run(["curl", "-s", "-m", str(timeout), "-A", "stockcrm/0.2", url],
                         capture_output=True, check=True)
    return json.loads(out.stdout.decode("utf-8"))


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _roc_year(v) -> int:
    return int(str(v).strip()) + 1911


# ---------------------------------------------------------------- monthly revenue

def parse_monthly_rows(rows: list[dict]) -> list[dict]:
    """Normalise TWSE/TPEx monthly revenue rows (same schema on both)."""
    out = []
    for r in rows:
        ym = str(r.get("資料年月", "")).strip()
        ticker = str(r.get("公司代號", "")).strip()
        if len(ym) < 5 or not ticker:
            continue
        year, month = _roc_year(ym[:-2]), int(ym[-2:])
        out.append({
            "ticker": ticker, "name": r.get("公司名稱", "").strip(),
            "year": year, "month": month, "period": f"{year}-{month:02d}",
            "revenue": _num(r.get("營業收入-當月營收")),
            "revenue_prev_month": _num(r.get("營業收入-上月營收")),
            "revenue_last_year": _num(r.get("營業收入-去年當月營收")),
            "yoy_pct": _num(r.get("營業收入-去年同月增減(%)")),
            "remark": (r.get("備註") or "").strip().strip("-"),
            "published": str(r.get("出表日期", "")).strip(),
        })
    return out


def fetch_monthly() -> list[dict]:
    rows = []
    for key in ("monthly_listed", "monthly_otc"):
        rows += [dict(r, _source=URLS[key]) for r in get_json(URLS[key])]
    parsed = parse_monthly_rows(rows)
    for p, r in zip(parsed, [r for r in rows if r.get("公司代號") and r.get("資料年月")]):
        p["source"] = r["_source"]
    return parsed


def apply_monthly(conn, parsed: list[dict], tickers: set[str] | None = None) -> dict:
    """Write revenue_m actuals for the TW companies we track. Also stores the same month of the
    previous year (the API ships it) so YoY works from the first run. Returns a summary."""
    wanted = tickers if tickers is not None else {c["ticker"] for c in core.tw_companies(conn)}
    written, updated_periods = [], []
    for row in parsed:
        if row["ticker"] not in wanted or row["revenue"] is None:
            continue
        cid = f"TW:{row['ticker']}"
        src = row.get("source", URLS["monthly_listed"])
        core.set_metric(conn, cid, "revenue_m", row["period"], "actual", row["revenue"], src, created_by=CREATED_BY)
        if row["revenue_last_year"] is not None:
            core.set_metric(conn, cid, "revenue_m", f"{row['year'] - 1}-{row['month']:02d}", "actual",
                            row["revenue_last_year"], src, created_by=CREATED_BY)
        if row["revenue_prev_month"] is not None:
            py, pm = (row["year"], row["month"] - 1) if row["month"] > 1 else (row["year"] - 1, 12)
            core.set_metric(conn, cid, "revenue_m", f"{py}-{pm:02d}", "actual", row["revenue_prev_month"], src,
                            created_by=CREATED_BY)
        written.append({"company_id": cid, "period": row["period"], "revenue": row["revenue"],
                        "yoy_pct": row["yoy_pct"], "remark": row["remark"]})
        updated_periods.append(row["period"])
    missing = sorted(wanted - {w["company_id"].split(":")[1] for w in written})
    return {"written": written, "missing": missing, "latest_period": max(updated_periods) if updated_periods else None}


# ---------------------------------------------------------------- quarterly income statement (YTD)

def parse_quarterly_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        ticker = str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()
        year = r.get("年度") or r.get("Year")
        q = r.get("季別") or r.get("Season")
        if not ticker or not year or not q:
            continue
        out.append({
            "ticker": ticker, "year": _roc_year(year), "quarter": int(str(q).strip()),
            "revenue": _num(r.get("營業收入")),
            "gross_profit": _num(r.get("營業毛利（毛損）淨額")) if _num(r.get("營業毛利（毛損）淨額")) is not None
            else _num(r.get("營業毛利（毛損）")),
            "opex": _num(r.get("營業費用")),
            "net_income": _num(r.get("淨利（淨損）歸屬於母公司業主")) if _num(r.get("淨利（淨損）歸屬於母公司業主")) is not None
            else _num(r.get("本期淨利（淨損）")),
            "eps": _num(r.get("基本每股盈餘（元）")),
            "published": str(r.get("出表日期") or r.get("Date") or "").strip(),
        })
    return out


def fetch_quarterly() -> list[dict]:
    parsed = []
    for key in ("quarterly_listed", "quarterly_otc"):
        rows = get_json(URLS[key])
        for p in parse_quarterly_rows(rows):
            p["source"] = URLS[key]
            parsed.append(p)
    return parsed


def apply_quarterly(conn, parsed: list[dict], tickers: set[str] | None = None) -> dict:
    wanted = tickers if tickers is not None else {c["ticker"] for c in core.tw_companies(conn)}
    stored, derived, skipped = [], [], []
    for row in parsed:
        if row["ticker"] not in wanted:
            continue
        cid = f"TW:{row['ticker']}"
        core.upsert_financials_ytd(conn, cid, row["year"], row["quarter"], row["revenue"], row["gross_profit"],
                                   row["opex"], row["net_income"], row["eps"], row.get("source"))
        stored.append({"company_id": cid, "year": row["year"], "quarter": row["quarter"]})
        res = core.derive_quarterly_metrics(conn, cid, row["year"], row["quarter"], created_by=CREATED_BY)
        (derived if res["written"] else skipped).append({"company_id": cid, **res})
    return {"stored": stored, "derived": derived, "skipped": skipped}


# ---------------------------------------------------------------- shares outstanding

def parse_shares_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        ticker = str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()
        shares = _num(r.get("已發行普通股數或TDR原股發行股數") or r.get("已發行普通股數"))
        if ticker and shares:
            out.append({"ticker": ticker, "shares_thousands": shares / 1000.0,
                        "published": str(r.get("出表日期") or r.get("Date") or "").strip()})
    return out


def fetch_shares() -> list[dict]:
    parsed = []
    for key in ("shares_listed", "shares_otc"):
        try:
            rows = get_json(URLS[key])
        except Exception as e:  # the OTC basic-info feed is less stable; monthly/quarterly matter more
            parsed.append({"_error": f"{key}: {e}"})
            continue
        for p in parse_shares_rows(rows):
            p["source"] = URLS[key]
            parsed.append(p)
    return parsed


def current_quarter_period(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.year}Q{(now.month - 1) // 3 + 1}"


def apply_shares(conn, parsed: list[dict], tickers: set[str] | None = None) -> dict:
    wanted = tickers if tickers is not None else {c["ticker"] for c in core.tw_companies(conn)}
    period = current_quarter_period()
    written, errors = [], [p["_error"] for p in parsed if "_error" in p]
    for row in parsed:
        if "_error" in row or row["ticker"] not in wanted:
            continue
        cid = f"TW:{row['ticker']}"
        core.set_metric(conn, cid, "shares", period, "actual", round(row["shares_thousands"], 3), row.get("source"),
                        created_by=CREATED_BY)
        written.append({"company_id": cid, "period": period, "shares_thousands": row["shares_thousands"]})
    return {"written": written, "errors": errors}
