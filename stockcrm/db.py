"""SQLite schema and connection. The database file is the single source of truth.

Enumerations (closed lists; agents must not invent new values):
  market:        TW | US
  stage:         watch | hold | exited
  tier:          core | satellite | watch | blacklist
  thesis_status: intact | shaken | broken
  interaction.kind:
                 buy | sell | add | trim | news | earnings | call | thought | review
  interaction.effect: supports | weakens | confirms   (how it bears on the linked driver/checkpoint)
  reminder.kind: price | earnings | revenue | review | other
  proposal.kind: interaction | edge | thesis_status | reminder
                 | driver | driver_value | checkpoint | checkpoint_resolve | metric
  proposal.status: pending | approved | rejected
  edge.confidence: high | medium | low
  edge.relation: customer (customer buys from supplier) | partner (capacity/licensing partner)
  value.kind:    model | actual
  checkpoint.status: pending | confirmed | missed
  metric:        revenue_m | revenue_q | gross_margin | opex | eps | shares
  period formats: "2026" (year) | "2026Q2" (quarter) | "2026-05" (month)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("STOCKCRM_DB", ROOT / "data" / "stockcrm.db"))

MARKETS = ("TW", "US")
STAGES = ("watch", "hold", "exited")
TIERS = ("core", "satellite", "watch", "blacklist")
THESIS_STATUSES = ("intact", "shaken", "broken")
INTERACTION_KINDS = ("buy", "sell", "add", "trim", "news", "earnings", "call", "thought", "review")
EFFECTS = ("supports", "weakens", "confirms")
REMINDER_KINDS = ("price", "earnings", "revenue", "review", "other")
PROPOSAL_KINDS = ("interaction", "edge", "thesis_status", "reminder",
                  "driver", "driver_value", "checkpoint", "checkpoint_resolve", "metric")
PROPOSAL_STATUSES = ("pending", "approved", "rejected")
CONFIDENCES = ("high", "medium", "low")
RELATIONS = ("customer", "partner")
VALUE_KINDS = ("model", "actual")
CHECKPOINT_STATUSES = ("pending", "confirmed", "missed")
METRICS = ("revenue_m", "revenue_q", "gross_margin", "opex", "eps", "shares")
METRIC_LABELS = {"revenue_m": "月營收", "revenue_q": "季營收", "gross_margin": "毛利率",
                 "opex": "營業費用", "eps": "EPS", "shares": "股數"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id        TEXT PRIMARY KEY,          -- "TW:2330", "US:NVDA"
  market    TEXT NOT NULL CHECK (market IN ('TW','US')),
  ticker    TEXT NOT NULL,
  name      TEXT NOT NULL,             -- 顯示名稱（中文優先）
  name_en   TEXT,
  sector    TEXT,
  notes     TEXT,                      -- markdown
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Directed relationship. relation='customer': customer buys from supplier.
-- relation='partner': capacity / licensing partner (customer_id = the partner, supplier_id = our company).
CREATE TABLE IF NOT EXISTS edges (
  id            INTEGER PRIMARY KEY,
  customer_id   TEXT NOT NULL REFERENCES companies(id),
  supplier_id   TEXT NOT NULL REFERENCES companies(id),
  revenue_share REAL,                  -- share of SUPPLIER revenue that comes from CUSTOMER, 0..1, nullable if unknown
  lag_days      INTEGER,               -- typical delay from customer news to supplier revenue impact
  confidence    TEXT NOT NULL DEFAULT 'medium' CHECK (confidence IN ('high','medium','low')),
  relation      TEXT NOT NULL DEFAULT 'customer' CHECK (relation IN ('customer','partner')),
  source        TEXT,
  notes         TEXT,
  created_by    TEXT NOT NULL DEFAULT 'user',
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (customer_id, supplier_id)
);

-- One card per company you track. This is the "account" in CRM terms.
CREATE TABLE IF NOT EXISTS positions (
  id             INTEGER PRIMARY KEY,
  company_id     TEXT NOT NULL UNIQUE REFERENCES companies(id),
  stage          TEXT NOT NULL CHECK (stage IN ('watch','hold','exited')),
  tier           TEXT NOT NULL CHECK (tier IN ('core','satellite','watch','blacklist')),
  thesis         TEXT,                 -- markdown: why own it
  invalidation   TEXT,                 -- markdown: what would prove the thesis wrong
  thesis_status  TEXT NOT NULL DEFAULT 'intact' CHECK (thesis_status IN ('intact','shaken','broken')),
  target_price   REAL,
  target_basis   TEXT,                 -- how target_price was derived, e.g. "PE 30 × 2027E EPS 48.56"
  stop_price     REAL,
  position_pct   REAL NOT NULL DEFAULT 0,   -- current % of total portfolio, 0..100
  idea_source    TEXT,
  model_url      TEXT,                 -- link/path to the valuation spreadsheet, if any
  tags           TEXT NOT NULL DEFAULT '[]',
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Key quantitative assumptions behind a thesis. Content is per company; the container is generic.
CREATE TABLE IF NOT EXISTS drivers (
  id          INTEGER PRIMARY KEY,
  position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,           -- "SEMI 營收占比", "自製設備出貨台數"
  unit        TEXT,                    -- "%", "台", "百萬美元"
  critical    INTEGER NOT NULL DEFAULT 1,   -- 1: weakening this driver shakes the thesis
  higher_is_better INTEGER NOT NULL DEFAULT 1, -- 0 for things like cost or churn
  sensitivity TEXT,                    -- free text, e.g. "每台約 0.25 EPS"
  notes       TEXT,
  created_by  TEXT NOT NULL DEFAULT 'user',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Model (what the thesis assumes) vs actual (what was observed) per period.
CREATE TABLE IF NOT EXISTS driver_values (
  id         INTEGER PRIMARY KEY,
  driver_id  INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
  period     TEXT NOT NULL,            -- "2026", "2026Q2", "2026-05"
  kind       TEXT NOT NULL CHECK (kind IN ('model','actual')),
  value      REAL NOT NULL,
  source     TEXT,
  created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (driver_id, period, kind)
);

-- "By <date> we should see <evidence>". Overdue pending checkpoints shake the thesis.
CREATE TABLE IF NOT EXISTS checkpoints (
  id              INTEGER PRIMARY KEY,
  position_id     INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  driver_id       INTEGER REFERENCES drivers(id) ON DELETE SET NULL,
  title           TEXT NOT NULL,
  expected_by     TEXT NOT NULL,       -- YYYY-MM-DD
  evidence_needed TEXT,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','missed')),
  evidence        TEXT,                -- filled when resolved
  interaction_id  INTEGER REFERENCES interactions(id) ON DELETE SET NULL,
  decided_at      TEXT,
  created_by      TEXT NOT NULL DEFAULT 'user',
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Standard financial metrics every listed company has. model vs actual per period.
CREATE TABLE IF NOT EXISTS metrics (
  id         INTEGER PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id),
  metric     TEXT NOT NULL CHECK (metric IN ('revenue_m','revenue_q','gross_margin','opex','eps','shares')),
  period     TEXT NOT NULL,            -- revenue_m: "2026-05"; others: "2026Q2" or "2026"
  kind       TEXT NOT NULL CHECK (kind IN ('model','actual')),
  value      REAL NOT NULL,            -- revenue/opex in thousands of local currency; margin as 0..1; shares in thousands
  source     TEXT,
  created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (company_id, metric, period, kind)
);

-- The CRM "contact log". Every trade, note, news item, review.
CREATE TABLE IF NOT EXISTS interactions (
  id            INTEGER PRIMARY KEY,
  position_id   INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  ts            TEXT NOT NULL DEFAULT (datetime('now')),   -- UTC ISO
  kind          TEXT NOT NULL CHECK (kind IN ('buy','sell','add','trim','news','earnings','call','thought','review')),
  content       TEXT NOT NULL,         -- markdown
  price         REAL,
  behavior_tags TEXT NOT NULL DEFAULT '[]',
  source_url    TEXT,
  driver_id     INTEGER REFERENCES drivers(id) ON DELETE SET NULL,
  checkpoint_id INTEGER REFERENCES checkpoints(id) ON DELETE SET NULL,
  effect        TEXT CHECK (effect IN ('supports','weakens','confirms')),
  created_by    TEXT NOT NULL DEFAULT 'user',
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reminders (
  id          INTEGER PRIMARY KEY,
  position_id INTEGER REFERENCES positions(id) ON DELETE CASCADE,
  due_date    TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('price','earnings','revenue','review','other')),
  content     TEXT NOT NULL,
  done        INTEGER NOT NULL DEFAULT 0,
  created_by  TEXT NOT NULL DEFAULT 'user',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Agents write here. The user approves in the UI; approval applies the payload.
CREATE TABLE IF NOT EXISTS proposals (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  payload     TEXT NOT NULL,
  rationale   TEXT NOT NULL,
  created_by  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  decided_at  TEXT,
  applied_ref TEXT
);

-- Raw year-to-date figures from the quarterly income statement (TWSE/TPEx open data publish YTD).
-- Single-quarter metrics are derived by differencing consecutive quarters. Thousands of TWD.
CREATE TABLE IF NOT EXISTS financials_ytd (
  id           INTEGER PRIMARY KEY,
  company_id   TEXT NOT NULL REFERENCES companies(id),
  year         INTEGER NOT NULL,
  quarter      INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  revenue      REAL,
  gross_profit REAL,
  opex         REAL,
  net_income   REAL,
  eps          REAL,
  source       TEXT,
  fetched_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (company_id, year, quarter)
);

-- Every automated job writes one row here, so "who touched the data and when" is always answerable.
CREATE TABLE IF NOT EXISTS maintenance_log (
  id      INTEGER PRIMARY KEY,
  ts      TEXT NOT NULL DEFAULT (datetime('now')),
  job     TEXT NOT NULL,                -- backup | fetch_monthly | fetch_quarterly | fetch_shares | quality | morning
  status  TEXT NOT NULL CHECK (status IN ('ok','error','skipped')),
  summary TEXT NOT NULL,
  details TEXT                          -- JSON
);

CREATE TABLE IF NOT EXISTS tier_rules (
  tier     TEXT PRIMARY KEY CHECK (tier IN ('core','satellite','watch','blacklist')),
  max_pct  REAL NOT NULL,
  label    TEXT NOT NULL,
  description TEXT NOT NULL
);
INSERT OR IGNORE INTO tier_rules VALUES
  ('core',      15, '核心', '高信心、可重倉、長抱'),
  ('satellite',  5, '衛星', '有論點但信心中等，部位有上限'),
  ('watch',      0, '觀察', '尚未持有，等待條件成立'),
  ('blacklist',  0, '黑名單', '踩過雷或已判定不碰，記下原因');

CREATE INDEX IF NOT EXISTS idx_interactions_pos ON interactions(position_id, ts);
CREATE INDEX IF NOT EXISTS idx_edges_customer ON edges(customer_id);
CREATE INDEX IF NOT EXISTS idx_edges_supplier ON edges(supplier_id);
CREATE INDEX IF NOT EXISTS idx_metrics_company ON metrics(company_id, metric, period);
CREATE INDEX IF NOT EXISTS idx_checkpoints_due ON checkpoints(status, expected_by);
"""

# Columns added after the first release. (table, column, DDL) — applied to existing databases.
_MIGRATIONS = [
    ("edges", "relation", "TEXT NOT NULL DEFAULT 'customer'"),
    ("positions", "target_basis", "TEXT"),
    ("positions", "model_url", "TEXT"),
    ("interactions", "driver_id", "INTEGER REFERENCES drivers(id) ON DELETE SET NULL"),
    ("interactions", "checkpoint_id", "INTEGER REFERENCES checkpoints(id) ON DELETE SET NULL"),
    ("interactions", "effect", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, ddl in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    # proposals.kind used to carry a CHECK constraint listing the first four kinds; rebuild if so.
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='proposals'").fetchone()
    if sql and "CHECK (kind IN" in sql[0]:
        conn.executescript("""
            ALTER TABLE proposals RENAME TO proposals_old;
            CREATE TABLE proposals (
              id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, rationale TEXT NOT NULL,
              created_by TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
              created_at TEXT NOT NULL DEFAULT (datetime('now')), decided_at TEXT, applied_ref TEXT);
            INSERT INTO proposals SELECT * FROM proposals_old;
            DROP TABLE proposals_old;
        """)
    conn.commit()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
