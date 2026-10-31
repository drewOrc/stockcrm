"""One-off: move the CRM's personal layer from the old single-file database into the shared market.db.

    python scripts/migrate_to_shared.py --old data/stockcrm.db --new ../data/market.db [--dry-run]

Expects the engine to have created market.db with users and knowledge_* already imported from the old file
(it reads companies / edges / company_briefs / financials_ytd / metrics(actual) itself). This script:
  1. checks the shared copy holds at least the old knowledge rows and refuses if any are missing,
  2. copies positions, drivers, driver_values, checkpoints, interactions, reminders, proposals, tier_rules
     into personal_* (ids preserved) and metrics(kind=model) into personal_model_values,
  3. prints what it did. The old file is left untouched; keep it as a backup.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stockcrm import db  # noqa: E402

KNOWLEDGE_CHECKS = [
    ("companies", "knowledge_companies", None),
    ("edges", "knowledge_edges", None),
    ("company_briefs", "knowledge_company_briefs", None),
    ("financials_ytd", "knowledge_financials_ytd", None),
    ("metrics", "knowledge_metrics", "kind='actual'"),
]

PERSONAL_COPIES = [
    # (old table, new table, columns)
    ("positions", "personal_positions",
     "id, user_id, company_id, stage, tier, thesis, invalidation, thesis_status, target_price, target_basis, "
     "stop_price, position_pct, shares, avg_cost, idea_source, model_url, tags, created_at, updated_at"),
    ("drivers", "personal_drivers",
     "id, position_id, name, unit, critical, higher_is_better, sensitivity, notes, created_by, created_at"),
    ("driver_values", "personal_driver_values", "id, driver_id, period, kind, value, source, created_by, created_at"),
    ("interactions", "personal_interactions",
     "id, position_id, ts, kind, content, price, behavior_tags, source_url, driver_id, checkpoint_id, effect, "
     "created_by, created_at"),
    ("checkpoints", "personal_checkpoints",
     "id, position_id, driver_id, title, expected_by, evidence_needed, status, evidence, interaction_id, decided_at, "
     "created_by, created_at"),
    ("reminders", "personal_reminders", "id, user_id, position_id, due_date, kind, content, done, created_by, created_at"),
    ("proposals", "personal_proposals",
     "id, user_id, scope, approver_user_id, kind, payload, rationale, created_by, status, created_at, decided_at, applied_ref"),
]


def count(conn, table, where=None):
    return conn.execute(f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")).fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", default=str(ROOT / "data" / "stockcrm.db"))
    ap.add_argument("--new", default=str(db.DEFAULT_SHARED_DB))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    old = sqlite3.connect(a.old)
    old.row_factory = sqlite3.Row
    new = db.connect(a.new)                     # creates personal_* if missing; refuses without engine tables
    new.execute("PRAGMA foreign_keys = OFF")    # ids are copied verbatim; order of tables handles references anyway

    print(f"old: {a.old}\nnew: {a.new}")
    bad = False
    for o, n, where in KNOWLEDGE_CHECKS:
        co, cn = count(old, o, where), count(new, n)
        # the engine keeps fetching after its import, so the shared copy may already hold MORE rows
        flag = "ok" if cn == co else ("ok (engine added rows since import)" if cn > co else "MISSING ROWS")
        bad |= cn < co
        print(f"  knowledge {o:16s} old={co:5d} new={cn:5d} {flag}")
    if bad:
        print("knowledge counts differ: ask the engine to re-run its import before migrating the personal layer")
        return 1
    for _, n, _ in PERSONAL_COPIES:
        if count(new, n):
            print(f"  {n} already has rows; refusing to double-copy")
            return 1

    done = {}
    for o, n, cols in PERSONAL_COPIES:
        rows = old.execute(f"SELECT {cols} FROM {o}").fetchall()
        if not a.dry_run and rows:
            new.executemany(f"INSERT INTO {n} ({cols}) VALUES ({','.join('?' * len(cols.split(',')))})",
                            [tuple(r) for r in rows])
        done[n] = len(rows)
    models = old.execute("SELECT 1 AS user_id, company_id, metric, period, value, source, created_by, created_at "
                         "FROM metrics WHERE kind='model'").fetchall()
    if not a.dry_run and models:
        new.executemany("INSERT OR IGNORE INTO personal_model_values (user_id, company_id, metric, period, value, source, "
                        "created_by, created_at) VALUES (?,?,?,?,?,?,?,?)", [tuple(r) for r in models])
    done["personal_model_values"] = len(models)
    rules = old.execute("SELECT user_id, tier, max_pct, label, description FROM tier_rules").fetchall()
    if not a.dry_run:
        new.executemany("INSERT OR REPLACE INTO personal_tier_rules (user_id, tier, max_pct, label, description) "
                        "VALUES (?,?,?,?,?)", [tuple(r) for r in rules])
    done["personal_tier_rules"] = len(rules)
    notes = old.execute("SELECT id, notes FROM companies WHERE notes IS NOT NULL AND notes<>''").fetchall()
    if not a.dry_run and notes:
        new.executemany("INSERT OR REPLACE INTO personal_company_notes (user_id, company_id, notes) VALUES (1,?,?)",
                        [(r["id"], r["notes"]) for r in notes])
    done["personal_company_notes"] = len(notes)
    logs = old.execute("SELECT ts, job, status, summary, details FROM maintenance_log").fetchall()
    if not a.dry_run and logs:
        new.executemany("INSERT INTO crm_maintenance_log (ts, job, status, summary, details) VALUES (?,?,?,?,?)",
                        [tuple(r) for r in logs])
    done["crm_maintenance_log"] = len(logs)
    if not a.dry_run:
        new.commit()
        new.execute("PRAGMA foreign_keys = ON")
        fk = new.execute("PRAGMA foreign_key_check").fetchall()
        print("  foreign_key_check:", "ok" if not fk else fk[:5])
    for k, v in done.items():
        print(f"  {'would copy' if a.dry_run else 'copied'} {v:4d} -> {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
