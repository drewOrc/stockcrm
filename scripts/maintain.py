"""Maintenance entry point. Run from anywhere:

    python scripts/maintain.py all            # backup -> fetch monthly/quarterly/shares -> quality report
    python scripts/maintain.py backup
    python scripts/maintain.py fetch-monthly
    python scripts/maintain.py fetch-quarterly
    python scripts/maintain.py fetch-shares
    python scripts/maintain.py quality
    python scripts/maintain.py health         # list positions whose thesis health suggests attention (no changes)
    python scripts/maintain.py reset          # DANGER: back up, then delete all data rows (keeps tier_rules and the log)

Every job logs one row in maintenance_log. `all` also writes data/reports/maintenance-YYYY-MM-DD.md.
Exit code 1 if any job errored, so the scheduler shows it.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import traceback
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stockcrm.env import load_dotenv  # noqa: E402

load_dotenv()
from stockcrm import core, db, mops  # noqa: E402

REPORTS = db.DB_PATH.parent / "reports"


def job_backup(conn):
    r = core.backup_db(conn)
    return f"備份到 {pathlib.Path(r['path']).name}，保留 {r['kept']} 份，刪除 {len(r['pruned'])} 份", r


def job_fetch_monthly(conn):
    r = mops.apply_monthly(conn, mops.fetch_monthly())
    n = len(r["written"])
    missing = f"，API 沒有：{'、'.join(r['missing'])}" if r["missing"] else ""
    return f"月營收寫入 {n} 家，最新期間 {r['latest_period']}{missing}", r


def job_fetch_quarterly(conn):
    r = mops.apply_quarterly(conn, mops.fetch_quarterly())
    return f"季報 YTD 存 {len(r['stored'])} 家，推出單季指標 {len(r['derived'])} 家，{len(r['skipped'])} 家缺前一季", r


def job_fetch_shares(conn):
    r = mops.apply_shares(conn, mops.fetch_shares())
    return f"股數寫入 {len(r['written'])} 家" + (f"，錯誤 {len(r['errors'])}" if r["errors"] else ""), r


def job_quality(conn):
    rep = core.data_quality_report(conn)
    return f"資料品質問題 {rep['total']} 項", rep


def job_reset(conn):
    """Irreversible: wipes all data rows (not tier_rules / maintenance_log). Backs up first."""
    b = core.backup_db(conn)
    counts = core.reset_data(conn)
    return f"已備份到 {pathlib.Path(b['path']).name}，清空 {sum(counts.values())} 列", {"backup": b, "deleted": counts}


def job_health(conn):
    """Thesis health for every user's cards (the personal layer is per user)."""
    out = []
    for u in core.list_users(conn):
        token = core.set_current_user(u["id"])
        try:
            for p in core.list_positions(conn):
                if p["stage"] == "exited":
                    continue
                h = core.thesis_health(conn, p["id"])
                if h["issues"]:
                    out.append({"user": u["username"], "company_id": p["company_id"], "name": p["name"],
                                "thesis_status": p["thesis_status"], "suggest_shaken": h["suggest_shaken"],
                                "issues": [i["message"] for i in h["issues"]]})
        finally:
            core.CURRENT_USER.reset(token)
    return f"{len(out)} 張卡片需要注意", out


JOBS = {
    "backup": ("backup", job_backup),
    "fetch-monthly": ("fetch_monthly", job_fetch_monthly),
    "fetch-quarterly": ("fetch_quarterly", job_fetch_quarterly),
    "fetch-shares": ("fetch_shares", job_fetch_shares),
    "quality": ("quality", job_quality),
    "health": ("health", job_health),
    "reset": ("reset", job_reset),      # not part of `all`
}
ALL = ["backup", "fetch-monthly", "fetch-quarterly", "fetch-shares", "quality", "health"]


def run(names: list[str], write_report: bool) -> int:
    conn = db.connect()
    results, failed = [], False
    for name in names:
        job_key, fn = JOBS[name]
        try:
            summary, details = fn(conn)
            core.log_maintenance(conn, job_key, "ok", summary, details)
            results.append((name, "ok", summary, details))
            print(f"[ok] {name}: {summary}")
        except Exception as e:  # keep going; the log and exit code carry the failure
            failed = True
            tb = traceback.format_exc(limit=3)
            core.log_maintenance(conn, job_key, "error", f"{type(e).__name__}: {e}", {"traceback": tb})
            results.append((name, "error", f"{type(e).__name__}: {e}", None))
            print(f"[error] {name}: {type(e).__name__}: {e}", file=sys.stderr)
    if write_report:
        REPORTS.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = REPORTS / f"maintenance-{day}.md"
        lines = [f"# 維護報告 {day}", ""]
        for name, status, summary, details in results:
            lines.append(f"- **{name}** [{status}] {summary}")
        for name, status, summary, details in results:
            if name == "quality" and details:
                lines += ["", core.quality_markdown(details)]
            if name == "health" and details:
                lines += ["", "# 論點健康度", ""]
                for h in details:
                    lines.append(f"## {h['name']}（{h['company_id']}）論點{h['thesis_status']}"
                                 + ("，建議標為動搖" if h["suggest_shaken"] else ""))
                    lines += [f"- {m}" for m in h["issues"]]
                    lines.append("")
            if name == "fetch-monthly" and details:
                lines += ["", "# 月營收", ""]
                for w in details["written"]:
                    yoy = f"{w['yoy_pct']:+.1f}%" if w["yoy_pct"] is not None else "?"
                    lines.append(f"- {w['company_id']} {w['period']}：{w['revenue']:,.0f} 千元，年增 {yoy}"
                                 + (f"，備註：{w['remark']}" if w["remark"] else ""))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"report: {path}")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", choices=list(JOBS) + ["all"])
    ap.add_argument("--no-report", action="store_true", help="do not write the markdown report (all only)")
    ap.add_argument("--json", action="store_true", help="print job details as JSON")
    a = ap.parse_args()
    names = ALL if a.job == "all" else [a.job]
    code = run(names, write_report=(a.job == "all" and not a.no_report))
    if a.json:
        conn = db.connect()
        print(json.dumps(core.list_maintenance(conn, len(names)), ensure_ascii=False, indent=1, default=str))
    sys.exit(code)


if __name__ == "__main__":
    main()
