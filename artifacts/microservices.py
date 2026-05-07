"""
Line Scheduling Microservices — Step 4
=======================================
Five REST endpoints that serve artifact data to the WatsonX agent tools.
Each endpoint reads from the JSON artifact written by scheduler.py.

Requirements:
    pip install flask langfuse

Run:
    python microservices.py

Endpoints (all GET, all require ?run_id=RUN-001&date=2026-04-12):
    /get_iis_report
    /get_availability_snapshot
    /get_constraint_slack
    /get_risk_score
    /get_allocation_trace

Test quickly with:
    curl "http://localhost:5000/get_iis_report?run_id=RUN-001&date=2026-04-12"
"""

import os
import sys
import json
import time
import uuid
from flask import Flask, request, jsonify, render_template
from knowledge_base import search_knowledge, get_collection
import kb_manager

# ── Langfuse observability (optional — degrades gracefully if unavailable) ──
_langfuse_enabled = False
try:
    # Allow running microservices.py from the artifacts/ directory while
    # the observability package lives one level up at the project root.
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    from observability.langfuse_client import (
        start_trace,
        make_trace_id,
        log_intent_classification,
        log_tool_call,
        log_knowledge_retrieval,
        log_synthesis,
        log_endpoint_call,
        update_trace_output,
        flush,
    )
    _langfuse_enabled = True
    print("[observability] Langfuse tracing enabled.")
except Exception as _lf_err:
    print(f"[observability] Langfuse not available — tracing disabled. ({_lf_err})")

app = Flask(__name__)

ARTIFACTS_DIR = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────────────────────────
# SHARED UTILITY
# ─────────────────────────────────────────────────────────────────

def load_artifact(run_id, date):
    """
    Loads the artifact JSON for a given run_id and date.
    Returns (artifact_dict, error_response) — one will always be None.
    """
    if not run_id or not date:
        return None, (
            jsonify({
                "error": "missing_parameters",
                "message": "Both run_id and date are required.",
                "example": "?run_id=RUN-001&date=2026-04-12"
            }),
            400,
        )

    filename = os.path.join(ARTIFACTS_DIR, f"{run_id}_{date}.json")

    if not os.path.exists(filename):
        return None, (
            jsonify({
                "error": "artifact_not_found",
                "message": f"No artifact found for run_id={run_id}, date={date}.",
                "looked_for": filename,
            }),
            404,
        )

    with open(filename) as f:
        artifact = json.load(f)

    return artifact, None


# ─────────────────────────────────────────────────────────────────
# TOOL 1 — get_iis_report
# ─────────────────────────────────────────────────────────────────

@app.route("/get_iis_report", methods=["GET"])
def get_iis_report():
    """
    Returns the conflict set (CP equivalent of IIS) for a run.
    Only populated when solve_status = infeasible or no_solution.
    Always safe to call — returns is_infeasible: false when model solved.
    """
    run_id = request.args.get("run_id")
    date   = request.args.get("date")

    artifact, err = load_artifact(run_id, date)
    if err:
        return err

    is_infeasible = artifact["solve_status"] in ("infeasible", "no_solution")

    result = {
        "run_id":          run_id,
        "date":            date,
        "solve_status":    artifact["solve_status"],
        "is_infeasible":   is_infeasible,
        "objective_value": artifact.get("objective_value"),
        "conflict_set":    artifact.get("conflict_set", []),
        "explanation_hint": (
            "The model is infeasible. The conflict_set lists the constraints "
            "that together make the problem impossible to solve."
            if is_infeasible else
            f"The model solved successfully with makespan "
            f"{artifact.get('objective_value')} time units. "
            f"No infeasibility conflict exists."
        ),
    }

    if _langfuse_enabled:
        log_endpoint_call("get_iis_report", {"run_id": run_id, "date": date}, result)

    return jsonify(result)


# ─────────────────────────────────────────────────────────────────
# TOOL 2 — get_availability_snapshot
# ─────────────────────────────────────────────────────────────────

