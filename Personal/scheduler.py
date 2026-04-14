"""
Line Scheduling Solver — Step 3
================================
Self-contained CP Optimizer model built on the Step 1 dataset.
Runs the solve, extracts structured artifacts, writes to JSON.

Requirements:
    pip install docplex

Run:
    python scheduler.py

Output:
    artifacts/RUN-001_2026-04-12.json
"""

import os
import json
from docplex.cp.model import CpoModel

# ─────────────────────────────────────────────────────────────────
# DATASET  (Step 1)
# ─────────────────────────────────────────────────────────────────

dataset = {
    "run_id": "RUN-001",
    "date": "2026-04-12",

    "machines": [
        {"id": "M1", "name": "Machine 1", "available": True,  "unavailability_reason": None},
        {"id": "M2", "name": "Machine 2", "available": True,  "unavailability_reason": None},
        {"id": "M3", "name": "Machine 3", "available": False, "unavailability_reason": "Scheduled maintenance"},
    ],

    "workers": [
        {"id": "W1", "name": "Worker 1", "skills": ["welding", "assembly"],  "max_hours": 8, "hours_already_worked": 6},
        {"id": "W2", "name": "Worker 2", "skills": ["painting", "assembly"], "max_hours": 8, "hours_already_worked": 2},
        {"id": "W3", "name": "Worker 3", "skills": ["welding", "painting"],  "max_hours": 8, "hours_already_worked": 0},
    ],

    "jobs": [
        {"id": "J1", "name": "Job 1", "required_skill": "welding",  "duration": {"M1": 30, "M2": 40}, "precedence": None},
        {"id": "J2", "name": "Job 2", "required_skill": "painting", "duration": {"M1": 20, "M2": 25}, "precedence": None},
        {"id": "J3", "name": "Job 3", "required_skill": "assembly", "duration": {"M1": 50, "M2": 60}, "precedence": "J1"},
        {"id": "J4", "name": "Job 4", "required_skill": "welding",  "duration": {"M1": 45, "M2": 35}, "precedence": None},
        {"id": "J5", "name": "Job 5", "required_skill": "painting", "duration": {"M1": 15, "M2": 20}, "precedence": "J2"},
    ],

    "constraints": [
        {"id": "C1", "type": "no_overlap",           "description": "No two jobs may run on the same machine at the same time"},
        {"id": "C2", "type": "precedence",           "description": "Job 3 cannot start until Job 1 has finished",             "jobs": ["J1", "J3"]},
        {"id": "C3", "type": "precedence",           "description": "Job 5 cannot start until Job 2 has finished",             "jobs": ["J2", "J5"]},
        {"id": "C4", "type": "machine_availability", "description": "Machine 3 is unavailable due to scheduled maintenance",   "machine": "M3"},
        {"id": "C5", "type": "worker_hours",         "description": "Worker 1 has only 2 hours remaining today",              "worker": "W1"},
    ],
}

# Only machines marked available are usable
AVAILABLE_MACHINES = [m["id"] for m in dataset["machines"] if m["available"]]

# Build a lookup: skill → workers who have it
SKILL_TO_WORKERS = {}
for w in dataset["workers"]:
    for s in w["skills"]:
        SKILL_TO_WORKERS.setdefault(s, []).append(w["id"])

# Build job id → job object lookup
JOB_BY_ID = {j["id"]: j for j in dataset["jobs"]}


# ─────────────────────────────────────────────────────────────────
# SOLVER
# ─────────────────────────────────────────────────────────────────

