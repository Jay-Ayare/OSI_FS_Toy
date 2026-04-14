"""
knowledge_base.py
=================
Builds and queries a local ChromaDB vector store containing
Layer 1 domain knowledge: constraint glossary, risk thresholds,
and business rules for the line scheduling system.

Run once to index:
    python knowledge_base.py

Then import search_knowledge() from microservices.py.
"""

import os
import chromadb
from chromadb.utils import embedding_functions

# ── ChromaDB setup ────────────────────────────────────────────
CHROMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
COLLECTION_NAME = "scheduling_knowledge"

embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)


def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
    )


# ── Documents ─────────────────────────────────────────────────
DOCUMENTS = [
    {
        "id": "C1",
        "type": "constraint",
        "title": "No-overlap constraint",
        "content": (
            "The no-overlap constraint ensures that no two jobs run on the same machine "
            "at the same time. If jobs J_a and J_b are both assigned to machine M, one "
            "must fully complete before the other begins. A gap of 0 between them means "
            "the constraint is tight — back to back with no buffer. Any gap greater than "
            "0 means there is idle time on the machine between those jobs."
        ),
    },
    {
        "id": "C2",
        "type": "constraint",
        "title": "Precedence constraint — J3 after J1",
        "content": (
            "Job 3 (assembly) cannot start until Job 1 (welding) has fully finished. "
            "This is a hard precedence constraint. A gap of 0 means J3 started exactly "
            "when J1 ended — the constraint is tight and any delay to J1 directly delays "
            "J3 with no buffer. A positive gap means J3 was able to wait and there is "
            "some tolerance for J1 running over."
        ),
    },
    {
        "id": "C3",
        "type": "constraint",
        "title": "Precedence constraint — J5 after J2",
        "content": (
            "Job 5 (painting) cannot start until Job 2 (painting preparation) has fully "
            "finished. A gap of 0 means the constraint is tight. A gap greater than 0 "
            "means J5 had to wait for other reasons (e.g. machine busy) and there is "
            "tolerance for J2 running over."
        ),
    },
    {
        "id": "C4",
        "type": "constraint",
        "title": "Machine availability constraint",
        "content": (
            "Machine M3 is excluded from all scheduling on this date due to scheduled "
            "maintenance. Any job that could have run on M3 must be reassigned to M1 or "
            "M2, increasing competition for those machines and potentially extending the "
            "makespan."
        ),
    },
    {
        "id": "C5",
        "type": "constraint",
        "title": "Worker hours constraint",
        "content": (
            "Worker W1 has only 2 hours remaining in their shift today. W1 holds welding "
            "and assembly skills. Because of this limit, W1 cannot be assigned to jobs "
            "that would exceed their remaining capacity. This constraint is tight when W1 "
            "is the only qualified worker for a given job."
        ),
    },
    {
        "id": "R1",
        "type": "risk_threshold",
        "title": "Risk level definitions",
        "content": (
            "Risk levels are computed deterministically from four factors: number of "
            "qualified backup workers, hours already worked by the assigned worker, "
            "machine availability reduction, and precedence tightness. Low risk = 0 "
            "contributing factors. Medium risk = 1 contributing factor. High risk = 2 "
            "factors. Critical risk = 3 or more factors. A qualified backup count of 0 "
            "means there is no alternative worker if the assigned worker becomes unavailable."
        ),
    },
    {
        "id": "R2",
        "type": "risk_threshold",
        "title": "Tight precedence as a risk signal",
        "content": (
            "When a precedence constraint has a gap of 0, it is classified as a risk "
            "signal regardless of other factors. This is because any disruption to the "
            "predecessor job (machine fault, worker absence, extended duration) directly "
            "cascades to the successor job with zero tolerance."
        ),
    },
    {
        "id": "R3",
        "type": "risk_threshold",
        "title": "Worker exhaustion as a risk signal",
        "content": (
            "When the assigned worker has 2 or fewer hours remaining in their shift, this "
            "is flagged as a contributing risk factor. If that worker is also the only "
            "qualified person for the job (backup count = 0), the combined risk level "
            "escalates to high or critical."
        ),
    },
    {
        "id": "B1",
        "type": "business_rule",
        "title": "Makespan objective",
        "content": (
            "The scheduler minimises makespan — the total time from the start of the first "
            "job to the end of the last job across all machines. It does not optimise for "
            "cost, worker preference, or ergonomics. Any question about financial cost or "
            "budget is out of scope for this system."
        ),
    },
    {
        "id": "B2",
        "type": "business_rule",
        "title": "Machine assignment logic",
        "content": (
            "Each job must run on exactly one machine. The solver selects the machine that "
            "minimises makespan while satisfying all constraints. M3 is excluded on this "
            "date. Jobs are assigned to M1 or M2 based on their processing times on each "
            "machine and the no-overlap constraint."
        ),
    },
    {
        "id": "B3",
        "type": "business_rule",
        "title": "Worker assignment logic",
        "content": (
            "Worker assignment is determined by skill match and hours remaining. The worker "
            "with the most hours remaining among those qualified for the required skill is "
            "preferred. W1 (welding + assembly, 2h remaining) is deprioritised. W2 "
            "(painting + assembly, 6h remaining) and W3 (welding + painting, 8h remaining) "
            "are preferred for their respective skills."
        ),
    },
]


def build_index():
    """Index all documents into ChromaDB. Safe to re-run — upserts existing docs."""
    collection = get_collection()
    collection.upsert(
        ids=[doc["id"] for doc in DOCUMENTS],
        documents=[doc["content"] for doc in DOCUMENTS],
        metadatas=[{"type": doc["type"], "id": doc["id"], "title": doc["title"]} for doc in DOCUMENTS],
    )
    print(f"Indexed {len(DOCUMENTS)} documents into '{COLLECTION_NAME}'.")
    for doc in DOCUMENTS:
        print(f"  [{doc['type']}] {doc['id']}: {doc['title']}")


def search_knowledge(query: str, n_results: int = 3) -> list:
    """
    Search the knowledge base with a natural language query.
    Returns a list of dicts with: id, title, type, content, distance.
    """
    collection = get_collection()
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
    )
    output = []
    for i in range(len(results["ids"][0])):
        output.append({
            "id":       results["ids"][0][i],
            "title":    results["metadatas"][0][i]["title"],
            "type":     results["metadatas"][0][i]["type"],
            "content":  results["documents"][0][i],
            "distance": round(results["distances"][0][i], 4),
        })
    return output


if __name__ == "__main__":
    build_index()