@app.route("/get_availability_snapshot", methods=["GET"])
def get_availability_snapshot():
    """
    Returns the exact machines and workers that were available
    when the solver ran. resource_type filters the response.
    """
    run_id        = request.args.get("run_id")
    date          = request.args.get("date")
    resource_type = request.args.get("resource_type", "both")  # people | tools | both

    artifact, err = load_artifact(run_id, date)
    if err:
        return err

    snapshot = artifact.get("availability_snapshot", {})
    result   = {"run_id": run_id, "date": date, "resource_type": resource_type}

    if resource_type in ("people", "both"):
        result["workers"] = snapshot.get("workers", [])

    if resource_type in ("tools", "both"):
        result["machines"] = snapshot.get("machines", [])

    # Surface any unavailable machines as a top-level flag
    unavailable_machines = [
        m for m in snapshot.get("machines", [])
        if not m["available"]
    ]
    if unavailable_machines:
        result["unavailable_machines"] = unavailable_machines
        result["warning"] = (
            f"{len(unavailable_machines)} machine(s) were unavailable on this date. "
            f"See unavailable_machines for details."
        )

    # Surface any workers with low hours remaining
    low_hours_workers = [
        w for w in snapshot.get("workers", [])
        if w["hours_remaining"] <= 2
    ]
    if low_hours_workers:
        result["low_hours_workers"] = low_hours_workers
        result["hours_warning"] = (
            f"{len(low_hours_workers)} worker(s) had 2 or fewer hours remaining. "
            f"See low_hours_workers for details."
        )

    if _langfuse_enabled:
        log_endpoint_call(
            "get_availability_snapshot",
            {"run_id": run_id, "date": date, "resource_type": resource_type},
            result,
        )

    return jsonify(result)


# ─────────────────────────────────────────────────────────────────
# TOOL 3 — get_constraint_slack
# ─────────────────────────────────────────────────────────────────

@app.route("/get_constraint_slack", methods=["GET"])
def get_constraint_slack():
    """
    Returns constraint tightness analysis.
    If constraint_id is supplied, returns that constraint only.
    If omitted, returns all constraints for the run.
    """
    run_id        = request.args.get("run_id")
    date          = request.args.get("date")
    constraint_id = request.args.get("constraint_id")  # optional

    artifact, err = load_artifact(run_id, date)
    if err:
        return err

    all_constraints = artifact.get("constraint_analysis", [])

    if constraint_id:
        matched = [c for c in all_constraints if c["constraint_id"] == constraint_id]
        if not matched:
            return jsonify({
                "error":         "constraint_not_found",
                "constraint_id": constraint_id,
                "message":       f"No constraint with id '{constraint_id}' found in this run.",
                "available_ids": list({c["constraint_id"] for c in all_constraints}),
            }), 404
        constraints = matched
    else:
        constraints = all_constraints

    # Surface the tightest constraints as a convenience field
    tight = [c for c in constraints if c.get("is_tight")]

    result = {
        "run_id":               run_id,
        "date":                 date,
        "constraint_id_filter": constraint_id or "all",
        "constraints":          constraints,
        "tight_constraints":    tight,
        "tight_count":          len(tight),
        "summary": (
            f"{len(tight)} of {len(constraints)} constraints are tight (gap = 0 "
            f"or resource fully binding)."
        ),
    }

    if _langfuse_enabled:
        log_endpoint_call(
            "get_constraint_slack",
            {"run_id": run_id, "date": date, "constraint_id": constraint_id},
            result,
        )

    return jsonify(result)


# ─────────────────────────────────────────────────────────────────
# TOOL 4 — get_risk_score
# ─────────────────────────────────────────────────────────────────

