#!/bin/bash
# =============================================================
# deploy.sh — Import tools and agent into WatsonX Orchestrate
# via the IBM ADK CLI
#
# Run from the line-scheduling-agent/ project root:
#   chmod +x deploy.sh
#   ./deploy.sh
# =============================================================

set -e  # stop on any error

echo "============================================================"
echo "Line Scheduling Agent — ADK Deploy"
echo "============================================================"

# ── Step 0: Check tool endpoint is reachable ─────────────────
echo ""
echo "[0/3] Checking tool endpoint..."
curl -s https://margot-unhesitative-mee.ngrok-free.dev/health || {
  echo "ERROR: Tool endpoint not reachable. Start Flask + ngrok first."
  exit 1
}
echo ""

# ── Step 1: Check environment is active ──────────────────────
echo ""
echo "[1/3] Checking active environment..."
orchestrate env list
echo ""
echo "If the above does not show your prod environment as active,"
echo "run: orchestrate env activate prod --api-key <your_key>"
echo "Then re-run this script."
echo ""

# ── Step 2: Import the five OpenAPI tools ─────────────────────
echo "[2/3] Importing OpenAPI tools..."

echo "  Importing get_iis_report..."
orchestrate tools import -k openapi -f tools/get_iis_report.yaml

echo "  Importing get_availability_snapshot..."
orchestrate tools import -k openapi -f tools/get_availability_snapshot.yaml

echo "  Importing get_constraint_slack..."
orchestrate tools import -k openapi -f tools/get_constraint_slack.yaml

echo "  Importing get_risk_score..."
orchestrate tools import -k openapi -f tools/get_risk_score.yaml

echo "  Importing get_allocation_trace..."
orchestrate tools import -k openapi -f tools/get_allocation_trace.yaml

echo ""
echo "  All 5 tools imported."

# ── Step 3: Import and deploy the agent ───────────────────────
echo ""
echo "[3/3] Importing agent..."
orchestrate agents import -f agents/line_scheduling_agent.agent.yaml

echo ""
echo "Deploying agent..."
orchestrate agents deploy --name line_scheduling_explanation_agent

echo ""
echo "============================================================"
echo "Deploy complete."
echo ""
echo "Test with:"
echo "  orchestrate chat ask --agent-name line_scheduling_explanation_agent \\"
echo "    \"run_id: RUN-001, date: 2026-04-12. Why was J1 assigned to M1?\""
echo ""
echo "Or open the WatsonX Orchestrate UI — the agent will appear"
echo "in the Agents catalog and can be chatted with directly."
echo "============================================================"