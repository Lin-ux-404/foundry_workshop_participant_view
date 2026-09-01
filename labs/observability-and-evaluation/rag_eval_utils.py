"""Shared deterministic helpers for the live end-to-end RAG evaluation labs."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


_CITATION_RE = re.compile(
    r"\[(?P<bracket>S\d+|ref_id:\d+)\]|"
    r"【(?P<foundry>\d+)[^】]*】",
    re.IGNORECASE,
)
_IQ_IDENTITY_FIELDS = (
    "id",
    "vwi_code",
    "raamopdracht_id",
    "crew_id",
    "title",
    "source_file",
)


def find_repo_root(start: Path | None = None) -> Path:
    """Find the repository root from a notebook or command-line working directory."""
    current = (start or Path.cwd()).resolve()
    for folder in (current, *current.parents):
        if (folder / ".env.example").exists() and (folder / "labs").is_dir():
            return folder
    raise FileNotFoundError("Repository root not found")


def load_cases(path: Path, target: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load and validate the versioned benchmark cases for one retrieval target."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("Unsupported RAG evaluation dataset schema")
    if (
        target == "azure_ai_search"
        and payload.get("answer_contract_version")
        != "search-required-parts-v2"
    ):
        raise ValueError("Unsupported Search answer contract")
    cases = [row for row in payload.get("cases", []) if row.get("target") == target]
    if not cases:
        raise ValueError(f"No evaluation cases found for target {target!r}")
    case_ids = [str(row.get("case_id", "")).strip() for row in cases]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("Every evaluation case needs a unique non-empty case_id")
    for row in cases:
        if not row.get("query") or not row.get("ground_truth"):
            raise ValueError(f"{row['case_id']}: query and ground_truth are required")
        groups = row.get("expected_evidence_groups")
        if not groups or not all(group.get("any_of") for group in groups):
            raise ValueError(f"{row['case_id']}: expected evidence groups are required")
        if target == "azure_ai_search":
            answer_parts = row.get("required_answer_parts")
            if not isinstance(answer_parts, list) or not answer_parts:
                raise ValueError(
                    f"{row['case_id']}: required_answer_parts are required for Search"
                )
            part_ids = []
            for part in answer_parts:
                if not isinstance(part, Mapping):
                    raise ValueError(
                        f"{row['case_id']}: every required answer part must be an object"
                    )
                raw_part_id = part.get("id")
                raw_description = part.get("description")
                if not isinstance(raw_part_id, str) or not isinstance(
                    raw_description, str
                ):
                    raise ValueError(
                        f"{row['case_id']}: required answer parts need string id and description"
                    )
                part_id = raw_part_id.strip()
                description = raw_description.strip()
                if (
                    not part_id
                    or not description
                    or not re.fullmatch(r"[a-z][a-z0-9_]*", part_id)
                ):
                    raise ValueError(
                        f"{row['case_id']}: required answer parts need a valid id and description"
                    )
                part_ids.append(part_id)
            if len(part_ids) != len(set(part_ids)):
                raise ValueError(
                    f"{row['case_id']}: required answer part IDs must be unique"
                )
        elif target == "foundry_iq":
            source_filters = row.get("source_filters")
            required_sources = {"procedure", "authorization", "crew"}
            if not isinstance(source_filters, Mapping):
                raise ValueError(
                    f"{row['case_id']}: source_filters are required for Foundry IQ"
                )
            missing_sources = sorted(
                key
                for key in required_sources
                if not str(source_filters.get(key, "")).strip()
            )
            if missing_sources:
                raise ValueError(
                    f"{row['case_id']}: source_filters missing "
                    f"{', '.join(missing_sources)}"
                )
    return payload, cases


def _normalise_key(value: Any) -> str:
    return str(value).strip().casefold()


def document_evidence_keys(document: Mapping[str, Any]) -> set[str]:
    """Return stable identifiers that can satisfy benchmark evidence groups."""
    keys: set[str] = set()
    for field in (
        "id",
        "vwi_code",
        "raamopdracht_id",
        "crew_id",
        "title",
        "bestemd_voor",
        "name",
        "source_file",
    ):
        value = document.get(field)
        if value:
            keys.add(_normalise_key(value))
    vwi_code = document.get("vwi_code")
    page_number = document.get("page_number")
    if vwi_code and page_number not in (None, ""):
        keys.add(_normalise_key(f"{vwi_code}#p{page_number}"))
    return keys


def retrieval_recall(
    expected_groups: Iterable[Mapping[str, Any]],
    retrieved_keys: Iterable[str],
) -> dict[str, Any]:
    """Compute evidence-group recall, allowing equivalent identifiers per group."""
    key_set = {_normalise_key(value) for value in retrieved_keys}
    details: list[dict[str, Any]] = []
    for group in expected_groups:
        alternatives = {_normalise_key(value) for value in group["any_of"]}
        matched = sorted(alternatives & key_set)
        details.append(
            {
                "name": str(group.get("name", "evidence")),
                "matched": bool(matched),
                "matched_keys": matched,
            }
        )
    matched_count = sum(int(item["matched"]) for item in details)
    return {
        "recall": matched_count / len(details) if details else 1.0,
        "matched_groups": matched_count,
        "total_groups": len(details),
        "groups": details,
    }


def _citation_id(match: re.Match[str]) -> str:
    if match.group("foundry") is not None:
        return f"ref_id:{match.group('foundry')}".casefold()
    return str(match.group("bracket")).casefold()


def _normalise_citation_id(value: Any) -> str:
    citation_id = _normalise_key(value)
    citation_id = citation_id.removeprefix("[").removesuffix("]")
    if citation_id.isdigit():
        return f"ref_id:{citation_id}"
    return citation_id


def factual_units(answer: str) -> list[str]:
    """Split an answer into factual units without counting markdown headings."""
    units: list[str] = []
    for raw_line in answer.splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", raw_line).strip()
        if not line or line.startswith("#"):
            continue
        citation_free = _CITATION_RE.sub("", line).strip()
        if citation_free.endswith(":") and len(citation_free.split()) <= 12:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line):
            sentence = sentence.strip()
            if len(re.findall(r"\w+", _CITATION_RE.sub("", sentence))) >= 3:
                units.append(sentence)
    return units


def validate_cited_answer(
    value: str | Mapping[str, Any],
    valid_reference_ids: Iterable[str],
    required_answer_part_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate claim citations and, when supplied, required-answer-part accounting."""
    valid = {_normalise_citation_id(item) for item in valid_reference_ids}
    required_parts: list[str] = []
    if required_answer_part_ids is not None:
        if isinstance(required_answer_part_ids, (str, bytes)):
            raise ValueError("required_answer_part_ids must be an iterable of IDs")
        required_parts = [str(item).strip() for item in required_answer_part_ids]
        if not required_parts or not all(required_parts):
            raise ValueError("required_answer_part_ids must contain non-empty IDs")
        if len(required_parts) != len(set(required_parts)):
            raise ValueError("required_answer_part_ids must be unique")
    required_part_set = set(required_parts)

    if isinstance(value, Mapping):
        raw_claims = value.get("claims")
        if not isinstance(raw_claims, list) or not raw_claims:
            raise ValueError("A structured cited answer needs at least one claim")
        claim_inputs: list[tuple[str, list[str], str, str | None]] = []
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, Mapping):
                raise ValueError("Every structured claim must be an object")
            text = str(raw_claim.get("text", "")).strip()
            source_ids = raw_claim.get("source_ids")
            if not text or not isinstance(source_ids, list) or not source_ids:
                raise ValueError(
                    "Every structured claim needs text and at least one source_id"
                )
            answer_part_id: str | None = None
            if required_parts:
                raw_answer_part_id = raw_claim.get("answer_part_id")
                if not isinstance(raw_answer_part_id, str):
                    raise ValueError(
                        "Every structured claim needs an answer_part_id"
                    )
                answer_part_id = raw_answer_part_id.strip()
                if answer_part_id not in required_part_set:
                    raise ValueError(
                        f"Unknown claim answer_part_id: {answer_part_id!r}"
                    )
            citations = [_normalise_citation_id(item) for item in source_ids]
            claim_inputs.append((text, citations, text, answer_part_id))
        insufficient_evidence = value.get("insufficient_evidence", [])
        if insufficient_evidence is None:
            insufficient_evidence = []
        if not isinstance(insufficient_evidence, list):
            raise ValueError("insufficient_evidence must be an array")
        if required_parts:
            if not all(isinstance(item, str) for item in insufficient_evidence):
                raise ValueError(
                    "insufficient_evidence must contain answer-part IDs"
                )
            insufficient_evidence = [
                str(item).strip() for item in insufficient_evidence
            ]
            if not all(insufficient_evidence):
                raise ValueError(
                    "insufficient_evidence must contain non-empty answer-part IDs"
                )
            if len(insufficient_evidence) != len(set(insufficient_evidence)):
                raise ValueError("insufficient_evidence must not contain duplicates")
            unknown_insufficient = sorted(
                set(insufficient_evidence) - required_part_set
            )
            if unknown_insufficient:
                raise ValueError(
                    "Unknown insufficient_evidence answer-part IDs: "
                    + ", ".join(unknown_insufficient)
                )
    else:
        if required_parts:
            raise ValueError(
                "Required-answer-part validation needs a structured cited answer"
            )
        claim_inputs = []
        for unit in factual_units(str(value)):
            citations = [
                _citation_id(match) for match in _CITATION_RE.finditer(unit)
            ]
            text = re.sub(r"\s+", " ", _CITATION_RE.sub("", unit)).strip()
            claim_inputs.append((text, citations, unit, None))
        insufficient_evidence = []

    cited_units = 0
    validly_cited_units = 0
    all_citations: list[str] = []
    invalid_citations: list[str] = []
    claims: list[dict[str, Any]] = []
    answered_part_ids: set[str] = set()
    for index, (text, citations, source_text, answer_part_id) in enumerate(
        claim_inputs, start=1
    ):
        valid_citations = [item for item in citations if item in valid]
        unresolved = [item for item in citations if item not in valid]
        if citations:
            cited_units += 1
        if citations and not unresolved:
            validly_cited_units += 1
        all_citations.extend(citations)
        invalid_citations.extend(unresolved)
        claim = {
            "claim_number": index,
            "text": text,
            "source_text": source_text,
            "source_ids": citations,
            "citation_ids": citations,
            "valid_citation_ids": valid_citations,
            "invalid_citation_ids": unresolved,
            "status": (
                "uncited"
                if not citations
                else "unresolved"
                if unresolved
                else "resolved"
            ),
        }
        if answer_part_id is not None:
            claim["answer_part_id"] = answer_part_id
            answered_part_ids.add(answer_part_id)
        claims.append(claim)

    insufficient_part_ids = set(insufficient_evidence) if required_parts else set()
    if required_parts:
        overlap = sorted(answered_part_ids & insufficient_part_ids)
        if overlap:
            raise ValueError(
                "Answer parts cannot be both answered and insufficient: "
                + ", ".join(overlap)
            )
        unaccounted = sorted(
            required_part_set - answered_part_ids - insufficient_part_ids
        )
        if unaccounted:
            raise ValueError(
                "Required answer parts are unaccounted for: "
                + ", ".join(unaccounted)
            )

    unit_count = len(claim_inputs)
    return {
        "coverage": cited_units / unit_count if unit_count else 1.0,
        "valid_coverage": validly_cited_units / unit_count if unit_count else 1.0,
        "validity": (
            (len(all_citations) - len(invalid_citations)) / len(all_citations)
            if all_citations
            else 0.0
        ),
        "factual_units": unit_count,
        "cited_units": cited_units,
        "validly_cited_units": validly_cited_units,
        "citation_count": len(all_citations),
        "invalid_citations": sorted(set(invalid_citations)),
        "claims": claims,
        "insufficient_evidence": [str(item) for item in insufficient_evidence],
        "required_answer_part_ids": required_parts,
        "answered_answer_part_ids": [
            item for item in required_parts if item in answered_part_ids
        ],
        "answer_part_coverage": (
            len(answered_part_ids) / len(required_parts)
            if required_parts
            else None
        ),
    }