@app.route("/get_risk_score", methods=["GET"])
def get_risk_score():
    """
    Returns computed risk scores for job assignments.
    If task_id is supplied, returns risk for that job only.
    If omitted, returns risk for all jobs.
    person_id filter is optional — narrows to jobs assigned to that worker.
    """
    run_id    = request.args.get("run_id")
    date      = request.args.get("date")
    task_id   = request.args.get("task_id")    # optional — maps to job_id
    person_id = request.args.get("person_id")  # optional — maps to worker_id

    artifact, err = load_artifact(run_id, date)
    if err:
        return err

    all_risks = artifact.get("risk_analysis", [])

    # Apply filters
    risks = all_risks
    if task_id:
        risks = [r for r in risks if r["job_id"] == task_id]
        if not risks:
            return jsonify({
                "error":   "task_not_found",
                "task_id": task_id,
                "message": f"No risk entry found for task '{task_id}'.",
                "available_task_ids": [r["job_id"] for r in all_risks],
            }), 404

    if person_id:
        risks = [r for r in risks if r.get("assigned_worker") == person_id]

    # Compute fleet-level summary
    level_counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for r in risks:
        level_counts[r["risk_level"]] = level_counts.get(r["risk_level"], 0) + 1

    result = {
        "run_id":      run_id,
        "date":        date,
        "task_filter": task_id or "all",
        "risks":       risks,
        "summary": {
            "total_jobs_assessed": len(risks),
            "by_risk_level":       level_counts,
            "highest_risk_jobs": [
                r["job_id"] for r in risks
                if r["risk_level"] in ("high", "critical")
            ],
        },
    }

    if _langfuse_enabled:
        log_endpoint_call(
            "get_risk_score",
            {"run_id": run_id, "date": date, "task_id": task_id, "person_id": person_id},
            result,
        )

    return jsonify(result)


# ─────────────────────────────────────────────────────────────────
# TOOL 5 — get_allocation_trace
# ─────────────────────────────────────────────────────────────────

@app.route("/get_allocation_trace", methods=["GET"])
def get_allocation_trace():
    """
    Returns the full allocation decision trace.
    If task_id is supplied, returns the trace for that job only,
    including which machines were considered and which was selected.
    """
    run_id  = request.args.get("run_id")
    date    = request.args.get("date")
    task_id = request.args.get("task_id")  # optional

    artifact, err = load_artifact(run_id, date)
    if err:
        return err

    all_traces = artifact.get("allocation_trace", [])

    if task_id:
        matched = [t for t in all_traces if t["job_id"] == task_id]
        if not matched:
            return jsonify({
                "error":   "task_not_found",
                "task_id": task_id,
                "message": f"No allocation trace found for task '{task_id}'. "
                           f"The task may not exist in this run.",
                "available_task_ids": [t["job_id"] for t in all_traces],
            }), 404
        traces = matched
    else:
        traces = all_traces

    unassigned = [t for t in traces if not t["is_present"]]

    result = {
        "run_id":          run_id,
        "date":            date,
        "task_filter":     task_id or "all",
        "traces":          traces,
        "unassigned_jobs": [t["job_id"] for t in unassigned],
        "summary": {
            "total_jobs":      len(traces),
            "assigned":        len(traces) - len(unassigned),
            "unassigned":      len(unassigned),
            "makespan":        artifact.get("objective_value"),
        },
    }

    if _langfuse_enabled:
        log_endpoint_call(
            "get_allocation_trace",
            {"run_id": run_id, "date": date, "task_id": task_id},
            result,
        )

    return jsonify(result)


# ─────────────────────────────────────────────────────────────────
# TOOL 6 — search_knowledge
# ─────────────────────────────────────────────────────────────────