def build_and_solve(data):
    mdl = CpoModel()

    job_vars  = {}   # job_id  → master interval var (must be executed)
    job_alts  = {}   # job_id  → list of optional interval vars, one per available machine
    job_sizes = {}   # job_id  → {machine_id: duration}

    # ── Step 1: create interval variables ──────────────────────────
    for j in data["jobs"]:
        job_sizes[j["id"]] = j["duration"]

        # Master var — not optional, so the job MUST be scheduled
        master = mdl.interval_var(name=j["id"])
        job_vars[j["id"]] = master

        # One optional alternative per available machine
        alts = []
        for m_id in AVAILABLE_MACHINES:
            duration = j["duration"].get(m_id)
            if duration is None:
                continue
            alt = mdl.interval_var(
                name=f"{j['id']}_{m_id}",
                size=duration,
                optional=True,
            )
            alts.append((m_id, alt))
        job_alts[j["id"]] = alts  # list of (machine_id, var)

        # Exactly one alternative must be present
        mdl.add(mdl.alternative(master, [a for _, a in alts]))

    # ── Step 2: no-overlap on each machine (C1) ────────────────────
    for m_id in AVAILABLE_MACHINES:
        machine_alts = [
            alt
            for j in data["jobs"]
            for mid, alt in job_alts[j["id"]]
            if mid == m_id
        ]
        if machine_alts:
            mdl.add(mdl.no_overlap(machine_alts))

    # ── Step 3: precedence constraints (C2, C3) ────────────────────
    for j in data["jobs"]:
        if j["precedence"]:
            pred_id = j["precedence"]
            mdl.add(mdl.end_before_start(job_vars[pred_id], job_vars[j["id"]]))

    # ── Step 4: objective — minimise makespan ──────────────────────
    makespan = mdl.max(mdl.end_of(job_vars[j["id"]]) for j in data["jobs"])
    mdl.minimize(makespan)

    # ── Step 5: solve ──────────────────────────────────────────────
    import io
    sol = mdl.solve(log_output=io.StringIO())
    return mdl, sol, job_vars, job_alts


# ─────────────────────────────────────────────────────────────────
# ARTIFACT EXTRACTION
# ─────────────────────────────────────────────────────────────────

def extract_artifacts(mdl, sol, job_vars, job_alts, data):
    """
    Builds the full artifact document from the solve result.
    This is the data the five microservices will query.
    """

    run_id = data["run_id"]
    date   = data["date"]

    # ── Solve status ───────────────────────────────────────────────
    if sol is None:
        status = "no_solution"
    else:
        solve_status = sol.get_solve_status()
        status = "optimal"  if solve_status == "Optimal"  else \
                 "feasible" if solve_status == "Feasible" else \
                 "infeasible"

    objective = sol.get_objective_value() if sol and status != "infeasible" else None

    # ── Conflict set (IIS equivalent for CP) ──────────────────────
    # Only populated when the model cannot be solved.
    conflict_set = []
    if status in ("no_solution", "infeasible"):
        try:
            conflicts = mdl.refine_conflict()
            if conflicts:
                for c in conflicts.get_all_constraints():
                    conflict_set.append(str(c))
        except Exception:
            conflict_set = ["conflict refinement unavailable"]

    # ── Availability snapshot ──────────────────────────────────────
    availability_snapshot = {
        "machines": [
            {
                "id":                   m["id"],
                "name":                 m["name"],
                "available":            m["available"],
                "unavailability_reason": m["unavailability_reason"],
            }
            for m in data["machines"]
        ],
        "workers": [
            {
                "id":                  w["id"],
                "name":                w["name"],
                "skills":              w["skills"],
                "max_hours":           w["max_hours"],
                "hours_already_worked": w["hours_already_worked"],
                "hours_remaining":     w["max_hours"] - w["hours_already_worked"],
            }
            for w in data["workers"]
        ],
    }

    # ── Allocation trace ───────────────────────────────────────────
    allocation_trace = []
    if sol:
        for j in data["jobs"]:
            jid      = j["id"]
            assigned_machine  = None
            assigned_start    = None
            assigned_end      = None
            is_present        = False
            alternatives_considered = []

            for m_id, alt_var in job_alts[jid]:
                var_sol = sol.get_var_solution(alt_var)
                present = var_sol is not None and var_sol.is_present()
                alternatives_considered.append({
                    "machine": m_id,
                    "duration": j["duration"].get(m_id),
                    "selected": present,
                })
                if present:
                    assigned_machine = m_id
                    assigned_start   = var_sol.get_start()
                    assigned_end     = var_sol.get_end()
                    is_present       = True

            # Derive which workers qualify for this job
            required_skill   = j["required_skill"]
            qualified_workers = SKILL_TO_WORKERS.get(required_skill, [])

            # Assign the worker with the most hours remaining among
            # those qualified — simple heuristic for this dataset
            assigned_worker = None
            if qualified_workers and is_present:
                worker_objs = [
                    w for w in data["workers"] if w["id"] in qualified_workers
                ]
                worker_objs.sort(
                    key=lambda w: w["max_hours"] - w["hours_already_worked"],
                    reverse=True,
                )
                assigned_worker = worker_objs[0]["id"] if worker_objs else None

            allocation_trace.append({
                "job_id":                  jid,
                "job_name":                j["name"],
                "required_skill":          required_skill,
                "assigned_machine":        assigned_machine,
                "assigned_worker":         assigned_worker,
                "start":                   assigned_start,
                "end":                     assigned_end,
                "is_present":              is_present,
                "alternatives_considered": alternatives_considered,
            })

    # ── Constraint analysis (CP equivalent of slack) ───────────────
    constraint_analysis = []
    if sol:
        # C1 — no-overlap: report per-machine job ordering
        for m_id in AVAILABLE_MACHINES:
            jobs_on_machine = [
                t for t in allocation_trace
                if t["assigned_machine"] == m_id
            ]
            jobs_on_machine.sort(key=lambda t: t["start"] if t["start"] is not None else 0)
            gaps = []
            for i in range(len(jobs_on_machine) - 1):
                gap = jobs_on_machine[i + 1]["start"] - jobs_on_machine[i]["end"]
                gaps.append(gap)
            constraint_analysis.append({
                "constraint_id":  "C1",
                "machine":        m_id,
                "jobs_in_order":  [t["job_id"] for t in jobs_on_machine],
                "gaps_between_jobs": gaps,
                "is_tight":       any(g == 0 for g in gaps),
                "description":    "No two jobs may run on the same machine at the same time",
            })

        # C2 — J1 → J3 precedence
        j1_trace = next((t for t in allocation_trace if t["job_id"] == "J1"), None)
        j3_trace = next((t for t in allocation_trace if t["job_id"] == "J3"), None)
        if j1_trace and j3_trace and j1_trace["end"] is not None and j3_trace["start"] is not None:
            gap_c2 = j3_trace["start"] - j1_trace["end"]
            constraint_analysis.append({
                "constraint_id": "C2",
                "predecessor":   "J1",
                "successor":     "J3",
                "gap":           gap_c2,
                "is_tight":      gap_c2 == 0,
                "description":   "Job 3 cannot start until Job 1 has finished",
            })

        # C3 — J2 → J5 precedence
        j2_trace = next((t for t in allocation_trace if t["job_id"] == "J2"), None)
        j5_trace = next((t for t in allocation_trace if t["job_id"] == "J5"), None)
        if j2_trace and j5_trace and j2_trace["end"] is not None and j5_trace["start"] is not None:
            gap_c3 = j5_trace["start"] - j2_trace["end"]
            constraint_analysis.append({
                "constraint_id": "C3",
                "predecessor":   "J2",
                "successor":     "J5",
                "gap":           gap_c3,
                "is_tight":      gap_c3 == 0,
                "description":   "Job 5 cannot start until Job 2 has finished",
            })

        # C4 — M3 unavailable
        constraint_analysis.append({
            "constraint_id": "C4",
            "machine":       "M3",
            "is_tight":      True,   # always binding — M3 is always excluded
            "gap":           None,
            "description":   "Machine 3 is unavailable due to scheduled maintenance",
        })

        # C5 — Worker 1 hours
        w1 = next(w for w in data["workers"] if w["id"] == "W1")
        w1_hours_remaining = w1["max_hours"] - w1["hours_already_worked"]
        w1_jobs = [t for t in allocation_trace if t["assigned_worker"] == "W1"]
        w1_total_assigned = sum(
            (t["end"] - t["start"]) / 60
            for t in w1_jobs
            if t["end"] is not None and t["start"] is not None
        )
        constraint_analysis.append({
            "constraint_id":        "C5",
            "worker":               "W1",
            "hours_remaining":      w1_hours_remaining,
            "hours_assigned":       round(w1_total_assigned, 2),
            "is_tight":             w1_hours_remaining <= 2,
            "description":          "Worker 1 has only 2 hours remaining today",
        })

    # ── Risk analysis ──────────────────────────────────────────────
    risk_analysis = []
    if sol:
        for t in allocation_trace:
            jid            = t["job_id"]
            required_skill = t["required_skill"]
            assigned_worker = t["assigned_worker"]

            qualified_workers = SKILL_TO_WORKERS.get(required_skill, [])
            backup_count      = max(0, len(qualified_workers) - 1)

            contributing_factors = []

            # Factor: M3 unavailable reduces machine options
            original_machines = list(JOB_BY_ID[jid]["duration"].keys())
            available_count   = sum(1 for m in original_machines if m in AVAILABLE_MACHINES)
            if available_count < len(original_machines):
                contributing_factors.append(
                    f"Machine M3 unavailable — only {available_count} of "
                    f"{len(original_machines)} machines usable"
                )

            # Factor: assigned worker is nearly exhausted
            if assigned_worker:
                w_obj = next((w for w in data["workers"] if w["id"] == assigned_worker), None)
                if w_obj:
                    hrs_left = w_obj["max_hours"] - w_obj["hours_already_worked"]
                    if hrs_left <= 2:
                        contributing_factors.append(
                            f"{assigned_worker} has only {hrs_left}h remaining today"
                        )

            # Factor: no qualified backup workers
            if backup_count == 0:
                contributing_factors.append(
                    f"No backup worker available with skill '{required_skill}'"
                )

            # Factor: tight precedence
            tight_prec = [
                c for c in constraint_analysis
                if c["constraint_id"] in ("C2", "C3")
                and (c.get("predecessor") == jid or c.get("successor") == jid)
                and c["is_tight"]
            ]
            if tight_prec:
                contributing_factors.append("Precedence constraint is tight — no buffer")

            # Compute risk level
            risk_score = len(contributing_factors)
            risk_level = (
                "critical" if risk_score >= 3 else
                "high"     if risk_score == 2 else
                "medium"   if risk_score == 1 else
                "low"
            )

            risk_analysis.append({
                "job_id":                 jid,
                "job_name":               t["job_name"],
                "assigned_machine":       t["assigned_machine"],
                "assigned_worker":        assigned_worker,
                "risk_level":             risk_level,
                "risk_score":             risk_score,
                "contributing_factors":   contributing_factors,
                "qualified_backup_count": backup_count,
            })

    # ── Assemble final artifact ────────────────────────────────────
    artifact = {
        "run_id":                run_id,
        "date":                  date,
        "solve_status":          status,
        "objective_value":       objective,
        "conflict_set":          conflict_set,
        "availability_snapshot": availability_snapshot,
        "constraint_analysis":   constraint_analysis,
        "risk_analysis":         risk_analysis,
        "allocation_trace":      allocation_trace,
    }

    return artifact


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Line Scheduling Solver — RUN-001")
    print("=" * 60)

    # 1. Solve
    print("\n[1/3] Solving CP model...")
    mdl, sol, job_vars, job_alts = build_and_solve(dataset)

    if sol is None:
        print("      No solution found.")
    else:
        print(f"      Status    : {sol.get_solve_status()}")
        print(f"      Makespan  : {sol.get_objective_value()} time units")

    # 2. Extract artifacts
    print("\n[2/3] Extracting artifacts...")
    artifact = extract_artifacts(mdl, sol, job_vars, job_alts, dataset)

    # 3. Write to JSON
    os.makedirs("artifacts", exist_ok=True)
    filename = f"artifacts/{dataset['run_id']}_{dataset['date']}.json"
    with open(filename, "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"\n[3/3] Artifact written to: {filename}")

    # 4. Print a readable summary
    print("\n" + "=" * 60)
    print("SCHEDULE SUMMARY")
    print("=" * 60)

    if sol:
        print(f"\nMakespan: {artifact['objective_value']} time units\n")
        print(f"{'Job':<8} {'Machine':<10} {'Worker':<10} {'Start':>6} {'End':>6} {'Risk':<10}")
        print("-" * 54)
        for t in artifact["allocation_trace"]:
            risk = next(
                (r["risk_level"] for r in artifact["risk_analysis"] if r["job_id"] == t["job_id"]),
                "n/a"
            )
            print(
                f"{t['job_id']:<8} "
                f"{t['assigned_machine'] or 'UNASSIGNED':<10} "
                f"{t['assigned_worker'] or 'none':<10} "
                f"{str(t['start']):>6} "
                f"{str(t['end']):>6} "
                f"{risk:<10}"
            )

        print("\nCONSTRAINT ANALYSIS")
        print("-" * 54)
        for c in artifact["constraint_analysis"]:
            tight = "TIGHT" if c["is_tight"] else "slack"
            cid   = c["constraint_id"]
            if cid == "C1":
                print(f"  C1 ({c['machine']}): no-overlap — {tight}")
            elif cid in ("C2", "C3"):
                print(f"  {cid}: {c['predecessor']} → {c['successor']} "
                      f"gap={c['gap']} — {tight}")
            elif cid == "C4":
                print(f"  C4: Machine M3 unavailable — always TIGHT")
            elif cid == "C5":
                print(f"  C5: Worker W1 — {c['hours_remaining']}h remaining, "
                      f"{c['hours_assigned']}h assigned — {tight}")
    else:
        print("\nNo solution — conflict set:")
        for c in artifact["conflict_set"]:
            print(f"  {c}")

    print("\n" + "=" * 60)
    print("Artifact ready for microservices.")
    print("=" * 60)


if __name__ == "__main__":
    main()