"""REST API + static web UI. Run: python -m stockcrm.api  (http://127.0.0.1:8765)

The API is a thin wrapper over core.py; the MCP server wraps the same functions.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from stockcrm import core, db  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI(title="Stock CRM", version="0.1")


def conn():
    return db.connect()


def guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))


async def body(req: Request) -> dict[str, Any]:
    try:
        return await req.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")


@app.get("/api/enums")
def enums():
    return {"stages": db.STAGES, "tiers": db.TIERS, "thesis_statuses": db.THESIS_STATUSES,
            "interaction_kinds": db.INTERACTION_KINDS, "reminder_kinds": db.REMINDER_KINDS,
            "confidences": db.CONFIDENCES, "markets": db.MARKETS, "effects": db.EFFECTS,
            "metrics": db.METRICS, "metric_labels": db.METRIC_LABELS, "relations": db.RELATIONS}


@app.get("/api/tier_rules")
def tier_rules():
    with conn() as c:
        return core.export_graph(c)["tier_rules"]


# ---- positions
@app.get("/api/positions")
def positions(stage: str | None = None, tier: str | None = None):
    with conn() as c:
        return guard(core.list_positions, c, stage, tier)


@app.get("/api/positions/{ref}")
def position(ref: str):
    with conn() as c:
        p = guard(core.get_position, c, ref)
        if not p:
            raise HTTPException(404, "no card")
        return p


@app.post("/api/positions")
async def upsert_position(req: Request):
    b = await body(req)
    with conn() as c:
        if b.get("market") and b.get("ticker") and b.get("name"):
            comp = guard(core.upsert_company, c, b["market"], b["ticker"], b["name"], b.get("name_en"), b.get("sector"))
            b["company_id"] = comp["id"]
        p = guard(core.upsert_position, c, b["company_id"], b["stage"], b["tier"], b.get("thesis"),
                  b.get("invalidation"), b.get("target_price"), b.get("stop_price"), b.get("position_pct"),
                  b.get("idea_source"), b.get("tags"), b.get("thesis_status"), b.get("target_basis"),
                  b.get("model_url"))
        if b.get("template"):
            guard(core.apply_template, c, p["id"], b["template"])
            p = core.get_position(c, p["id"])
        return p


@app.post("/api/positions/{pid}/interactions")
async def add_interaction(pid: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.add_interaction, c, pid, b["kind"], b["content"], b.get("price"),
                     b.get("behavior_tags"), b.get("source_url"), "user", b.get("ts"),
                     b.get("driver_id"), b.get("checkpoint_id"), b.get("effect"))


# ---- drivers / checkpoints / metrics / health
@app.get("/api/positions/{pid}/health")
def health(pid: int):
    with conn() as c:
        return guard(core.thesis_health, c, pid)


@app.post("/api/positions/{pid}/drivers")
async def add_driver(pid: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.add_driver, c, pid, b["name"], b.get("unit"), b.get("critical", 1),
                     b.get("sensitivity"), b.get("notes"), b.get("higher_is_better", 1))


@app.patch("/api/drivers/{did}")
async def update_driver(did: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.update_driver, c, did, **b)


@app.delete("/api/drivers/{did}")
def delete_driver(did: int):
    with conn() as c:
        core.delete_driver(c, did)
        return {"ok": True}


@app.post("/api/drivers/{did}/values")
async def set_driver_value(did: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.set_driver_value, c, did, b["period"], b["kind"], b["value"], b.get("source"))


@app.get("/api/checkpoints")
def checkpoints(status: str | None = None):
    with conn() as c:
        return guard(core.list_checkpoints, c, None, status)


@app.post("/api/positions/{pid}/checkpoints")
async def add_checkpoint(pid: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.add_checkpoint, c, pid, b["title"], b["expected_by"], b.get("evidence_needed"),
                     b.get("driver_id"))


@app.post("/api/checkpoints/{cid}/resolve")
async def resolve_checkpoint(cid: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.resolve_checkpoint, c, cid, b["status"], b.get("evidence"))


@app.delete("/api/checkpoints/{cid}")
def delete_checkpoint(cid: int):
    with conn() as c:
        core.delete_checkpoint(c, cid)
        return {"ok": True}


@app.get("/api/metrics/{cid}")
def metrics(cid: str):
    with conn() as c:
        return core.metric_comparison(c, cid)


@app.post("/api/metrics/{cid}")
async def set_metric(cid: str, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.set_metric, c, cid, b["metric"], b["period"], b["kind"], b["value"], b.get("source"))


@app.get("/api/templates")
def templates():
    return core.list_templates()


@app.post("/api/positions/{pid}/template")
async def apply_template(pid: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.apply_template, c, pid, b["template"])


@app.post("/api/checks/apply")
def apply_checks():
    with conn() as c:
        return core.apply_health_flags(c)


# ---- maintenance
@app.get("/api/maintenance")
def maintenance():
    with conn() as c:
        return {"log": core.list_maintenance(c, 30), "quality": core.data_quality_report(c),
                "last": {j: core.last_run(c, j) for j in ("backup", "fetch_monthly", "fetch_quarterly", "fetch_shares",
                                                           "quality", "morning")}}


@app.post("/api/maintenance/run")
async def maintenance_run(req: Request):
    """Run one maintenance job now (the same code the scheduler runs)."""
    import sys as _sys
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts) not in _sys.path:
        _sys.path.insert(0, str(scripts))
    import maintain  # noqa: E402  (scripts/maintain.py)
    b = await body(req)
    job = b.get("job")
    if job not in maintain.JOBS:
        raise HTTPException(400, f"job must be one of {list(maintain.JOBS)}")
    key, fn = maintain.JOBS[job]
    with conn() as c:
        try:
            summary, details = fn(c)
        except Exception as e:
            core.log_maintenance(c, key, "error", f"{type(e).__name__}: {e}")
            raise HTTPException(502, f"{type(e).__name__}: {e}")
        core.log_maintenance(c, key, "ok", summary, details)
        return {"job": job, "summary": summary, "details": details}


@app.post("/api/positions/{pid}/thesis_status")
async def thesis_status(pid: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.set_thesis_status, c, pid, b["status"])


@app.get("/api/positions/{pid}/tier_check")
def tier_check(pid: int, new_pct: float):
    with conn() as c:
        return guard(core.check_tier_limit, c, pid, new_pct)


# ---- companies & graph
@app.get("/api/companies")
def companies(q: str = "", market: str | None = None):
    with conn() as c:
        return core.search_companies(c, q, market)


@app.post("/api/companies")
async def upsert_company(req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.upsert_company, c, b["market"], b["ticker"], b["name"], b.get("name_en"),
                     b.get("sector"), b.get("notes"))


@app.get("/api/graph")
def graph():
    with conn() as c:
        return core.export_graph(c)


@app.get("/api/graph.md", response_class=PlainTextResponse)
def graph_md():
    with conn() as c:
        return core.graph_markdown(c)


@app.post("/api/edges")
async def add_edge(req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.add_edge, c, b["customer_id"], b["supplier_id"], b.get("revenue_share"),
                     b.get("lag_days"), b.get("confidence", "medium"), b.get("source"), b.get("notes"),
                     "user", b.get("relation", "customer"))


@app.delete("/api/edges/{eid}")
def delete_edge(eid: int):
    with conn() as c:
        core.delete_edge(c, eid)
        return {"ok": True}


@app.get("/api/relations/{cid}")
def relations(cid: str):
    with conn() as c:
        return core.relations(c, cid)


@app.get("/api/exposure/{cid}")
def exposure(cid: str):
    with conn() as c:
        return core.indirect_exposure(c, cid)


@app.get("/api/overlap")
def overlap():
    with conn() as c:
        return core.portfolio_overlap(c)


@app.get("/api/morning")
def morning():
    with conn() as c:
        return core.morning_context(c)


# ---- proposals & reminders
@app.get("/api/proposals")
def proposals(status: str = "pending"):
    with conn() as c:
        return core.list_proposals(c, None if status == "all" else status)


@app.post("/api/proposals")
async def create_proposal(req: Request):
    """For other local services (e.g. the twstock-tracker engine) to propose changes instead of
    writing CRM tables directly. body: {kind, payload, rationale, created_by}. Same shape as list_proposals rows."""
    b = await body(req)
    for k in ("kind", "payload", "rationale", "created_by"):
        if k not in b or b[k] in (None, ""):
            raise HTTPException(400, f"missing {k}")
    if not isinstance(b["payload"], dict):
        raise HTTPException(400, "payload must be an object")
    with conn() as c:
        pr = guard(core.create_proposal, c, b["kind"], b["payload"], b["rationale"], str(b["created_by"]))
        pr["summary"] = core._proposal_summary(c, pr)
        return pr


@app.post("/api/proposals/{pid}/decide")
async def decide(pid: int, req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.decide_proposal, c, pid, bool(b.get("approve")))


@app.get("/api/reminders")
def reminders():
    with conn() as c:
        return core.all_reminders(c)


@app.post("/api/reminders")
async def add_reminder(req: Request):
    b = await body(req)
    with conn() as c:
        return guard(core.add_reminder, c, b["due_date"], b["kind"], b["content"], b.get("position_id"))


@app.post("/api/reminders/{rid}/done")
def reminder_done(rid: int):
    with conn() as c:
        core.complete_reminder(c, rid)
        return {"ok": True}


# ---- static
@app.get("/")
def index():
    # The page is tiny; never let the browser cache it so asset version bumps take effect.
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=WEB), name="static")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    uvicorn.run("stockcrm.api:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
