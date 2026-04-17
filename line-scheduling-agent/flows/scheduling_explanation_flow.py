"""
scheduling_explanation_flow.py
================================
Agentic workflow for the Line Scheduling Explanation Agent.
Routes questions through the appropriate tool chain based on
intent classification and returns structured data for the
outer agent to synthesise into an explanation.

Deploy:
    orchestrate tools import -k python -f tools/python/classify_intent.py
    orchestrate tools import -k flow -f flows/scheduling_explanation_flow.py

Flow structure:
    START
      │
      ▼
    [classify_intent]
      │
      ▼
    [branch on intent]
      ├── diagnostic   → iis_report → constraint_slack → allocation_trace → search_knowledge
      ├── risk         → risk_score → constraint_slack → availability     → search_knowledge
      ├── availability → availability                                      → search_knowledge
      └── general      →                                                    search_knowledge
      │
      ▼
    END
"""

from pydantic import BaseModel
from typing import Optional

from ibm_watsonx_orchestrate.flow_builder.flows import Flow, flow, START, END


# ─────────────────────────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────────────────────────

class FlowInput(BaseModel):
    run_id: str
    date: str
    user_message: str


class FlowOutput(BaseModel):
    intent: str
    run_id: str
    date: str
    user_message: str


# ─────────────────────────────────────────────────────────────────
# FLOW DEFINITION
# ─────────────────────────────────────────────────────────────────

@flow(
    name="scheduling_explanation_flow",
    display_name="Line Scheduling Explanation Flow",
    description=(
        "Routes scheduling questions through the appropriate tool chain "
        "based on intent classification. Returns structured tool results "
        "for the agent to synthesise into a grounded explanation."
    ),
    input_schema=FlowInput,
    output_schema=FlowOutput,
)
def build_scheduling_explanation_flow(aflow: Flow) -> Flow:

    # ── Node 1: classify intent ───────────────────────────────────
    classify_node = aflow.tool(
        "classify_intent",
        name="classify_intent",
        display_name="Classify Intent",
    )

    # ── Node 2: branch on intent ──────────────────────────────────
    intent_branch = aflow.branch(
        name="intent_branch",
        display_name="Branch on Intent",
        evaluator="flow.nodes.classify_intent.output.intent",
    )

    # ── Diagnostic path nodes ─────────────────────────────────────
    iis_node = aflow.tool(
        "get_iis_report",
        name="run_iis_report",
        display_name="Get IIS Report",
    )
    constraint_node_d = aflow.tool(
        "get_constraint_slack",
        name="run_constraint_slack_diagnostic",
        display_name="Get Constraint Slack (diagnostic)",
    )
    trace_node = aflow.tool(
        "get_allocation_trace",
        name="run_allocation_trace",
        display_name="Get Allocation Trace",
    )
    knowledge_node_d = aflow.tool(
        "search_knowledge",
        name="run_search_knowledge_diagnostic",
        display_name="Search Knowledge (diagnostic)",
    )

    # ── Risk path nodes ───────────────────────────────────────────
    risk_node = aflow.tool(
        "get_risk_score",
        name="run_risk_score",
        display_name="Get Risk Score",
    )
    constraint_node_r = aflow.tool(
        "get_constraint_slack",
        name="run_constraint_slack_risk",
        display_name="Get Constraint Slack (risk)",
    )
    avail_node_r = aflow.tool(
        "get_availability_snapshot",
        name="run_availability_risk",
        display_name="Get Availability (risk)",
    )
    knowledge_node_r = aflow.tool(
        "search_knowledge",
        name="run_search_knowledge_risk",
        display_name="Search Knowledge (risk)",
    )

    # ── Availability path nodes ───────────────────────────────────
    avail_node_a = aflow.tool(
        "get_availability_snapshot",
        name="run_availability",
        display_name="Get Availability",
    )
    knowledge_node_a = aflow.tool(
        "search_knowledge",
        name="run_search_knowledge_availability",
        display_name="Search Knowledge (availability)",
    )

    # ── General path node ─────────────────────────────────────────
    knowledge_node_g = aflow.tool(
        "search_knowledge",
        name="run_search_knowledge_general",
        display_name="Search Knowledge (general)",
    )

    # ── Wire edges ────────────────────────────────────────────────

    # Entry
    aflow.edge(START, classify_node)
    aflow.edge(classify_node, intent_branch)

    # Diagnostic path
    intent_branch.case("diagnostic", iis_node)
    aflow.sequence(iis_node, constraint_node_d, trace_node, knowledge_node_d)
    aflow.edge(knowledge_node_d, END)

    # Risk path
    intent_branch.case("risk_assessment", risk_node)
    aflow.sequence(risk_node, constraint_node_r, avail_node_r, knowledge_node_r)
    aflow.edge(knowledge_node_r, END)

    # Availability path
    intent_branch.case("availability", avail_node_a)
    aflow.sequence(avail_node_a, knowledge_node_a)
    aflow.edge(knowledge_node_a, END)

    # General path (default)
    intent_branch.default(knowledge_node_g)
    aflow.edge(knowledge_node_g, END)

    return aflow