def citation_metrics(answer: str, valid_citation_ids: Iterable[str]) -> dict[str, Any]:
    """Backward-compatible alias for structured claim-level validation."""
    return validate_cited_answer(answer, valid_citation_ids)


def render_cited_answer(value: Mapping[str, Any]) -> str:
    """Render validated claims with machine-checkable inline reference IDs."""
    claims = value.get("claims")
    if not isinstance(claims, list):
        raise TypeError("render_cited_answer expects validate_cited_answer output")
    lines: list[str] = []
    for claim in claims:
        text = re.sub(r"\s+", " ", str(claim.get("text", ""))).strip()
        citations = " ".join(
            f"[{citation_id.upper() if re.fullmatch(r's\d+', citation_id) else citation_id}]"
            for citation_id in claim.get("citation_ids") or []
        )
        lines.append(f"- {text} {citations}".rstrip())
    return "\n".join(lines)


def select_coherent_search_documents(
    documents: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Keep the top-ranked VWI variant coherent for answer generation.

    Retrieval metrics still use the unfiltered ranked list. This selection uses
    only the top result returned by Search, never benchmark labels or ground
    truth, and prevents a generated answer from mixing similarly named but
    operationally different procedure variants.
    """
    ranked = list(documents)
    if not ranked:
        raise ValueError("Search returned no documents for coherent selection")
    primary_vwi_code = _normalise_key(ranked[0].get("vwi_code") or "")
    if not primary_vwi_code:
        raise ValueError("Top-ranked Search document has no vwi_code")
    coherent = [
        document
        for document in ranked
        if _normalise_key(document.get("vwi_code") or "") == primary_vwi_code
    ]
    if not coherent or any(
        _normalise_key(document.get("vwi_code") or "") != primary_vwi_code
        for document in coherent
    ):
        raise ValueError("Search generation context is not variant-coherent")
    return coherent


def ranked_evidence_metrics(
    expected_groups: Iterable[Mapping[str, Any]],
    documents: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Measure precision and first-match rank over the untouched Search list."""
    expected_keys = {
        _normalise_key(value)
        for group in expected_groups
        for value in group.get("any_of") or []
    }
    ranked = list(documents)
    matches = [
        bool(document_evidence_keys(document) & expected_keys)
        for document in ranked
    ]
    first_match_rank = next(
        (rank for rank, matched in enumerate(matches, start=1) if matched),
        None,
    )
    return {
        "precision": sum(matches) / len(matches) if matches else 0.0,
        "top_1_match": bool(matches and matches[0]),
        "first_match_rank": first_match_rank,
        "reciprocal_rank": 1.0 / first_match_rank if first_match_rank else 0.0,
        "matched_ranks": [
            rank for rank, matched in enumerate(matches, start=1) if matched
        ],
    }


def format_search_context(documents: Iterable[Mapping[str, Any]]) -> str:
    """Format direct Search results with stable source labels for generation."""
    blocks: list[str] = []
    for index, document in enumerate(documents, start=1):
        blocks.append(
            f"[S{index}] {document.get('vwi_code') or document.get('title') or 'document'}"
            f" | {document.get('source_file', '')}"
            f" | page {document.get('page_number', '')}\n"
            f"{document.get('content') or document.get('excerpt') or ''}"
        )
    return "\n\n".join(blocks)


def prepare_coherent_search_context(
    documents: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the exact coherent Search context and its valid citation IDs."""
    coherent = select_coherent_search_documents(documents)
    primary_vwi_code = str(coherent[0].get("vwi_code") or "").strip()
    return {
        "documents": coherent,
        "primary_vwi_code": primary_vwi_code,
        "context": format_search_context(coherent),
        "valid_source_ids": {
            f"S{index}" for index in range(1, len(coherent) + 1)
        },
    }


def _iq_response_text_blocks(payload: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for message in payload.get("response") or []:
        for content in message.get("content") or []:
            if content.get("type") == "text" and content.get("text"):
                texts.append(str(content["text"]))
    if not texts:
        result = payload.get("result")
        if isinstance(result, Mapping):
            for content in result.get("content") or []:
                if content.get("type") == "text" and content.get("text"):
                    texts.append(str(content["text"]))
    return texts


def extract_iq_response_text(payload: Mapping[str, Any]) -> str:
    """Extract synthesis or extractive text from IQ REST and MCP envelopes."""
    texts = _iq_response_text_blocks(payload)
    return "\n".join(texts).strip()


def extract_iq_extracted_documents(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Parse exact documents from IQ's extractive ``extractedData`` output."""
    documents: list[dict[str, Any]] = []
    for text in _iq_response_text_blocks(payload):
        candidate = text.strip()
        if not (re.match(r"^\[\s*\{", candidate) or candidate.startswith("{")):
            continue
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError("Foundry IQ extractedData is not valid JSON") from exc
        if isinstance(decoded, Mapping):
            if isinstance(decoded.get("documents"), list):
                decoded = decoded["documents"]
            elif isinstance(decoded.get("value"), list):
                decoded = decoded["value"]
            else:
                decoded = [decoded]
        if not isinstance(decoded, list) or not all(
            isinstance(item, Mapping) for item in decoded
        ):
            raise ValueError("Foundry IQ extractedData must be a JSON document array")
        for item in decoded:
            document = dict(item)
            if not str(document.get("ref_id", "")).strip():
                raise ValueError("Every extractedData document needs a ref_id")
            documents.append(document)
    return documents


def fetch_iq_reference_documents(
    payload: Mapping[str, Any],
    clients_by_source: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Resolve references while preserving exact extractive output as context."""
    activities = {
        str(item.get("id")): item
        for item in payload.get("activity") or []
        if item.get("type") == "searchIndex"
    }
    extracted_documents = {
        _normalise_key(document["ref_id"]): document
        for document in extract_iq_extracted_documents(payload)
    }
    resolved: list[dict[str, Any]] = []
    for reference in payload.get("references") or []:
        if reference.get("type") != "searchIndex":
            continue
        activity = activities.get(str(reference.get("activitySource"))) or {}
        source_name = activity.get("knowledgeSourceName")
        source_data = reference.get("sourceData")
        ref_id = str(reference["id"])
        if _normalise_key(ref_id) in extracted_documents:
            document = dict(extracted_documents[_normalise_key(ref_id)])
            if isinstance(source_data, Mapping):
                for field in _IQ_IDENTITY_FIELDS:
                    value = source_data.get(field)
                    if field not in document and value not in (None, "", []):
                        document[field] = value
            resolution = "extracted_response"
        elif isinstance(source_data, Mapping) and source_data:
            document = dict(source_data)
            resolution = "inline_source_data"
        else:
            client = clients_by_source.get(source_name)
            if client is None:
                raise KeyError(
                    f"No Search client configured for IQ source {source_name!r}"
                )
            document = dict(client.get_document(key=reference["docKey"]))
            resolution = "search_document_fallback"
        resolved.append(
            {
                "ref_id": ref_id,
                "source_name": str(source_name),
                "doc_key": str(reference["docKey"]),
                "reranker_score": reference.get("rerankerScore"),
                "resolution": resolution,
                "document": document,
            }
        )
    return resolved


def _document_text(document: Mapping[str, Any]) -> str:
    preferred = (
        "id",
        "vwi_code",
        "title",
        "source_file",
        "page_number",
        "content",
        "excerpt",
        "raamopdracht_id",
        "bestemd_voor",
        "covered_vwi_ids",
        "geldigheidsgebied_postcodes",
        "geldigheidsduur_start",
        "geldigheidsduur_end",
        "permits_live_work",
        "omschrijving_bedieningshandelingen",
        "omschrijving_werkzaamheden",
        "bei_bls_aanwijzing",
        "geldigheidsgebied_prose",
        "crew_id",
        "name",
        "personeelsnummer",
        "functie",
        "aanwijzingen",
        "raamopdracht_ids",
        "home_base",
        "shift_status_demo",
    )
    parts: list[str] = []
    for field in preferred:
        value = document.get(field)
        if value not in (None, "", []):
            parts.append(
                f"{field}: "
                f"{json.dumps(value, ensure_ascii=False, default=str)}"
            )
    if not parts:
        parts.append(json.dumps(dict(document), ensure_ascii=False, default=str))
    return "\n".join(parts)


def format_iq_context(resolved_references: Iterable[Mapping[str, Any]]) -> str:
    """Build the groundedness context from the documents IQ actually cited."""
    blocks: list[str] = []
    for reference in resolved_references:
        document = reference["document"]
        label = (
            document.get("vwi_code")
            or document.get("raamopdracht_id")
            or document.get("crew_id")
            or document.get("title")
            or reference["doc_key"]
        )
        blocks.append(
            f"[ref_id:{reference['ref_id']}] {label}"
            f" | {reference['source_name']}\n{_document_text(document)}"
        )
    return "\n\n".join(blocks)


def response_token_usage(
    response: Any,
    semantic_requests: int = 1,
) -> dict[str, int]:
    """Extract model token usage from an OpenAI Responses API object."""
    usage = getattr(response, "usage", None)
    return {
        "model_input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "model_output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "agentic_retrieval_tokens": 0,
        "semantic_requests": int(semantic_requests),
    }


def iq_activity_metrics(activity: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Aggregate IQ model, retrieval, and stage-latency telemetry."""
    metrics = {
        "model_input_tokens": 0,
        "model_output_tokens": 0,
        "agentic_retrieval_tokens": 0,
        "semantic_requests": 0,
        "query_planning_ms": 0,
        "search_execution_ms_sum": 0,
        "answer_synthesis_ms": 0,
    }
    for item in activity:
        item_type = item.get("type")
        if item_type in {"modelQueryPlanning", "modelAnswerSynthesis"}:
            metrics["model_input_tokens"] += int(item.get("inputTokens", 0) or 0)
            metrics["model_output_tokens"] += int(item.get("outputTokens", 0) or 0)
        if item_type == "modelQueryPlanning":
            metrics["query_planning_ms"] += int(item.get("elapsedMs", 0) or 0)
        elif item_type == "modelAnswerSynthesis":
            metrics["answer_synthesis_ms"] += int(item.get("elapsedMs", 0) or 0)
        elif item_type == "searchIndex":
            metrics["semantic_requests"] += 1
            metrics["search_execution_ms_sum"] += int(item.get("elapsedMs", 0) or 0)
        elif item_type == "agenticReasoning":
            metrics["agentic_retrieval_tokens"] += int(
                item.get("reasoningTokens", 0) or 0
            )
    return metrics


def price_rates_from_env() -> dict[str, float | None]:
    """Read optional contract-specific rates without hard-coding public prices."""
    names = {
        "model_input_per_1m": "RAG_MODEL_INPUT_USD_PER_1M_TOKENS",
        "model_output_per_1m": "RAG_MODEL_OUTPUT_USD_PER_1M_TOKENS",
        "semantic_per_1k": "RAG_SEMANTIC_RANKER_USD_PER_1000_REQUESTS",
        "agentic_per_1m": "RAG_AGENTIC_RETRIEVAL_USD_PER_1M_TOKENS",
    }
    rates: dict[str, float | None] = {}
    for key, env_name in names.items():
        raw = os.getenv(env_name, "").strip()
        rates[key] = float(raw) if raw else None
    return rates


def estimate_cost_usd(
    usage: Mapping[str, int],
    rates: Mapping[str, float | None],
) -> dict[str, Any]:
    """Estimate observable request cost using explicitly configured rates."""
    components = {
        "model_input": (
            usage.get("model_input_tokens", 0),
            rates.get("model_input_per_1m"),
            1_000_000,
        ),
        "model_output": (
            usage.get("model_output_tokens", 0),
            rates.get("model_output_per_1m"),
            1_000_000,
        ),
        "semantic_ranker": (
            usage.get("semantic_requests", 0),
            rates.get("semantic_per_1k"),
            1_000,
        ),
        "agentic_retrieval": (
            usage.get("agentic_retrieval_tokens", 0),
            rates.get("agentic_per_1m"),
            1_000_000,
        ),
    }
    estimates: dict[str, float | None] = {}
    for name, (quantity, rate, divisor) in components.items():
        estimates[name] = (
            round(float(quantity) * float(rate) / divisor, 8)
            if rate is not None
            else None
        )
    configured_values = [value for value in estimates.values() if value is not None]
    return {
        "estimated_cost_usd": (
            round(sum(configured_values), 8) if configured_values else None
        ),
        "components_usd": estimates,
        "rates_complete": all(rate is not None for rate in rates.values()),
    }


def primitive(value: Any) -> Any:
    """Convert Azure/OpenAI SDK result models into JSON-compatible values."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
