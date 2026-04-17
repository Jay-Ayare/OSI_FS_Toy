#!/bin/bash
# =============================================================
# deploy.sh — Import tools and agent into WatsonX Orchestrate
# via the IBM ADK CLI
#
# Run from the line-scheduling-agent/ project root:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# To update the Cloudflare URL, edit .env and change CLOUDFLARE_URL.
# Then re-run this script — it will patch all tool YAMLs automatically.
# =============================================================

set -e  # stop on any error

# ── Load .env ─────────────────────────────────────────────────
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
else
  echo "ERROR: .env file not found. Create one with CLOUDFLARE_URL, WO_API_KEY."
  exit 1
fi

if [ -z "$CLOUDFLARE_URL" ]; then
  echo "ERROR: CLOUDFLARE_URL is not set in .env"
  exit 1
fi

echo "============================================================"
echo "Line Scheduling Agent — ADK Deploy"
echo "============================================================"
echo ""
echo "Using Cloudflare URL: $CLOUDFLARE_URL"

# ── Step 0: Check tool endpoint is reachable ─────────────────
echo ""
echo "[0/4] Checking tool endpoint..."
curl -s "$CLOUDFLARE_URL/health" || {
  echo ""
  echo "ERROR: Tool endpoint not reachable. Start Flask + cloudflared first."
  exit 1
}
echo ""

# ── Step 1: Patch all tool YAMLs with current Cloudflare URL ─
echo "[1/4] Patching tool YAMLs with current Cloudflare URL..."
for f in tools/*.yaml; do
  # Replace any trycloudflare.com or ngrok URL with the current one
  sed -i '' "s|https://[a-z0-9-]*\.trycloudflare\.com|$CLOUDFLARE_URL|g" "$f"
  sed -i '' "s|https://[a-z0-9-]*\.ngrok-free\.app|$CLOUDFLARE_URL|g" "$f"
  sed -i '' "s|https://[a-z0-9-]*\.ngrok\.io|$CLOUDFLARE_URL|g" "$f"
  echo "  Patched: $f"
done
echo ""

# ── Step 2: Authenticate and import tools ────────────────────
echo "[2/4] Activating WatsonX Orchestrate environment..."
orchestrate env activate prod --api-key "$WO_API_KEY"
echo ""

echo "[3/4] Importing OpenAPI tools..."
for tool in get_iis_report get_availability_snapshot get_constraint_slack get_risk_score get_allocation_trace search_knowledge trace_query; do
  echo "  Importing $tool..."
  orchestrate tools import -k openapi -f "tools/${tool}.yaml"
done
echo ""
echo "  All tools imported."

# ── Step 3: Import agent ──────────────────────────────────────
echo ""
echo "[4/4] Importing agent..."
orchestrate agents import -f agents/line_scheduling_agent.agent.yaml

echo ""
echo "============================================================"
echo "Deploy complete."
echo ""
echo "Test with:"
echo "  orchestrate chat ask --agent-name line_scheduling_explanation_agent \\"
echo "    \"run_id: RUN-001, date: 2026-04-12. Why was J1 assigned to M1?\""
echo "============================================================"
