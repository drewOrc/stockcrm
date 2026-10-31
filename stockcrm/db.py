"""Database layout for the shared market database.

One SQLite file (``workspace/data/market.db``) is shared with the twstock-tracker engine. Ownership is by
table prefix, not by process:

  knowledge_*   generic facts (companies, supply-chain edges, briefs, actual financials)
                WRITTEN BY THE ENGINE (via its REST API); the CRM only reads them and proposes changes.
  personal_*    one user's cards, theses, evidence, reminders, proposals, model values  -> written by the CRM
  users         accounts (engine's login system); the CRM reads id/role and never touches password_hash
  schema_registry  who writes which table
  crm_maintenance_log  this service's job log

Enumerations (closed lists; agents must not invent new values):
  market:        TW | US
  stage:         watch | hold | exited
  tier:          core | satellite | watch | blacklist
  thesis_status: intact | shaken | broken
  interaction.kind:
                 buy | sell | add | trim | news | earnings | call | thought | review
                 | alert (automatic event from the price engine; always carries price)
  interaction.effect: supports | weakens | confirms
  reminder.kind: price | earnings | revenue | review | other
  proposal.kind: interaction | edge | thesis_status | reminder
                 | driver | driver_value | checkpoint | checkpoint_resolve | metric | brief
  proposal.scope: knowledge (an admin approves; applied through the engine) | personal (the owner approves)
  brief.kind:    profile | highlights
  edge.confidence: high | medium | low
  edge.relation: customer | partner | holding (constituent -> fund, revenue_share = weight)
  checkpoint.status: pending | confirmed | missed
  metric:        revenue_m | revenue_q | gross_margin | opex | eps | shares
  period formats: "2026" | "2026Q2" | "2026-05"
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED_DB = ROOT.parent / "data" / "market.db"          # workspace/data/market.db
DB_PATH = Path(os.environ.get("STOCKCRM_DB", DEFAULT_SHARED_DB))

MARKETS = ("TW", "US")
ROLES = ("admin", "member")
STAGES = ("watch", "hold", "exited")
TIERS = ("core", "satellite", "watch", "blacklist")
THESIS_STATUSES = ("intact", "shaken", "broken")
INTERACTION_KINDS = ("buy", "sell", "add", "trim", "news", "earnings", "call", "thought", "review", "alert")
EFFECTS = ("supports", "weakens", "confirms")
REMINDER_KINDS = ("price", "earnings", "revenue", "review", "other")
PROPOSAL_KINDS = ("interaction", "edge", "thesis_status", "reminder",
                  "driver", "driver_value", "checkpoint", "checkpoint_resolve", "metric", "brief")
PROPOSAL_SCOPES = ("knowledge", "personal")
KNOWLEDGE_PROPOSAL_KINDS = ("edge", "brief")   # metric proposals are knowledge when kind='actual'
PROPOSAL_STATUSES = ("pending", "approved", "rejected")
BRIEF_KINDS = ("profile", "highlights")
CONFIDENCES = ("high", "medium", "low")
RELATIONS = ("customer", "partner", "holding")
VALUE_KINDS = ("model", "actual")
CHECKPOINT_STATUSES = ("pending", "confirmed", "missed")
METRICS = ("revenue_m", "revenue_q", "gross_margin", "opex", "eps", "shares")
METRIC_LABELS = {"revenue_m": "月營收", "revenue_q": "季營收", "gross_margin": "毛利率",
                 "opex": "營業費用", "eps": "EPS", "shares": "股數"}

TIER_DEFAULTS = (("core", 15, "核心", "高信心、可重倉、長抱"),
                 ("satellite", 5, "衛星", "有論點但信心中等，部位有上限"),
                 ("watch", 0, "觀察", "尚未持有，等待條件成立"),
                 ("blacklist", 0, "黑名單", "踩過雷或已判定不碰，記下原因"))

# Tables this service owns (and registers in schema_registry).
PERSONAL_TABLES = ("personal_positions", "personal_drivers", "personal_driver_values", "personal_checkpoints",
                   "personal_interactions", "personal_reminders", "personal_proposals", "personal_tier_rules",
                   "personal_model_values", "personal_company_notes")
OPS_TABLES = ("crm_maintenance_log",)
# Tables the engine owns; the CRM needs them present.
KNOWLEDGE_TABLES = ("knowledge_companies", "knowledge_edges", "knowledge_edge_weights", "knowledge_company_briefs",
                    "knowledge_financials_ytd", "knowledge_metrics")

PERSONAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_registry (
  table_name TEXT PRIMARY KEY,
  layer      TEXT NOT NULL,            -- knowledge | personal | auth | ops
  owner      TEXT NOT NULL,            -- engine | crm
  writer     TEXT NOT NULL,
  note       TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One card per (user, company). This is the "account" in CRM terms.
CREATE TABLE IF NOT EXISTS personal_positions (
  id             INTEGER PRIMARY KEY,
  user_id        INTEGER NOT NULL REFERENCES users(id),
  company_id     TEXT NOT NULL REFERENCES knowledge_companies(id),
  stage          TEXT NOT NULL CHECK (stage IN ('watch','hold','exited')),
  tier           TEXT NOT NULL CHECK (tier IN ('core','satellite','watch','blacklist')),
  thesis         TEXT,
  invalidation   TEXT,
  thesis_status  TEXT NOT NULL DEFAULT 'intact' CHECK (thesis_status IN ('intact','shaken','broken')),
  target_price   REAL,
  target_basis   TEXT,
  stop_price     REAL,
  position_pct   REAL NOT NULL DEFAULT 0,
  shares         INTEGER,
  avg_cost       REAL,
  idea_source    TEXT,
  model_url      TEXT,
  tags           TEXT NOT NULL DEFAULT '[]',
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, company_id)
);

CREATE TABLE IF NOT EXISTS personal_drivers (
  id          INTEGER PRIMARY KEY,
  position_id INTEGER NOT NULL REFERENCES personal_positions(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  unit        TEXT,
  critical    INTEGER NOT NULL DEFAULT 1,
  higher_is_better INTEGER NOT NULL DEFAULT 1,
  sensitivity TEXT,
  notes       TEXT,
  created_by  TEXT NOT NULL DEFAULT 'user',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS personal_driver_values (
  id         INTEGER PRIMARY KEY,
  driver_id  INTEGER NOT NULL REFERENCES personal_drivers(id) ON DELETE CASCADE,
  period     TEXT NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('model','actual')),
  value      REAL NOT NULL,
  source     TEXT,
  created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (driver_id, period, kind)
);

CREATE TABLE IF NOT EXISTS personal_checkpoints (
  id              INTEGER PRIMARY KEY,
  position_id     INTEGER NOT NULL REFERENCES personal_positions(id) ON DELETE CASCADE,
  driver_id       INTEGER REFERENCES personal_drivers(id) ON DELETE SET NULL,
  title           TEXT NOT NULL,
  expected_by     TEXT NOT NULL,
  evidence_needed TEXT,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','missed')),
  evidence        TEXT,
  interaction_id  INTEGER REFERENCES personal_interactions(id) ON DELETE SET NULL,
  decided_at      TEXT,
  created_by      TEXT NOT NULL DEFAULT 'user',
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS personal_interactions (
  id            INTEGER PRIMARY KEY,
  position_id   INTEGER NOT NULL REFERENCES personal_positions(id) ON DELETE CASCADE,
  ts            TEXT NOT NULL DEFAULT (datetime('now')),
  kind          TEXT NOT NULL CHECK (kind IN ('buy','sell','add','trim','news','earnings','call','thought','review','alert')),
  content       TEXT NOT NULL,
  price         REAL,
  behavior_tags TEXT NOT NULL DEFAULT '[]',
  source_url    TEXT,
  driver_id     INTEGER REFERENCES personal_drivers(id) ON DELETE SET NULL,
  checkpoint_id INTEGER REFERENCES personal_checkpoints(id) ON DELETE SET NULL,
  effect        TEXT CHECK (effect IN ('supports','weakens','confirms')),
  created_by    TEXT NOT NULL DEFAULT 'user',
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS personal_reminders (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  position_id INTEGER REFERENCES personal_positions(id) ON DELETE CASCADE,
  due_date    TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('price','earnings','revenue','review','other')),
  content     TEXT NOT NULL,
  done        INTEGER NOT NULL DEFAULT 0,
  created_by  TEXT NOT NULL DEFAULT 'user',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Agents and services write here. scope=personal: the owner approves. scope=knowledge: an admin approves and
-- the change is applied through the engine's knowledge API.
CREATE TABLE IF NOT EXISTS personal_proposals (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  scope       TEXT NOT NULL DEFAULT 'personal' CHECK (scope IN ('knowledge','personal')),
  approver_user_id INTEGER REFERENCES users(id),
  kind        TEXT NOT NULL,
  payload     TEXT NOT NULL,
  rationale   TEXT NOT NULL,
  created_by  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  decided_at  TEXT,
  applied_ref TEXT
);

CREATE TABLE IF NOT EXISTS personal_tier_rules (
  user_id  INTEGER NOT NULL REFERENCES users(id),
  tier     TEXT NOT NULL CHECK (tier IN ('core','satellite','watch','blacklist')),
  max_pct  REAL NOT NULL,
  label    TEXT NOT NULL,
  description TEXT NOT NULL,
  PRIMARY KEY (user_id, tier)
);

-- The user's own model values for standard metrics (actuals live in knowledge_metrics).
CREATE TABLE IF NOT EXISTS personal_model_values (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  company_id TEXT NOT NULL REFERENCES knowledge_companies(id),
  metric     TEXT NOT NULL CHECK (metric IN ('revenue_m','revenue_q','gross_margin','opex','eps','shares')),
  period     TEXT NOT NULL,
  value      REAL NOT NULL,
  source     TEXT,
  created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, company_id, metric, period)
);

CREATE TABLE IF NOT EXISTS personal_company_notes (
  user_id    INTEGER NOT NULL REFERENCES users(id),
  company_id TEXT NOT NULL REFERENCES knowledge_companies(id),
  notes      TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, company_id)
);

CREATE TABLE IF NOT EXISTS crm_maintenance_log (
  id      INTEGER PRIMARY KEY,
  ts      TEXT NOT NULL DEFAULT (datetime('now')),
  job     TEXT NOT NULL,
  status  TEXT NOT NULL CHECK (status IN ('ok','error','skipped')),
  summary TEXT NOT NULL,
  details TEXT
);

CREATE INDEX IF NOT EXISTS idx_p_interactions_pos ON personal_interactions(position_id, ts);
CREATE INDEX IF NOT EXISTS idx_p_checkpoints_due ON personal_checkpoints(status, expected_by);
CREATE INDEX IF NOT EXISTS idx_p_model_values ON personal_model_values(company_id, metric, period);
"""