@app.route("/search_knowledge", methods=["GET"])
def search_knowledge_endpoint():
    """
    Searches the ChromaDB knowledge base with a natural language query.
    Uses intent-aware threshold filtering by default.
    Pass n_results to override with a fixed count (bypasses threshold).
    Pass intent to select the appropriate retrieval config.
    """
    query     = request.args.get("query", "")
    intent    = request.args.get("intent", "default")
    n_results = request.args.get("n_results", None)

    if not query:
        return jsonify({"error": "missing_parameter", "message": "'query' is required."}), 400

    if n_results is not None:
        n_results = int(n_results)

    from knowledge_base import search_knowledge as _search, INTENT_RETRIEVAL_CONFIG

    chunks = _search(query, intent=intent, n_results=n_results)

    config_used = (
        None if n_results is not None
        else INTENT_RETRIEVAL_CONFIG.get(intent, INTENT_RETRIEVAL_CONFIG["default"])
    )

    return jsonify({
        "query":           query,
        "intent":          intent,
        "chunks":          chunks,
        "chunks_returned": len(chunks),
        "config_used": {
            "max_candidates": (
                n_results if n_results is not None
                else config_used["max_candidates"]
            ),
            "threshold": (
                None if n_results is not None
                else config_used["threshold"]
            ),
            "mode": (
                "override" if n_results is not None
                else "intent_aware"
            ),
        },
    })


# ─────────────────────────────────────────────────────────────────
# TOOL 7 — trace_query  (deterministic pipeline with step trace)
# ─────────────────────────────────────────────────────────────────

def _classify_intent(user_message: str) -> dict:
    """Keyword-based intent classifier. Returns intent + extracted entities."""
    msg = user_message.lower()

    diagnostic_keywords   = ["why", "not assigned", "not scheduled", "constraint",
                              "violation", "cause", "reason", "explain", "decision",
                              "allocation", "assigned", "tight", "slack", "gap",
                              "mean", "what does", "what is"]
    risk_keywords         = ["risk", "risky", "safe", "dangerous", "fragile",
                              "could fail", "backup", "what if", "concern", "warning"]
    availability_keywords = ["available", "availability", "who can", "which machine",
                              "maintenance", "hours left", "qualified", "skill"]

    if any(kw in msg for kw in risk_keywords):
        intent = "risk_assessment"
    elif any(kw in msg for kw in diagnostic_keywords):
        intent = "diagnostic"
    elif any(kw in msg for kw in availability_keywords):
        intent = "availability"
    else:
        intent = "general"

    # Extract job/machine/worker entity mentions
    import re
    entities = {
        "jobs":     re.findall(r'\bJ\d+\b', user_message, re.IGNORECASE),
        "machines": re.findall(r'\bM\d+\b', user_message, re.IGNORECASE),
        "workers":  re.findall(r'\bW\d+\b', user_message, re.IGNORECASE),
    }
    return {"intent": intent, "entities": entities}


def _run_tool_chain(intent: str, artifact: dict) -> dict:
    """Runs the deterministic tool chain for the given intent. Returns results + tool names."""
    tools_called = []
    results      = {}

    if intent == "diagnostic":
        tools_called = ["get_iis_report", "get_constraint_slack", "get_allocation_trace"]
        is_infeasible = artifact["solve_status"] in ("infeasible", "no_solution")
        results["iis_report"] = {
            "solve_status":    artifact["solve_status"],
            "is_infeasible":   is_infeasible,
            "objective_value": artifact.get("objective_value"),
            "conflict_set":    artifact.get("conflict_set", []),
        }
        all_constraints = artifact.get("constraint_analysis", [])
        tight = [c for c in all_constraints if c.get("is_tight")]
        results["constraint_slack"] = {
            "constraints":       all_constraints,
            "tight_constraints": tight,
            "tight_count":       len(tight),
        }
        results["allocation_trace"] = {
            "traces":   artifact.get("allocation_trace", []),
            "makespan": artifact.get("objective_value"),
        }

    elif intent == "risk_assessment":
        tools_called = ["get_risk_score", "get_constraint_slack", "get_availability_snapshot"]
        all_risks = artifact.get("risk_analysis", [])
        level_counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for r in all_risks:
            level_counts[r["risk_level"]] = level_counts.get(r["risk_level"], 0) + 1
        results["risk_score"] = {
            "risks":   all_risks,
            "summary": {"by_risk_level": level_counts,
                        "highest_risk_jobs": [r["job_id"] for r in all_risks
                                              if r["risk_level"] in ("high", "critical")]},
        }
        all_constraints = artifact.get("constraint_analysis", [])
        tight = [c for c in all_constraints if c.get("is_tight")]
        results["constraint_slack"] = {
            "constraints":       all_constraints,
            "tight_constraints": tight,
            "tight_count":       len(tight),
        }
        snapshot = artifact.get("availability_snapshot", {})
        results["availability_snapshot"] = {
            "machines": snapshot.get("machines", []),
            "workers":  snapshot.get("workers", []),
        }

    elif intent == "availability":
        tools_called = ["get_availability_snapshot"]
        snapshot = artifact.get("availability_snapshot", {})
        results["availability_snapshot"] = {
            "machines": snapshot.get("machines", []),
            "workers":  snapshot.get("workers", []),
        }

    else:  # general
        tools_called = []
        results = {}

    return {"tools_called": tools_called, "results": results}


