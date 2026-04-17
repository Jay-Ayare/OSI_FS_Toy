"""
Line Scheduling Microservices — Step 4
=======================================
Five REST endpoints that serve artifact data to the WatsonX agent tools.
Each endpoint reads from the JSON artifact written by scheduler.py.

Requirements:
    pip install flask

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
import json
from flask import Flask, request, jsonify
from knowledge_base import search_knowledge

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

    return jsonify({
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
    })


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

    return jsonify({
        "run_id":              run_id,
        "date":                date,
        "constraint_id_filter": constraint_id or "all",
        "constraints":         constraints,
        "tight_constraints":   tight,
        "tight_count":         len(tight),
        "summary": (
            f"{len(tight)} of {len(constraints)} constraints are tight (gap = 0 "
            f"or resource fully binding)."
        ),
    })


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

    return jsonify({
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
    })


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

    return jsonify({
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
    })


# ─────────────────────────────────────────────────────────────────
# TOOL 6 — search_knowledge
# ─────────────────────────────────────────────────────────────────

@app.route("/search_knowledge", methods=["GET"])
def search_knowledge_endpoint():
    """
    Searches the ChromaDB knowledge base with a natural language query.
    Returns the top matching constraint glossary, risk threshold,
    or business rule chunks.
    """
    query     = request.args.get("query")
    n_results = int(request.args.get("n_results", 3))

    if not query:
        return jsonify({"error": "missing_parameter", "message": "'query' is required."}), 400

    results = search_knowledge(query, n_results=n_results)
    return jsonify({"query": query, "n_results": n_results, "results": results})


# ─────────────────────────────────────────────────────────────────
# TOOL 7 — trace_query  (deterministic pipeline with step trace)
# ─────────────────────────────────────────────────────────────────

def _classify_intent(user_message: str) -> dict:
    """Keyword-based intent classifier. Returns intent + extracted entities."""
    msg = user_message.lower()

    diagnostic_keywords   = ["why", "not assigned", "not scheduled", "constraint",
                              "violation", "cause", "reason", "explain", "decision",
                              "allocation", "assigned"]
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

    Query params:
        run_id       — solver run identifier
        date         — ISO date string
        user_message — the user's natural language question
        n_results    — number of knowledge chunks (default 3)
    """
    run_id       = request.args.get("run_id")
    date         = request.args.get("date")
    user_message = request.args.get("user_message", "")
    n_results    = int(request.args.get("n_results", 3))

    artifact, err = load_artifact(run_id, date)
    if err:
        return err

    trace = []

    # ── STEP 1: classify_intent ───────────────────────────────────
    classification = _classify_intent(user_message)
    intent  = classification["intent"]
    entities = classification["entities"]
    trace.append({
        "step":   "classify_intent",
        "output": {
            "intent":   intent,
            "entities": entities,
            "date":     date,
        },
    })

    # ── STEP 2: tool_chain ────────────────────────────────────────
    tool_result = _run_tool_chain(intent, artifact)
    tools_called = tool_result["tools_called"]
    tool_results = tool_result["results"]
    trace.append({
        "step":         "tool_chain",
        "tools_called": tools_called,
        "summary":      f"Retrieved data via: {', '.join(tools_called)}" if tools_called
                        else "No tools called (general intent — knowledge retrieval only)",
    })

    # ── STEP 3: knowledge_retrieval ───────────────────────────────
    knowledge_chunks = search_knowledge(user_message, n_results=n_results)
    chunk_ids = [c["id"] for c in knowledge_chunks]
    trace.append({
        "step":        "knowledge_retrieval",
        "chunks_used": chunk_ids,
        "summary":     f"Retrieved {len(knowledge_chunks)} knowledge chunks: {chunk_ids}",
    })

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
    trace.append({
        "step":   "synthesis",
        "status": "ready — context assembled, awaiting LLM synthesis",
    })

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