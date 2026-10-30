"""SQLite schema and connection. The database file is the single source of truth.

Enumerations (closed lists; agents must not invent new values):
  market:        TW | US
  stage:         watch | hold | exited
  tier:          core | satellite | watch | blacklist
  thesis_status: intact | shaken | broken
  interaction.kind:
                 buy | sell | add | trim | news | earnings | call | thought | review
  reminder.kind: price | earnings | revenue | review | other
  proposal.kind: interaction | edge | thesis_status | reminder
  proposal.status: pending | approved | rejected
  edge.confidence: high | medium | low
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
REMINDER_KINDS = ("price", "earnings", "revenue", "review", "other")
PROPOSAL_KINDS = ("interaction", "edge", "thesis_status", "reminder")
PROPOSAL_STATUSES = ("pending", "approved", "rejected")
CONFIDENCES = ("high", "medium", "low")

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

-- Directed supply-chain relationship: customer buys from supplier.
CREATE TABLE IF NOT EXISTS edges (
  id            INTEGER PRIMARY KEY,
  customer_id   TEXT NOT NULL REFERENCES companies(id),
  supplier_id   TEXT NOT NULL REFERENCES companies(id),
  revenue_share REAL,                  -- share of SUPPLIER revenue that comes from CUSTOMER, 0..1, nullable if unknown
  lag_days      INTEGER,               -- typical delay from customer news to supplier revenue impact
  confidence    TEXT NOT NULL DEFAULT 'medium' CHECK (confidence IN ('high','medium','low')),
  source        TEXT,                  -- where this figure came from (annual report, call, broker note)
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
  stop_price     REAL,
  position_pct   REAL NOT NULL DEFAULT 0,   -- current % of total portfolio, 0..100
  idea_source    TEXT,                 -- where the idea came from (self, friend, youtuber name, news)
  tags           TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The CRM "contact log". Every trade, note, news item, review.
CREATE TABLE IF NOT EXISTS interactions (
  id            INTEGER PRIMARY KEY,
  position_id   INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  ts            TEXT NOT NULL DEFAULT (datetime('now')),   -- UTC ISO
  kind          TEXT NOT NULL CHECK (kind IN ('buy','sell','add','trim','news','earnings','call','thought','review')),
  content       TEXT NOT NULL,         -- markdown
  price         REAL,                  -- price at the time, optional
  behavior_tags TEXT NOT NULL DEFAULT '[]', -- JSON: e.g. ["chasing","averaging_down","revenge"]
  source_url    TEXT,
  created_by    TEXT NOT NULL DEFAULT 'user',
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reminders (
  id          INTEGER PRIMARY KEY,
  position_id INTEGER REFERENCES positions(id) ON DELETE CASCADE,
  due_date    TEXT NOT NULL,           -- YYYY-MM-DD
  kind        TEXT NOT NULL CHECK (kind IN ('price','earnings','revenue','review','other')),
  content     TEXT NOT NULL,
  done        INTEGER NOT NULL DEFAULT 0,
  created_by  TEXT NOT NULL DEFAULT 'user',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Agents write here. The user approves in the UI; approval applies the payload.
CREATE TABLE IF NOT EXISTS proposals (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('interaction','edge','thesis_status','reminder')),
  payload     TEXT NOT NULL,           -- JSON, shape depends on kind (see core.apply_proposal)
  rationale   TEXT NOT NULL,           -- why the agent proposes this, markdown
  created_by  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  decided_at  TEXT,
  applied_ref TEXT                     -- e.g. "interaction:12" once applied
);

-- Position-size discipline per tier. max_pct is % of total portfolio.
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
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn
