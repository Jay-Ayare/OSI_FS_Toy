"""
knowledge_base.py — Layer 1 Domain Knowledge
=============================================
Builds and queries the ChromaDB vector knowledge base for the
line scheduling XAI system.

DESIGN PRINCIPLE — STRICT SEPARATION OF CONCERNS:
  This file contains ONLY timeless domain knowledge:
    - What constraints mean (definitions)
    - How the scheduler makes decisions (business rules)
    - What risk levels imply (risk thresholds)
    - How to interpret scheduling concepts (concepts)

  This file does NOT contain:
    - Any data from a specific solver run
    - Any specific job assignments, start/end times
    - Any specific worker hours or availability values
    - Any specific machine status for a particular date
    - Any constraint slack or tightness values for a run

  All run-specific facts come exclusively from the five
  microservice tool endpoints (get_iis_report,
  get_availability_snapshot, get_constraint_slack,
  get_risk_score, get_allocation_trace).

  The knowledge base answers "what does X mean?"
  The tool endpoints answer "what happened in run Y?"

Run:
    python knowledge_base.py

This rebuilds the ChromaDB collection from scratch.
The chroma_db/ folder is created in the current directory.
"""

import os
import chromadb
from chromadb.utils import embedding_functions

# ─────────────────────────────────────────────────────────────────
# CHROMADB SETUP
# ─────────────────────────────────────────────────────────────────

CHROMA_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
COLLECTION_NAME = "scheduling_knowledge"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

client = chromadb.PersistentClient(path=CHROMA_PATH)
ef     = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name=EMBEDDING_MODEL
)

# ─────────────────────────────────────────────────────────────────
# INTENT-AWARE RETRIEVAL CONFIG
# ─────────────────────────────────────────────────────────────────

INTENT_RETRIEVAL_CONFIG = {
    "diagnostic": {
        "max_candidates": 4,
        "threshold":      0.45,
        "rationale": (
            "Constraint-specific queries need precision. "
            "Tight threshold avoids pulling in irrelevant "
            "business rules when explaining a specific "
            "constraint or allocation decision."
        ),
    },
    "risk_assessment": {
        "max_candidates": 5,
        "threshold":      0.50,
        "rationale": (
            "Risk queries need both constraint definitions "
            "and risk threshold docs to answer properly. "
            "Slightly broader retrieval to mix doc types."
        ),
    },
    "availability": {
        "max_candidates": 3,
        "threshold":      0.50,
        "rationale": (
            "Availability queries map closely to a small "
            "set of worker and machine rule documents. "
            "Keep retrieval tight."
        ),
    },
    "general": {
        "max_candidates": 8,
        "threshold":      0.65,
        "rationale": (
            "Open-ended or conceptual queries benefit from "
            "broader retrieval. Cast a wide net across all "
            "document types."
        ),
    },
    "default": {
        "max_candidates": 6,
        "threshold":      0.50,
        "rationale":      "Fallback config for unknown intent.",
    },
}

# ─────────────────────────────────────────────────────────────────
# LAYER 1 DOCUMENTS
# ─────────────────────────────────────────────────────────────────
# Each document is a timeless definition, rule, or concept.
# NO run-specific data. NO specific job names, worker values,
# machine states, or constraint outcomes from any solver run.
# ─────────────────────────────────────────────────────────────────

DOCUMENTS = [

    # ── CONSTRAINT CONCEPTS ──────────────────────────────────────

    {
        "id":    "CONCEPT_NO_OVERLAP",
        "type":  "constraint_concept",
        "title": "No-overlap constraint — what it means",
        "content": (
            "A no-overlap constraint ensures that no two jobs run "
            "on the same machine at the same time. If two jobs are "
            "both assigned to the same machine, one must completely "
            "finish before the other can begin. "
            "When the gap between two consecutive jobs on a machine "
            "is 0, the constraint is called tight — the jobs are "
            "back-to-back with zero idle time between them. "
            "A gap greater than 0 means there is idle time on that "
            "machine between those two jobs, which gives some "
            "tolerance for delays. "
            "A tight no-overlap constraint is a schedule risk signal "
            "because any overrun on the first job will directly delay "
            "the second job with no buffer to absorb it."
        ),
    },

    {
        "id":    "CONCEPT_PRECEDENCE",
        "type":  "constraint_concept",
        "title": "Precedence constraint — what it means",
        "content": (
            "A precedence constraint enforces that one job (the "
            "successor) cannot start until another job (the "
            "predecessor) has fully finished. "
            "The gap value measures how much time passes between "
            "the predecessor ending and the successor starting. "
            "A gap of 0 means the successor starts exactly when "
            "the predecessor ends — the constraint is tight and "
            "there is no buffer. Any delay to the predecessor "
            "directly delays the successor with zero tolerance. "
            "A positive gap means the successor was forced to wait "
            "for other reasons (such as a machine being busy), "
            "which provides some tolerance for the predecessor "
            "running slightly over. "
            "Tight precedence constraints are the primary source "
            "of cascade risk in a schedule — a single upstream "
            "delay propagates downstream through every dependent job."
        ),
    },

    {
        "id":    "CONCEPT_MACHINE_AVAILABILITY",
        "type":  "constraint_concept",
        "title": "Machine availability constraint — what it means",
        "content": (
            "A machine availability constraint excludes a specific "
            "machine from being used during a scheduling run. "
            "When a machine is unavailable (for example, due to "
            "scheduled maintenance, breakdown, or calibration), "
            "all jobs that could have used that machine must be "
            "reassigned to the remaining available machines. "
            "This increases competition for the available machines "
            "and can extend the overall makespan. "
            "The constraint is always considered tight when a "
            "machine is fully excluded, because removing it forces "
            "the optimizer to use suboptimal assignments for some "
            "jobs. The impact is proportional to how many jobs "
            "listed that machine as their preferred option."
        ),
    },

    {
        "id":    "CONCEPT_WORKER_HOURS",
        "type":  "constraint_concept",
        "title": "Worker hours constraint — what it means",
        "content": (
            "A worker hours constraint limits how many hours a "
            "worker can be assigned during a shift or day. "
            "Each worker has a maximum allowed hours value and "
            "an hours-already-worked value. The remaining capacity "
            "is max_hours minus hours_already_worked. "
            "When a worker's remaining hours are very low, they "
            "cannot be assigned to jobs that would exceed their "
            "capacity. This is especially significant when that "
            "worker is the only one with the required skill for "
            "certain jobs — their unavailability creates a risk "
            "of those jobs being unassignable. "
            "A worker hours constraint is considered tight when "
            "the remaining hours are at or near the minimum "
            "threshold needed to cover any job requiring that "
            "worker's skills."
        ),
    },

    # ── SCHEDULING CONCEPTS ──────────────────────────────────────

    {
        "id":    "CONCEPT_MAKESPAN",
        "type":  "scheduling_concept",
        "title": "Makespan — definition and significance",
        "content": (
            "Makespan is the total elapsed time from the start of "
            "the first job to the completion of the last job across "
            "all machines. It is the primary objective the scheduler "
            "minimises. "
            "The makespan is determined by whichever job finishes "
            "last. Reducing makespan requires either shortening the "
            "critical path (the chain of jobs and constraints that "
            "determines the last finish time) or reassigning jobs "
            "to machines where they complete faster. "
            "Makespan does not account for cost, worker preference, "
            "ergonomics, or quality — it is a pure time measure. "
            "Any question about financial cost or budget is outside "
            "the scope of the makespan-based scheduling model."
        ),
    },

    {
        "id":    "CONCEPT_CONSTRAINT_TIGHTNESS",
        "type":  "scheduling_concept",
        "title": "Constraint tightness — what tight and slack mean",
        "content": (
            "A constraint is described as tight when it is operating "
            "at its exact limit with no spare capacity. For interval "
            "constraints in CP Optimizer, tightness is measured by "
            "the gap between consecutive jobs or events. "
            "A gap of 0 means tight — the constraint is binding and "
            "any disruption will immediately cause a violation. "
            "A gap greater than 0 means the constraint has slack — "
            "there is tolerance before a violation occurs. "
            "Tight constraints are the first place to look when "
            "diagnosing why a schedule is fragile or why a delay "
            "cascades. They represent the bottlenecks of the schedule."
        ),
    },

    {
        "id":    "CONCEPT_CRITICAL_PATH",
        "type":  "scheduling_concept",
        "title": "Critical path — what determines the makespan",
        "content": (
            "The critical path is the sequence of jobs and "
            "constraints that determines the minimum possible "
            "makespan. Any delay on the critical path directly "
            "increases the makespan by the same amount. "
            "Jobs on the critical path have zero slack — they "
            "cannot be delayed without extending the overall "
            "schedule. Jobs off the critical path have positive "
            "slack — they can be delayed up to their slack amount "
            "without affecting the makespan. "
            "Tight precedence constraints and tight no-overlap "
            "constraints are indicators of critical path membership. "
            "When asking why a schedule cannot finish earlier, "
            "the answer is always found by tracing the critical path."
        ),
    },

    {
        "id":    "CONCEPT_ALTERNATIVE_MACHINE",
        "type":  "scheduling_concept",
        "title": "Machine selection — how the solver chooses",
        "content": (
            "For each job, the solver considers all available "
            "machines that the job can run on and selects exactly "
            "one. The selection is driven by the makespan "
            "minimisation objective combined with the no-overlap "
            "constraint. "
            "A job will be assigned to the machine that results "
            "in the lowest overall makespan, not necessarily the "
            "machine where the job runs fastest in isolation. "
            "If the fastest machine for a job is already heavily "
            "occupied by other jobs, the solver may assign the "
            "job to a slower machine that has a free window, "
            "because this produces a better overall schedule. "
            "The alternatives_considered field in the allocation "
            "trace shows all machines the solver evaluated and "
            "which one was selected."
        ),
    },

    {
        "id":    "CONCEPT_WORKER_ASSIGNMENT",
        "type":  "scheduling_concept",
        "title": "Worker assignment — how workers are allocated",
        "content": (
            "Worker assignment is a post-solve step that maps "
            "workers to jobs based on skill match and remaining "
            "hours. The solver itself optimises machine assignment "
            "and timing — worker assignment follows from that. "
            "A worker can only be assigned to a job if they have "
            "the required skill for that job. Among qualified "
            "workers, the one with the most remaining hours is "
            "preferred, as this reduces the risk of hitting hour "
            "limits mid-schedule. "
            "If only one worker has the required skill for a job, "
            "that worker is the sole qualified person and their "
            "absence or exhaustion creates an unresolvable gap. "
            "The qualified_backup_count in the risk analysis "
            "shows how many alternative workers could cover "
            "a job if the assigned worker became unavailable."
        ),
    },

    # ── RISK CONCEPTS ─────────────────────────────────────────────

    {
        "id":    "CONCEPT_RISK_LEVELS",
        "type":  "risk_concept",
        "title": "Risk levels — how they are defined and computed",
        "content": (
            "Risk levels are computed deterministically from four "
            "contributing factors. Each factor that applies adds 1 "
            "to the risk score. "
            "Low risk: 0 contributing factors. The assignment is "
            "stable with no identified vulnerability. "
            "Medium risk: 1 contributing factor. One vulnerability "
            "exists but the assignment is generally manageable. "
            "High risk: 2 contributing factors. Multiple "
            "vulnerabilities compound each other. "
            "Critical risk: 3 or more contributing factors. The "
            "assignment is fragile and should be reviewed before "
            "the schedule is executed. "
            "The four possible contributing factors are: "
            "tight precedence constraint with no buffer, "
            "machine availability reduction reducing options, "
            "the assigned worker having very few hours remaining, "
            "and no qualified backup worker existing for the job."
        ),
    },

    {
        "id":    "CONCEPT_RISK_TIGHT_PRECEDENCE",
        "type":  "risk_concept",
        "title": "Tight precedence as a risk signal",
        "content": (
            "A tight precedence constraint is flagged as a risk "
            "factor regardless of other conditions. This is because "
            "a gap of 0 between a predecessor and successor job "
            "means any disruption to the predecessor — a machine "
            "fault, a worker running late, extended setup time, "
            "or any other cause of overrun — will immediately "
            "cascade to the successor job with zero tolerance. "
            "In a chain of dependent jobs, a single tight "
            "precedence at any point can cause the entire "
            "downstream chain to shift, potentially affecting "
            "the makespan. The longer the chain and the earlier "
            "the tight constraint, the greater the cascade risk."
        ),
    },

    {
        "id":    "CONCEPT_RISK_BACKUP_COUNT",
        "type":  "risk_concept",
        "title": "Qualified backup count — what it means for risk",
        "content": (
            "The qualified backup count is the number of workers "
            "who could cover a job if the assigned worker became "
            "unavailable. It is calculated as the total number of "
            "workers with the required skill minus one (the "
            "assigned worker themselves). "
            "A backup count of 0 means there is no alternative "
            "worker. If the assigned worker is absent, ill, or "
            "exhausted, the job cannot be covered and will either "
            "be delayed or left unassigned. This is the highest "
            "single-factor risk signal in the system. "
            "A backup count of 1 means one alternative exists, "
            "which provides minimal but non-zero resilience. "
            "Higher backup counts indicate greater schedule "
            "resilience against worker-related disruptions."
        ),
    },

    {
        "id":    "CONCEPT_INFEASIBILITY",
        "type":  "risk_concept",
        "title": "Infeasibility — when the model cannot find a solution",
        "content": (
            "A schedule is infeasible when the combination of "
            "constraints makes it mathematically impossible to "
            "assign all jobs within the given rules. "
            "The solver identifies the minimal set of constraints "
            "that together cause the impossibility — this is called "
            "the conflict set (equivalent to the Irreducible "
            "Infeasible Set in LP models). "
            "Common causes of infeasibility include: all machines "
            "for a required job being unavailable simultaneously, "
            "a worker being the only qualified person for multiple "
            "jobs that overlap in time, or a precedence chain that "
            "cannot be satisfied within the available time horizon. "
            "When the model is feasible, the conflict set is empty "
            "and the solve_status is optimal or feasible."
        ),
    },

    # ── SYSTEM SCOPE ─────────────────────────────────────────────

    {
        "id":    "CONCEPT_SYSTEM_SCOPE",
        "type":  "system_concept",
        "title": "What this system can and cannot answer",
        "content": (
            "This scheduling explanation system can answer "
            "questions about: why a specific job was assigned to "
            "a specific machine, why a constraint is tight or "
            "has slack, what the risk level of an assignment means, "
            "which jobs are on the critical path, what the makespan "
            "is and which job determines it, whether the model was "
            "infeasible and what caused it, and which workers or "
            "machines were available during a run. "
            "This system cannot answer questions about: financial "
            "cost or budget, worker pay rates, shift preferences "
            "or ergonomics, quality metrics, customer satisfaction, "
            "or any information not present in the solver artifact "
            "and the domain knowledge base. "
            "For out-of-scope questions, the system will explicitly "
            "state that the information is not available rather "
            "than estimating or fabricating an answer."
        ),
    },
]

# ─────────────────────────────────────────────────────────────────
# BUILD THE COLLECTION
# ─────────────────────────────────────────────────────────────────

def build_knowledge_base(force_rebuild: bool = False):
    """
    Builds the ChromaDB collection from the DOCUMENTS list.

    If force_rebuild=True: drops the existing collection and rebuilds.
    If force_rebuild=False: checks if the collection already has
    documents and skips the rebuild if so.
    """
    if not force_rebuild:
        try:
            existing = client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
            count = existing.count()
            if count > 0:
                print(f"Collection exists with {count} documents, skipping rebuild.")
                print("Run with --rebuild to force a full rebuild.")
                return existing
        except Exception:
            pass  # Collection doesn't exist yet — fall through to build

    # Drop and rebuild
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Dropped existing collection: {COLLECTION_NAME}")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    ids       = [doc["id"]      for doc in DOCUMENTS]
    contents  = [doc["content"] for doc in DOCUMENTS]
    metadatas = [
        {"id": doc["id"], "type": doc["type"], "title": doc["title"]}
        for doc in DOCUMENTS
    ]

    collection.add(ids=ids, documents=contents, metadatas=metadatas)

    print(f"Indexed {len(DOCUMENTS)} documents into '{COLLECTION_NAME}'")
    print("\nDocuments indexed:")
    for doc in DOCUMENTS:
        print(f"  [{doc['type']:25s}] {doc['id']:35s} — {doc['title']}")

    return collection


# ─────────────────────────────────────────────────────────────────
# QUERY FUNCTION
# ─────────────────────────────────────────────────────────────────

def get_collection():
    """
    Returns the existing collection, or creates an empty one if it
    doesn't exist yet. This allows the system to start cleanly on a
    fresh machine without requiring python knowledge_base.py first.
    """
    try:
        return client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
    except Exception:
        # Collection does not exist — create empty
        return client.create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )


def search_knowledge(query, intent="default", n_results=None):
    """
    Retrieves knowledge chunks dynamically.

    If n_results is set explicitly, retrieves exactly that many
    chunks with no threshold filtering (backward-compatible override).

    Otherwise uses intent to select max_candidates and threshold
    from INTENT_RETRIEVAL_CONFIG. Always returns at least 1 chunk.

    Args:
        query:     natural language search query
        intent:    classified intent (diagnostic / risk_assessment /
                   availability / general / default)
        n_results: hard override — bypasses intent config
    Returns:
        list of dicts: id, title, type, content, distance
    """
    collection = get_collection()

    # ── Override mode ──────────────────────────────────────────
    if n_results is not None:
        candidates = min(n_results, collection.count())
        results = collection.query(
            query_texts=[query],
            n_results=candidates,
            include=["documents", "metadatas", "distances"],
        )
        return [
            {
                "id":       m.get("id") or m.get("source_file", "unknown"),
                "title":    m.get("title") or m.get("source_file", ""),
                "type":     m.get("source_type") or m.get("type", ""),
                "content":  d,
                "distance": round(dist, 4),
            }
            for d, m, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    # ── Intent-aware threshold mode ────────────────────────────
    config         = INTENT_RETRIEVAL_CONFIG.get(intent, INTENT_RETRIEVAL_CONFIG["default"])
    max_candidates = min(config["max_candidates"], collection.count())
    threshold      = config["threshold"]

    results = collection.query(
        query_texts=[query],
        n_results=max_candidates,
        include=["documents", "metadatas", "distances"],
    )

    docs      = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    def _fmt(m, d, dist):
        return {
            "id":       m.get("id") or m.get("source_file", "unknown"),
            "title":    m.get("title") or m.get("source_file", ""),
            "type":     m.get("source_type") or m.get("type", ""),
            "content":  d,
            "distance": round(dist, 4),
        }

    filtered = [
        _fmt(m, d, dist)
        for d, m, dist in zip(docs, metadatas, distances)
        if dist < threshold
    ]

    # Always return at least 1 chunk (the closest one)
    if not filtered and docs:
        filtered = [_fmt(metadatas[0], docs[0], distances[0])]

    return filtered


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    force = "--rebuild" in sys.argv

    print("=" * 60)
    print("Building scheduling knowledge base")
    if force:
        print("Mode: FORCE REBUILD (--rebuild flag set)")
    print("=" * 60)
    collection = build_knowledge_base(force_rebuild=force)

    print("\n" + "=" * 60)
    print("Smoke tests")
    print("=" * 60)

    tests = [
        ("Why was J1 assigned to M1?",           "diagnostic"),
        ("Does this schedule look risky?",        "risk_assessment"),
        ("Which workers are available today?",    "availability"),
        ("What does makespan mean?",              "general"),
        ("What happens if J1 runs over?",         "general"),
        ("Why is there no buffer between jobs?",  "diagnostic"),
    ]

    for query, intent in tests:
        chunks = search_knowledge(query, intent=intent)
        print(f"\nQ ({intent}): {query}")
        for c in chunks:
            print(f"  → [{c['type']:25s}] {c['id']:35s} dist={c['distance']}")

    print("\n" + "=" * 60)
    print("Knowledge base ready.")
    print("=" * 60)
