"""Run a Foundry-managed Prompt Agent via WorkflowBuilder streaming."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from agent_framework import AgentResponseUpdate, WorkflowBuilder

from config import get_foundry_agent


_TRANSIENT_STATUS_CODES = {404, 408, 409, 429, 500, 502, 503, 504}
_MAX_AGENT_ATTEMPTS = 6


def _transient_agent_error(exc: Exception) -> bool:
    """Recognize retryable service/propagation failures without masking bad prompts."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in _TRANSIENT_STATUS_CODES:
        return True
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "project not found",
            "agent not found",
            "status code: 429",
            "status code: 500",
            "status code: 502",
            "status code: 503",
            "status code: 504",
        )
    )


async def _retry_delay(attempt: int) -> None:
    await asyncio.sleep(min(2 ** (attempt - 1), 20))


@dataclass(frozen=True)
class AgentTextResult:
    """Text plus stable, human-readable citation labels from agent output."""

    text: str
    sources: tuple[str, ...]


def _annotation_value(annotation: Any, name: str) -> str:
    if isinstance(annotation, dict):
        value = annotation.get(name)
    else:
        value = getattr(annotation, name, None)
    return str(value).strip() if value else ""


def _citation_labels(update: AgentResponseUpdate) -> list[str]:
    """Extract citation titles without depending on SDK raw response classes."""
    labels: list[str] = []
    for content in update.contents:
        for annotation in getattr(content, "annotations", None) or []:
            if _annotation_value(annotation, "type") != "citation":
                continue
            title = _annotation_value(annotation, "title")
            url = _annotation_value(annotation, "url")
            label = title or url
            if label and label not in labels:
                labels.append(label)
    return labels


async def run_foundry_agent_with_citations(
    agent_name: str,
    input_text: str,
) -> AgentTextResult:
    """Run a prompt agent and retain citation annotations emitted by Search."""
    for attempt in range(1, _MAX_AGENT_ATTEMPTS + 1):
        text = ""
        sources: list[str] = []
        try:
            agent = get_foundry_agent(agent_name)
            workflow = WorkflowBuilder(start_executor=agent).build()
            async for event in workflow.run(input_text, stream=True):
                if event.type != "output" or not isinstance(
                    event.data, AgentResponseUpdate
                ):
                    continue
                text += event.data.text or ""
                for label in _citation_labels(event.data):
                    if label not in sources:
                        sources.append(label)
            return AgentTextResult(text=text, sources=tuple(sources))
        except Exception as exc:
            if text or attempt == _MAX_AGENT_ATTEMPTS or not _transient_agent_error(exc):
                raise
            await _retry_delay(attempt)
    raise AssertionError("agent retry loop exhausted without returning or raising")


async def run_foundry_agent(agent_name: str, input_text: str) -> str:
    """Run a Foundry-managed Prompt Agent and return its full text output."""
    return (await run_foundry_agent_with_citations(agent_name, input_text)).text


async def stream_foundry_agent(
    agent_name: str, input_text: str,
) -> AsyncGenerator[str, None]:
    """Yield token chunks as the Foundry agent streams its response."""
    for attempt in range(1, _MAX_AGENT_ATTEMPTS + 1):
        emitted = False
        try:
            agent = get_foundry_agent(agent_name)
            workflow = WorkflowBuilder(start_executor=agent).build()
            async for event in workflow.run(input_text, stream=True):
                if event.type == "output" and isinstance(
                    event.data, AgentResponseUpdate
                ):
                    if event.data.text:
                        emitted = True
                        yield event.data.text
            return
        except Exception as exc:
            if emitted or attempt == _MAX_AGENT_ATTEMPTS or not _transient_agent_error(exc):
                raise
            await _retry_delay(attempt)
    raise AssertionError("agent retry loop exhausted without returning or raising")
