# Line Scheduling Explanation Agent

A proof-of-concept AI agent that answers natural language questions about the output of a CPLEX CP Optimizer scheduling model.

Given a question like *"Why was J1 assigned to M1?"* or *"Is the schedule risky?"*, the agent retrieves grounded data from structured microservices and a vector knowledge base, then synthesises a structured explanation.

---

## What It Does

CP Optimizer can find an optimal schedule, but it doesn't explain its decisions. This system adds an explainability layer — a conversational agent that can answer questions about:

- Why a specific job was assigned to a specific machine
- Which constraints are tight and what that means operationally
- Which jobs carry risk and what the contributing factors are
- What impact machine unavailability or worker hour limits had on the schedule

---

## Architecture

```
scheduler.py          →  runs CP Optimizer, writes artifact JSON
artifact JSON         →  single source of truth for a solver run
microservices.py      →  Flask API serving 6 endpoints over the artifact + ChromaDB
knowledge_base.py     →  builds ChromaDB vector store of domain knowledge
Cloudflare Tunnel     →  exposes Flask to the public internet
tool YAMLs            →  OpenAPI specs registered in WatsonX Orchestrate
agent YAML            →  ReAct agent with selective tool calling
WatsonX Orchestrate   →  hosts and runs the agent
```

---

## Project Structure

```
OSI_FS_Toy/
├── Personal/
│   └── scheduler.py                        # CP Optimizer solver
├── artifacts/
│   ├── microservices.py                    # Flask API (6 endpoints)
│   ├── knowledge_base.py                   # ChromaDB vector store builder
│   ├── chroma_db/                          # Persisted vector store (auto-created)
│   ├── RUN-001_2026-04-12.json             # Solver artifact
│   └── RUN-001_2026-04-12.md              # Human-readable artifact summary
└── line-scheduling-agent/
    ├── .venv/                              # Python virtual environment
    ├── deploy.sh                           # WatsonX Orchestrate deploy script
    ├── agents/
    │   └── line_scheduling_agent.agent.yaml
    └── tools/
        ├── get_iis_report.yaml
        ├── get_availability_snapshot.yaml
        ├── get_constraint_slack.yaml
        ├── get_risk_score.yaml
        ├── get_allocation_trace.yaml
        └── search_knowledge.yaml
```

---

## Prerequisites

- macOS (arm64) or Linux
- Python 3.10+
- IBM CPLEX Studio Community Edition — install from [ibm.com/products/ilog-cplex-optimization-studio](https://www.ibm.com/products/ilog-cplex-optimization-studio)
- Cloudflare tunnel — `brew install cloudflared`
- IBM WatsonX Orchestrate SaaS instance + API key
- Python packages (see below)

---

## First-Time Setup

### 1. Install Python dependencies

```bash
pip3 install docplex flask chromadb sentence-transformers --break-system-packages
```

### 2. Set up the WatsonX Orchestrate virtual environment

```bash
cd line-scheduling-agent
python3 -m venv .venv
source .venv/bin/activate
pip install ibm-watsonx-orchestrate
```

### 3. Register your WatsonX Orchestrate instance

```bash
orchestrate env add --name prod --url <your_instance_url>
orchestrate env activate prod --api-key <your_api_key>
```

### 4. Run the solver (generates the artifact JSON)

```bash
cd /path/to/OSI_FS_Toy
export PATH="$PATH:/Users/<you>/Applications/CPLEX_Studio_Community2212/cpoptimizer/bin/arm64_osx"
python3 Personal/scheduler.py
```

Output: `artifacts/RUN-001_2026-04-12.json`

### 5. Build the knowledge base (one-time)

```bash
cd artifacts
python3 knowledge_base.py
```

Output: `artifacts/chroma_db/` (persisted vector store, 11 documents indexed)

---

## Starting the System

Open 3 terminals and run one command in each:

**Terminal 1 — Flask microservice**
```bash
cd /path/to/OSI_FS_Toy/artifacts
python microservices.py
```

**Terminal 2 — Cloudflare tunnel**
```bash
cloudflared tunnel --url http://localhost:5001
```

Copy the URL printed (e.g. `https://something.trycloudflare.com`).

**Terminal 3 — Update tools and authenticate**

If the Cloudflare URL has changed since last time, update all tool YAMLs:
```bash
cd /path/to/OSI_FS_Toy/line-scheduling-agent
for f in tools/*.yaml; do
  sed -i '' 's|https://old-url.trycloudflare.com|https://new-url.trycloudflare.com|g' "$f"
done
for tool in get_iis_report get_availability_snapshot get_constraint_slack get_risk_score get_allocation_trace search_knowledge; do
  orchestrate tools import -k openapi -f tools/${tool}.yaml
done
```

Then activate and chat:
```bash
source .venv/bin/activate
orchestrate env activate prod --api-key <your_api_key>
orchestrate chat ask --agent-name line_scheduling_explanation_agent "run_id: RUN-001, date: 2026-04-12. Why was J1 assigned to M1?"
```

---

## Example Questions

```
run_id: RUN-001, date: 2026-04-12. Why was J1 assigned to M1?
Which constraints have no buffer at all?
Which job is most at risk and why?
Why wasn't W1 assigned to anything?
What impact did M3's maintenance have on the schedule?
Why are J1 and J3 medium risk but J2 is low risk?
What is the makespan and which job determines it?
```

---

## API Endpoints

All endpoints served at `http://localhost:5001`. Requires `?run_id=RUN-001&date=2026-04-12` unless noted.

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check, lists available artifacts |
| `GET /get_iis_report` | Conflict set (IIS equivalent) for infeasibility diagnosis |
| `GET /get_availability_snapshot` | Machines and workers available when solver ran |
| `GET /get_constraint_slack` | Constraint tightness and gap analysis |
| `GET /get_risk_score` | Risk scores and contributing factors per job |
| `GET /get_allocation_trace` | Full decision audit log — which machines were considered |
| `GET /search_knowledge?query=<text>` | Semantic search over domain knowledge (ChromaDB) |

---

## Known Limitations

- The Cloudflare quick tunnel URL changes on every restart — tool YAMLs must be updated and reimported each session. Use a named Cloudflare tunnel or deploy Flask to a permanent host to avoid this.
- The WatsonX Orchestrate MCSP token expires periodically — re-run `orchestrate env activate prod --api-key <key>` to refresh.
- The solver dataset is hardcoded in `scheduler.py`. Production use would require connecting to a live database (see `people_availability_snapshot.py` for the intended DB integration pattern).
