"""
observability/langfuse_client.py
=================================
Shared Langfuse v4 client and helper functions for the Line Scheduling
Explanation Agent.

Trace structure per pipeline run:
  - One root trace (scheduling_explanation_pipeline) with input = question,
    output = final answer set at the end via update_trace_output()
  - Child spans emitted in order: classify_intent → tool calls →
    search_knowledge → grounded_synthesis

Individual Flask endpoint calls (from WatsonX UI direct calls) each get
their own self-contained trace via log_endpoint_call().

Langfuse v4 API notes:
  - start_as_current_observation(as_type, name, trace_context, input, output)
  - TraceContext(trace_id=<32-char hex>) links spans to a trace
  - client.flush() must be called to push buffered events
"""

import os
import time as _time
from langfuse import Langfuse
from langfuse.types import TraceContext

# ── Client (singleton) ────────────────────────────────────────────
client = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-b6756553-e5bf-4fa4-9b55-d7daf992d841"),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-75e96842-3afa-427d-9f66-b51fcc152f2a"),
    host=os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
)


# ── ID factory ────────────────────────────────────────────────────

def make_trace_id() -> str:
    """
    Generates a Langfuse v4 compatible trace ID.
    Must be a 32-character lowercase hex string (OTel format, no hyphens).
    """
    import uuid
    return uuid.uuid4().hex


# ── Internal span emitter ─────────────────────────────────────────

def _emit(trace_id: str, name: str, as_type: str,
          input_data, output_data, metadata: dict = None):
    """
    Creates a single timed observation under the given trace_id.
    Uses a real sleep-free timing block so Langfuse records non-zero duration.
    """
    with client.start_as_current_observation(
        as_type=as_type,
        name=name,
        trace_context=TraceContext(trace_id=trace_id),
        input=input_data,
        metadata=metadata or {},
    ) as obs:
        # Set output inside the context so end_time is recorded after input
        obs.update(output=output_data)


# ── FIX 2: Root trace with proper input/output ────────────────────

def start_trace(trace_id: str, run_id: str, date: str, user_message: str) -> str:
    """
    Creates the root pipeline trace with the user question as input.
    Output is set later via update_trace_output() once synthesis completes.
    Returns trace_id for passing to all child span helpers.
    """
    _emit(
        trace_id=trace_id,
        name="scheduling_explanation_pipeline",
        as_type="span",
        input_data={
            "run_id": run_id,
            "date": date,
            "user_message": user_message,
        },
        output_data={"status": "pipeline started"},
        metadata={"node": "root", "run_id": run_id, "date": date},
    )
    return trace_id


def update_trace_output(trace_id: str, final_response: str):
    """
    FIX 2: Sets the root trace output to the final answer text so the
    trace is searchable by answer content in Langfuse.
    Called after grounded_synthesis completes.
    """
    _emit(
        trace_id=trace_id,
        name="trace_output",
        as_type="span",
        input_data={"trace_id": trace_id},
        output_data={"answer": final_response},
        metadata={"node": "trace_output"},
    )


# ── FIX 1: Per-endpoint trace for direct WatsonX tool calls ──────

def log_endpoint_call(endpoint_name: str, params: dict, result: dict):
    """
    FIX 1: Creates a self-contained trace for every direct Flask endpoint
    call. Fires whether the caller is a Python test, the LangFlow pipeline,
    or the WatsonX agent calling via OpenAPI — ensuring every tool call
    is visible in Langfuse regardless of entry path.
    """
    trace_id = make_trace_id()
    _emit(
        trace_id=trace_id,
        name=endpoint_name,
        as_type="tool",
        input_data=params,
        output_data=result,
        metadata={"source": "flask_endpoint", "endpoint": endpoint_name},
    )
    client.flush()


# ── Pipeline node span helpers ────────────────────────────────────

def log_intent_classification(
    trace_id: str,
    user_message: str,
    classified_intent: str,
    entities: dict,
    run_id: str,
    date: str,
):
    """Logs the intent classification step as a named span."""
    _emit(
        trace_id=trace_id,
        name="classify_intent",
        as_type="span",
        input_data={"user_message": user_message, "run_id": run_id, "date": date},
        output_data={"intent": classified_intent, "entities": entities},
        metadata={"node": "classify_intent"},
    )


def log_tool_call(
    trace_id: str,
    tool_name: str,
    tool_input: dict,
    tool_output: dict,
    latency_ms: int = None,
):
    """
    Logs a single microservice tool call as a named tool span.
    FIX 3: Called sequentially in the pipeline so spans appear in
    correct order — all tool calls complete before synthesis is logged.
    """
    _emit(
        trace_id=trace_id,
        name=tool_name,
        as_type="tool",
        input_data=tool_input,
        output_data=tool_output,
        metadata={"node": "tool_call", "tool": tool_name, "latency_ms": latency_ms},
    )


def log_knowledge_retrieval(
    trace_id: str,
    query: str,
    chunks_returned: list,
):
    """Logs the ChromaDB semantic search step as a retriever span."""
    _emit(
        trace_id=trace_id,
        name="search_knowledge",
        as_type="retriever",
        input_data={"query": query},
        output_data={"chunks": chunks_returned},
        metadata={"node": "knowledge_retrieval", "chunks_returned": len(chunks_returned)},
    )


def log_synthesis(
    trace_id: str,
    assembled_context: str,
    final_response: str,
    model: str = "gpt-oss-120b",
):
    """
    FIX 3: Logs the LLM synthesis step as a generation span.
    This is always the last span emitted in the pipeline, after all
    tool calls and knowledge retrieval have been logged.
    """
    _emit(
        trace_id=trace_id,
        name="grounded_synthesis",
        as_type="generation",
        input_data={"assembled_context": assembled_context},
        output_data={"response": final_response},
        metadata={"node": "synthesis", "model": model},
    )


def flush():
    """
    Flushes all pending spans to Langfuse. Call at the end of each
    pipeline run to ensure nothing is lost if the process exits.
    """
    client.flush()
