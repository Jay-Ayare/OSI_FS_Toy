"""
classify_intent.py
==================
Python tool that classifies a scheduling question into one of four
intents: diagnostic, risk_assessment, availability, or general.

Import as a tool:
    orchestrate tools import -k python -f tools/python/classify_intent.py
"""

from pydantic import BaseModel
from ibm_watsonx_orchestrate.agent_builder.tools import tool


class IntentOutput(BaseModel):
    intent: str
    run_id: str
    date: str
    user_message: str


@tool
def classify_intent(run_id: str, date: str, user_message: str) -> IntentOutput:
    """
    Classifies the user's scheduling question into one of four intents:
    diagnostic, risk_assessment, availability, or general.
    Uses keyword matching against the user message.
    """
    msg = user_message.lower()

    diagnostic_keywords = [
        "why", "not assigned", "not scheduled", "constraint",
        "violation", "cause", "reason", "explain", "decision",
        "allocation", "assigned",
    ]
    risk_keywords = [
        "risk", "risky", "safe", "dangerous", "fragile",
        "could fail", "backup", "what if", "concern", "warning",
    ]
    availability_keywords = [
        "available", "availability", "who can", "which machine",
        "maintenance", "hours left", "qualified", "skill",
    ]

    if any(kw in msg for kw in risk_keywords):
        intent = "risk_assessment"
    elif any(kw in msg for kw in diagnostic_keywords):
        intent = "diagnostic"
    elif any(kw in msg for kw in availability_keywords):
        intent = "availability"
    else:
        intent = "general"

    return IntentOutput(
        intent=intent,
        run_id=run_id,
        date=date,
        user_message=user_message,
    )
