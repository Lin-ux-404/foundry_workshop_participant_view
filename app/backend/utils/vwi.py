"""Canonical VWI identifiers and coverage matching.

VWI work-mode suffixes are safety-significant. A bare base code may only expand
to a suffixed code when the indexed corpus contains exactly one variant for that
base. Bases such as E-22 and E-40 have both energized and de-energized variants,
so they must never be treated as interchangeable.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_VWI_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "vwi_catalog.json"
_VWI_CODE_RE = re.compile(
    r"\bE[-_]\d{2}(?:[-_](?:onder[-_]sp|sp[-_]loos))?\b",
    re.IGNORECASE,
)


def normalize_vwi_code(raw: str) -> str:
    """Return the canonical spelling for a syntactically valid VWI code."""
    match = _VWI_CODE_RE.fullmatch(raw.strip())
    if not match:
        return ""
    parts = match.group(0).upper().replace("_", "-").split("-")
    if len(parts) == 2:
        return f"{parts[0]}-{parts[1]}"
    return f"{parts[0]}-{parts[1]}-{'-'.join(p.lower() for p in parts[2:])}"


@lru_cache(maxsize=1)
def load_indexed_vwi_ids() -> frozenset[str]:
    """Load the canonical runtime catalogue from versioned synthetic data.

    The participant package does not need to ship the source PDFs. Keeping the
    safety-significant identifiers in a small data contract makes deterministic
    matching independent of the optional document corpus while the indexer can
    continue to derive each indexed document's code from its filename.
    """
    try:
        payload = json.loads(_VWI_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Unable to load VWI catalogue from {_VWI_CATALOG_PATH}"
        ) from exc

    raw_ids = payload.get("vwi_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        raise RuntimeError("VWI catalogue must contain a non-empty 'vwi_ids' list")

    ids: set[str] = set()
    for raw in raw_ids:
        if not isinstance(raw, str):
            raise RuntimeError("VWI catalogue identifiers must be strings")
        normalized = normalize_vwi_code(raw)
        if not normalized or normalized != raw:
            raise RuntimeError(f"Invalid canonical VWI identifier: {raw!r}")
        if normalized in ids:
            raise RuntimeError(f"Duplicate VWI identifier: {normalized}")
        ids.add(normalized)
    return frozenset(ids)


def vwi_matches(
    selected: str,
    covered: str,
    *,
    catalogue: frozenset[str] | None = None,
) -> bool:
    """Return whether a selected VWI is covered without crossing work modes.

    Exact identifiers always match. A bare selected base can match a suffixed
    covered identifier only if the catalogue contains exactly one variant for
    that base. This permits defensive recovery from a dropped suffix only when
    doing so is unambiguous.
    """
    selected_code = normalize_vwi_code(selected)
    covered_code = normalize_vwi_code(covered)
    if not selected_code or not covered_code:
        return False
    if selected_code == covered_code:
        return True
    if selected_code.count("-") != 1:
        return False

    prefix = selected_code + "-"
    variants = {
        code
        for code in (catalogue or load_indexed_vwi_ids())
        if code.startswith(prefix)
    }
    return len(variants) == 1 and covered_code in variants


def is_live_work_vwi(vwi_id: str) -> bool:
    """Return whether a VWI explicitly represents energized work."""
    code = normalize_vwi_code(vwi_id)
    return code.endswith("-onder-sp") or code in {"E-45", "E-66"}