@app.route("/trace_query", methods=["GET"])
def trace_query():
    """
    Deterministic pipeline endpoint with full step trace.
    Runs: classify_intent → tool_chain → knowledge_retrieval →
          context_assembly → (synthesis ready)
    Returns trace + assembled context for the agent to synthesise.
    Also emits a structured Langfuse trace when observability is enabled.

    Query params:
        run_id       — solver run identifier
        date         — ISO date string
        user_message — the user's natural language question
        n_results    — number of knowledge chunks (default 3)
    """
    run_id       = request.args.get("run_id")
    date         = request.args.get("date")
    user_message = request.args.get("user_message", "")
    n_results    = request.args.get("n_results", None)
    if n_results is not None:
        n_results = int(n_results)

    artifact, err = load_artifact(run_id, date)
    if err:
        return err

    # ── Langfuse: open one trace for this entire pipeline run ─────
    lf_trace_id = make_trace_id() if _langfuse_enabled else str(uuid.uuid4())
    if _langfuse_enabled:
        start_trace(
            trace_id=lf_trace_id,
            run_id=run_id,
            date=date,
            user_message=user_message,
        )

    trace = []

    # ── STEP 1: classify_intent ───────────────────────────────────
    classification = _classify_intent(user_message)
    intent   = classification["intent"]
    entities = classification["entities"]
    trace.append({
        "step":   "classify_intent",
        "output": {
            "intent":   intent,
            "entities": entities,
            "date":     date,
        },
    })

    if _langfuse_enabled:
        log_intent_classification(
            trace_id=lf_trace_id,
            user_message=user_message,
            classified_intent=intent,
            entities=entities,
            run_id=run_id,
            date=date,
        )

    # ── STEP 2: tool_chain ────────────────────────────────────────
    # Run each tool individually so we can capture per-tool latency.
    tools_called = []
    tool_results = {}

    if intent == "diagnostic":
        tools_called = ["get_iis_report", "get_constraint_slack", "get_allocation_trace"]

        # get_iis_report
        _t0 = time.time()
        is_infeasible = artifact["solve_status"] in ("infeasible", "no_solution")
        iis_result = {
            "solve_status":    artifact["solve_status"],
            "is_infeasible":   is_infeasible,
            "objective_value": artifact.get("objective_value"),
            "conflict_set":    artifact.get("conflict_set", []),
        }
        _latency = int((time.time() - _t0) * 1000)
        tool_results["iis_report"] = iis_result
        if _langfuse_enabled:
            log_tool_call(lf_trace_id, "get_iis_report",
                          {"run_id": run_id, "date": date}, iis_result, _latency)

        # get_constraint_slack
        _t0 = time.time()
        all_constraints = artifact.get("constraint_analysis", [])
        tight = [c for c in all_constraints if c.get("is_tight")]
        constraint_result = {
            "constraints":       all_constraints,
            "tight_constraints": tight,
            "tight_count":       len(tight),
        }
        _latency = int((time.time() - _t0) * 1000)
        tool_results["constraint_slack"] = constraint_result
        if _langfuse_enabled:
            log_tool_call(lf_trace_id, "get_constraint_slack",
                          {"run_id": run_id, "date": date}, constraint_result, _latency)

        # get_allocation_trace
        _t0 = time.time()
        allocation_result = {
            "traces":   artifact.get("allocation_trace", []),
            "makespan": artifact.get("objective_value"),
        }
        _latency = int((time.time() - _t0) * 1000)
        tool_results["allocation_trace"] = allocation_result
        if _langfuse_enabled:
            log_tool_call(lf_trace_id, "get_allocation_trace",
                          {"run_id": run_id, "date": date}, allocation_result, _latency)

    elif intent == "risk_assessment":
        tools_called = ["get_risk_score", "get_constraint_slack", "get_availability_snapshot"]

        # get_risk_score
        _t0 = time.time()
        all_risks = artifact.get("risk_analysis", [])
        level_counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for r in all_risks:
            level_counts[r["risk_level"]] = level_counts.get(r["risk_level"], 0) + 1
        risk_result = {
            "risks":   all_risks,
            "summary": {
                "by_risk_level":      level_counts,
                "highest_risk_jobs":  [r["job_id"] for r in all_risks
                                       if r["risk_level"] in ("high", "critical")],
            },
        }
        _latency = int((time.time() - _t0) * 1000)
        tool_results["risk_score"] = risk_result
        if _langfuse_enabled:
            log_tool_call(lf_trace_id, "get_risk_score",
                          {"run_id": run_id, "date": date}, risk_result, _latency)

        # get_constraint_slack
        _t0 = time.time()
        all_constraints = artifact.get("constraint_analysis", [])
        tight = [c for c in all_constraints if c.get("is_tight")]
        constraint_result = {
            "constraints":       all_constraints,
            "tight_constraints": tight,
            "tight_count":       len(tight),
        }
        _latency = int((time.time() - _t0) * 1000)
        tool_results["constraint_slack"] = constraint_result
        if _langfuse_enabled:
            log_tool_call(lf_trace_id, "get_constraint_slack",
                          {"run_id": run_id, "date": date}, constraint_result, _latency)

        # get_availability_snapshot
        _t0 = time.time()
        snapshot = artifact.get("availability_snapshot", {})
        availability_result = {
            "machines": snapshot.get("machines", []),
            "workers":  snapshot.get("workers", []),
        }
        _latency = int((time.time() - _t0) * 1000)
        tool_results["availability_snapshot"] = availability_result
        if _langfuse_enabled:
            log_tool_call(lf_trace_id, "get_availability_snapshot",
                          {"run_id": run_id, "date": date}, availability_result, _latency)

    elif intent == "availability":
        tools_called = ["get_availability_snapshot"]

        _t0 = time.time()
        snapshot = artifact.get("availability_snapshot", {})
        availability_result = {
            "machines": snapshot.get("machines", []),
            "workers":  snapshot.get("workers", []),
        }
        _latency = int((time.time() - _t0) * 1000)
        tool_results["availability_snapshot"] = availability_result
        if _langfuse_enabled:
            log_tool_call(lf_trace_id, "get_availability_snapshot",
                          {"run_id": run_id, "date": date}, availability_result, _latency)

    # general intent: no tool calls

    trace.append({
        "step":         "tool_chain",
        "tools_called": tools_called,
        "summary":      f"Retrieved data via: {', '.join(tools_called)}" if tools_called
                        else "No tools called (general intent — knowledge retrieval only)",
    })

    # ── STEP 3: knowledge_retrieval ───────────────────────────────
    _t0 = time.time()
    knowledge_chunks = search_knowledge(user_message, intent=intent)
    _latency = int((time.time() - _t0) * 1000)
    chunk_ids = [c["id"] for c in knowledge_chunks]
    trace.append({
        "step":        "knowledge_retrieval",
        "chunks_used": chunk_ids,
        "summary":     f"Retrieved {len(knowledge_chunks)} knowledge chunks: {chunk_ids}",
    })

    if _langfuse_enabled:
        log_knowledge_retrieval(
            trace_id=lf_trace_id,
            query=user_message,
            chunks_returned=knowledge_chunks,
        )

    # ── STEP 4: context_assembly ──────────────────────────────────
    formatted_chunks = "\n".join(
        f"[{c['id']}] {c['title']}: {c['content']}" for c in knowledge_chunks
    )
    assembled_context = (
        f"INTENT: {intent}\n"
        f"RUN: {run_id} | DATE: {date}\n"
        f"QUESTION: {user_message}\n\n"
        f"--- TOOL RESULTS ---\n"
        f"{json.dumps(tool_results, indent=2)}\n\n"
        f"--- KNOWLEDGE CHUNKS ---\n"
        f"{formatted_chunks}"
    )
    trace.append({
        "step":    "context_assembly",
        "summary": "Combined tool outputs and knowledge into structured context block",
    })

    # ── STEP 5: synthesis (ready) ─────────────────────────────────
    # The actual LLM call happens in WatsonX Orchestrate after this
    # endpoint returns. We log a placeholder generation span here so
    # the trace is complete. synthesis is always the LAST span emitted —
    # all tool calls and knowledge retrieval are already logged above.
    final_response = "[pending — LLM synthesis occurs in WatsonX Orchestrate]"
    trace.append({
        "step":   "synthesis",
        "status": "ready — context assembled, awaiting LLM synthesis",
    })

    if _langfuse_enabled:
        # FIX 3: synthesis span is emitted last, after all tool + retrieval spans
        log_synthesis(
            trace_id=lf_trace_id,
            assembled_context=assembled_context,
            final_response=final_response,
        )
        # FIX 2: set root trace output to the assembled context summary
        # so the trace is searchable by question and answer in Langfuse
        update_trace_output(
            trace_id=lf_trace_id,
            final_response=f"Context assembled for: {user_message}",
        )
        flush()

    # ── Format trace as readable string ──────────────────────────
    trace_lines = ["=== FLOW TRACE ==="]
    for i, step in enumerate(trace, 1):
        if step["step"] == "classify_intent":
            trace_lines.append(
                f"{i}. classify_intent → {step['output']['intent'].upper()}"
                f" (entities: {step['output']['entities']})"
            )
        elif step["step"] == "tool_chain":
            trace_lines.append(
                f"{i}. tool_chain → [{', '.join(step['tools_called'])}]"
                if step["tools_called"] else
                f"{i}. tool_chain → [none — general intent]"
            )
        elif step["step"] == "knowledge_retrieval":
            trace_lines.append(
                f"{i}. knowledge_retrieval → {step['chunks_used']}"
            )
        elif step["step"] == "context_assembly":
            trace_lines.append(f"{i}. context_assembly → complete")
        elif step["step"] == "synthesis":
            trace_lines.append(f"{i}. synthesis → ready")
    trace_lines.append("=== END TRACE ===")

    return jsonify({
        "run_id":            run_id,
        "date":              date,
        "user_message":      user_message,
        "trace":             trace,
        "trace_readable":    "\n".join(trace_lines),
        "assembled_context": assembled_context,
        "langfuse_trace_id": lf_trace_id if _langfuse_enabled else None,
    })