# The engine creates these. The CRM only creates them when bootstrapping a standalone/test database
# (STOCKCRM_BOOTSTRAP_KNOWLEDGE=1); the shapes follow the agreed contract so core.py reads the same SQL.
KNOWLEDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL,
  password_hash TEXT,
  role          TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
  line_user_id  TEXT,
  email         TEXT UNIQUE,
  disabled      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  last_login_at TEXT
);
INSERT OR IGNORE INTO users (id, username, display_name, role) VALUES (1, 'drew', 'Drew', 'admin');

CREATE TABLE IF NOT EXISTS knowledge_companies (
  id         TEXT PRIMARY KEY,
  market     TEXT NOT NULL CHECK (market IN ('TW','US')),
  ticker     TEXT NOT NULL,
  name       TEXT NOT NULL,
  name_en    TEXT,
  sector     TEXT,
  notes      TEXT,
  source     TEXT,
  confidence TEXT,
  created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge_edges (
  id            INTEGER PRIMARY KEY,
  customer_id   TEXT NOT NULL REFERENCES knowledge_companies(id),
  supplier_id   TEXT NOT NULL REFERENCES knowledge_companies(id),
  revenue_share REAL,
  lag_days      INTEGER,
  confidence    TEXT NOT NULL DEFAULT 'medium' CHECK (confidence IN ('high','medium','low')),
  relation      TEXT NOT NULL DEFAULT 'customer' CHECK (relation IN ('customer','partner','holding')),
  source        TEXT,
  notes         TEXT,
  created_by    TEXT NOT NULL DEFAULT 'user',
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (customer_id, supplier_id)
);

CREATE TABLE IF NOT EXISTS knowledge_edge_weights (
  id            INTEGER PRIMARY KEY,
  edge_id       INTEGER NOT NULL REFERENCES knowledge_edges(id) ON DELETE CASCADE,
  date          TEXT NOT NULL,
  revenue_share REAL NOT NULL,
  source        TEXT,
  created_by    TEXT NOT NULL DEFAULT 'user',
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (edge_id, date)
);

CREATE TABLE IF NOT EXISTS knowledge_company_briefs (
  id         INTEGER PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES knowledge_companies(id),
  date       TEXT NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('profile','highlights')),
  content    TEXT NOT NULL,
  sources    TEXT NOT NULL DEFAULT '[]',
  created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge_financials_ytd (
  id           INTEGER PRIMARY KEY,
  company_id   TEXT NOT NULL REFERENCES knowledge_companies(id),
  year         INTEGER NOT NULL,
  quarter      INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  revenue      REAL, gross_profit REAL, opex REAL, net_income REAL, eps REAL,
  source       TEXT,
  fetched_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (company_id, year, quarter)
);

-- Actual values only. Model values are personal (personal_model_values).
CREATE TABLE IF NOT EXISTS knowledge_metrics (
  id         INTEGER PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES knowledge_companies(id),
  metric     TEXT NOT NULL CHECK (metric IN ('revenue_m','revenue_q','gross_margin','opex','eps','shares')),
  period     TEXT NOT NULL,
  value      REAL NOT NULL,
  source     TEXT,
  created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (company_id, metric, period)
);
CREATE INDEX IF NOT EXISTS idx_k_metrics_company ON knowledge_metrics(company_id, metric, period);
CREATE INDEX IF NOT EXISTS idx_k_edges_customer ON knowledge_edges(customer_id);
CREATE INDEX IF NOT EXISTS idx_k_edges_supplier ON knowledge_edges(supplier_id);
"""


def bootstrap_enabled() -> bool:
    return os.environ.get("STOCKCRM_BOOTSTRAP_KNOWLEDGE", "0") == "1"


def _existing_tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _register(conn) -> None:
    rows = [(t, "personal", "crm", "stockcrm", "written by the CRM only") for t in PERSONAL_TABLES]
    rows += [(t, "ops", "crm", "stockcrm", "CRM job log") for t in OPS_TABLES]
    conn.executemany(
        "INSERT OR REPLACE INTO schema_registry (table_name, layer, owner, writer, note, updated_at) "
        "VALUES (?,?,?,?,?, datetime('now'))", rows)


def connect(path: Path | str | None = None, bootstrap: bool | None = None) -> sqlite3.Connection:
    """Open the shared database. The engine must have created users and knowledge_* first, unless
    bootstrapping a standalone/test database (bootstrap=True or STOCKCRM_BOOTSTRAP_KNOWLEDGE=1)."""
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    have = _existing_tables(conn)
    missing = [t for t in ("users", *KNOWLEDGE_TABLES) if t not in have]
    if missing:
        if bootstrap or (bootstrap is None and bootstrap_enabled()):
            conn.executescript(KNOWLEDGE_SCHEMA)
        else:
            conn.close()
            raise RuntimeError(
                f"{p} lacks engine-owned tables {missing}. Start the twstock-tracker engine first so it creates "
                "the shared database, or set STOCKCRM_BOOTSTRAP_KNOWLEDGE=1 for a standalone/test database.")
    conn.executescript(PERSONAL_SCHEMA)
    _register(conn)
    for tier, max_pct, label, desc in TIER_DEFAULTS:
        conn.execute("INSERT OR IGNORE INTO personal_tier_rules (user_id, tier, max_pct, label, description) "
                     "VALUES (1,?,?,?,?)", (tier, max_pct, label, desc))
    conn.commit()
    return conn
