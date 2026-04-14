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