# ─────────────────────────────────────────────────────────────────
# KB WEB UI
# ─────────────────────────────────────────────────────────────────

@app.route("/kb")
def kb_ui():
    return render_template("kb_manager.html")


# ─────────────────────────────────────────────────────────────────
# KB API — GET /kb/documents
# ─────────────────────────────────────────────────────────────────

@app.route("/kb/documents", methods=["GET"])
def kb_list_documents():
    """Returns the current state of the knowledge base."""
    collection = get_collection()
    docs       = kb_manager.list_documents(collection)
    return jsonify({
        "total_documents":  len(docs),
        "total_chunks":     sum(d["chunk_count"] for d in docs),
        "concepts_enabled": kb_manager.concepts_are_enabled(collection),
        "documents":        docs,
    })


# ─────────────────────────────────────────────────────────────────
# KB API — POST /kb/upload
# ─────────────────────────────────────────────────────────────────

@app.route("/kb/upload", methods=["POST"])
def kb_upload():
    """Accepts a multipart file upload, parses and indexes it."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file     = request.files["file"]
    filename = file.filename
    if not filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "csv", "txt"):
        return jsonify({
            "error": f"Unsupported file type: {ext}. Supported: pdf, csv, txt"
        }), 400

    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        if ext == "pdf":
            chunks = kb_manager.parse_pdf(tmp_path)
        elif ext == "csv":
            chunks = kb_manager.parse_csv(tmp_path)
        else:
            chunks = kb_manager.parse_txt(tmp_path)

        collection = get_collection()
        ids = kb_manager.index_document(filename, chunks, ext, collection)

        return jsonify({
            "status":       "indexed",
            "filename":     filename,
            "chunks_added": len(ids),
            "chunk_ids":    ids,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────────────────────────
# KB API — DELETE /kb/document
# ─────────────────────────────────────────────────────────────────

@app.route("/kb/document", methods=["DELETE"])
def kb_delete_document():
    """Deletes all chunks for a given filename."""
    filename_stem = request.args.get("filename")
    if not filename_stem:
        return jsonify({"error": "filename query parameter required"}), 400

    collection    = get_collection()
    deleted_count = kb_manager.delete_document(filename_stem, collection)

    if deleted_count == 0:
        return jsonify({
            "error": f"No document found with filename '{filename_stem}'"
        }), 404

    return jsonify({
        "status":         "deleted",
        "filename":       filename_stem,
        "chunks_removed": deleted_count,
    })


# ─────────────────────────────────────────────────────────────────
# KB API — POST /kb/concepts/enable
# ─────────────────────────────────────────────────────────────────

@app.route("/kb/concepts/enable", methods=["POST"])
def kb_enable_concepts():
    """Re-indexes the 13 built-in concept documents."""
    collection    = get_collection()
    indexed_count = kb_manager.enable_concepts(collection)
    return jsonify({
        "status":         "enabled",
        "chunks_indexed": indexed_count,
    })


# ─────────────────────────────────────────────────────────────────
# KB API — POST /kb/concepts/disable
# ─────────────────────────────────────────────────────────────────

@app.route("/kb/concepts/disable", methods=["POST"])
def kb_disable_concepts():
    """Removes all concept documents from the collection."""
    collection    = get_collection()
    removed_count = kb_manager.disable_concepts(collection)
    return jsonify({
        "status":         "disabled",
        "chunks_removed": removed_count,
    })


# ─────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    artifacts = os.listdir(ARTIFACTS_DIR) if os.path.exists(ARTIFACTS_DIR) else []
    return jsonify({
        "status":            "ok",
        "artifacts_found":   artifacts,
        "endpoints": [
            "/get_iis_report",
            "/get_availability_snapshot",
            "/get_constraint_slack",
            "/get_risk_score",
            "/get_allocation_trace",
        ],
    })


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Line Scheduling Microservices")
    print("=" * 60)
    print("\nEndpoints available at http://localhost:5001\n")
    print("Quick test commands:")
    print('  curl "http://localhost:5001/health"')
    print('  curl "http://localhost:5001/get_iis_report?run_id=RUN-001&date=2026-04-12"')
    print('  curl "http://localhost:5001/get_availability_snapshot?run_id=RUN-001&date=2026-04-12&resource_type=both"')
    print('  curl "http://localhost:5001/get_constraint_slack?run_id=RUN-001&date=2026-04-12"')
    print('  curl "http://localhost:5001/get_risk_score?run_id=RUN-001&date=2026-04-12"')
    print('  curl "http://localhost:5001/get_allocation_trace?run_id=RUN-001&date=2026-04-12"')
    print("\n" + "=" * 60)

    app.run(debug=True, port=5001)