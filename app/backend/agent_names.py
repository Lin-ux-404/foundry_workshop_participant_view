"""Stable, team-scoped names for the DRAAD prompt agents.

This module deliberately contains no prompt instructions or tool definitions.
The runtime only needs the deployed agent names, which lets a participant deploy
their own implementations without importing the internal reference solution.
"""
from __future__ import annotations

from utils.naming import scoped_name


RETRIEVER_NAME = scoped_name(
    "draad-procedure-retriever", "DRAAD_RETRIEVER_AGENT"
)
MATCHER_NAME = scoped_name("draad-dispatch-matcher", "DRAAD_MATCHER_AGENT")
REVIEWER_NAME = scoped_name("draad-dispatch-reviewer", "DRAAD_REVIEWER_AGENT")
QA_NAME = scoped_name("draad-qa-assistant", "DRAAD_QA_AGENT")

ALL_AGENT_NAMES = (
    RETRIEVER_NAME,
    MATCHER_NAME,
    REVIEWER_NAME,
    QA_NAME,
)
