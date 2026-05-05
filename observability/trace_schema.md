# Langfuse Trace Schema — Line Scheduling Agent

Each user query produces one trace named `scheduling_explanation_pipeline`
with named spans per node. Open https://cloud.langfuse.com → Traces to view.

---

## Span Reference

| Span name                  | What it shows                                        |
|----------------------------|------------------------------------------------------|
| `classify_intent`          | Input question → classified intent + entities        |
| `get_iis_report`           | run_id + date → conflict set JSON                    |
| `get_constraint_slack`     | run_id + date → constraint tightness analysis        |
| `get_allocation_trace`     | run_id + date → full decision trace JSON             |
| `get_risk_score`           | run_id + date → risk scores per job                  |
| `get_availability_snapshot`| run_id + date → machines and workers available       |
| `search_knowledge`         | query → knowledge chunks returned from ChromaDB      |
| `grounded_synthesis`       | assembled context → final answer (generation span)   |

---

## Which Spans Appear Per Intent

| Intent          | Spans present                                                                                  |
|-----------------|-----------------------------------------------------------------------------------------------|
| `diagnostic`    | classify_intent → get_iis_report → get_constraint_slack → get_allocation_trace → search_knowledge → grounded_synthesis |
| `risk_assessment` | classify_intent → get_risk_score → get_constraint_slack → get_availability_snapshot → search_knowledge → grounded_synthesis |
| `availability`  | classify_intent → get_availability_snapshot → search_knowledge → grounded_synthesis           |
| `general`       | classify_intent → search_knowledge → grounded_synthesis                                       |

Spans only appear if they were called for that query.
A `diagnostic` trace will NOT show `get_risk_score`.
A `general` trace will show only `search_knowledge` and `grounded_synthesis`.

---

## Key Things to Check Per Trace

1. **Was the correct intent classified?**
   Check the `classify_intent` span output → `intent` field.

2. **Were only the expected spans present?**
   Cross-reference the table above. Extra or missing spans indicate
   a routing bug in the pipeline.

3. **Did `search_knowledge` return relevant chunks?**
   Check the span output → `chunks` array. Each chunk has an `id`,
   `title`, and `content`. Verify the top result is topically relevant
   to the question.

4. **Does the synthesis output match the DIRECT ANSWER?**
   The `grounded_synthesis` span output should begin with the direct
   answer stated in the final response. If it diverges, the LLM may
   be hallucinating beyond the assembled context.

5. **Latency hotspots**
   Each tool call span includes `latency_ms` in its metadata.
   High latency on `search_knowledge` may indicate ChromaDB cold start.
   High latency on tool calls may indicate Cloudflare tunnel instability.

---

## Trace Metadata Fields

Every trace carries these top-level metadata fields:

| Field         | Value                        |
|---------------|------------------------------|
| `run_id`      | e.g. `RUN-001`               |
| `date`        | e.g. `2026-04-12`            |
| `user_message`| The original question        |

---

## Test Queries and Expected Trace Shapes

**Test 1 — Diagnostic**
```
run_id: RUN-001, date: 2026-04-12
user_message: "Why was J1 assigned to M1?"
```
Expected spans: classify_intent, get_iis_report, get_constraint_slack,
get_allocation_trace, search_knowledge, grounded_synthesis

**Test 2 — Risk**
```
run_id: RUN-001, date: 2026-04-12
user_message: "Does this schedule look risky?"
```
Expected spans: classify_intent, get_risk_score, get_constraint_slack,
get_availability_snapshot, search_knowledge, grounded_synthesis

**Test 3 — General**
```
run_id: RUN-001, date: 2026-04-12
user_message: "What does makespan mean?"
```
Expected spans: classify_intent, search_knowledge, grounded_synthesis
(no tool chain spans — general intent skips all microservice calls)
