from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from utils.vwi import load_indexed_vwi_ids, normalize_vwi_code


NOTEBOOK_PATH = ROOT / "app" / "participant" / "1-build-ai-components.ipynb"


def _cell_source(cell: dict) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else str(source)


class ParticipantApplicationContractTests(unittest.TestCase):
    def test_notebook_is_an_output_free_four_task_starter(self) -> None:
        notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(notebook["nbformat"], 4)

        text = "\n".join(_cell_source(cell) for cell in notebook["cells"])
        for task in range(1, 5):
            self.assertIn(f"Participant task {task}", text)
            self.assertIn(f"TODO {task}", text)
        for name in (
            "QA_NAME",
            "RETRIEVER_NAME",
            "MATCHER_NAME",
            "REVIEWER_NAME",
        ):
            self.assertIn(name, text)
        self.assertIn("with_project_retry", text)
        self.assertIn("project not found", text.lower())
        self.assertIn("with_agent_retry", text)
        self.assertIn("APIStatusError", text)
        self.assertIn("agent version is still propagating", text.lower())
        self.assertIn("†source", text)
        self.assertIn("literal `Sources:` list", text)
        for contract_field in (
            '"matched_crew": null',
            '"matched_raamopdracht_id": null',
            '"coverage_status": null',
            '"operational_action": null',
            '"raamopdracht_scope_excerpts"',
            '"review_status": "pass|revise|flagged_for_human_review"',
            '"feedback_for_matcher"',
        ):
            self.assertIn(contract_field, text)

        for index, cell in enumerate(notebook["cells"], start=1):
            if cell.get("cell_type") != "code":
                continue
            self.assertIsNone(cell.get("execution_count"), index)
            self.assertFalse(cell.get("outputs"), index)
            compile(
                _cell_source(cell),
                f"{NOTEBOOK_PATH}:cell-{index}",
                "exec",
                flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            )

    def test_runtime_imports_names_without_reference_prompt_modules(self) -> None:
        names_source = (BACKEND_ROOT / "agent_names.py").read_text(encoding="utf-8")
        self.assertNotIn("PROMPT", names_source)
        self.assertNotIn("from agents", names_source)

        for relative in ("workflows/qa.py", "workflows/dispatch.py"):
            source = (BACKEND_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("from agent_names import", source)
            self.assertIsNone(
                re.search(r"from\s+agents(?:\.|\s)", source),
                f"{relative} imports the internal prompt solution",
            )

    def test_playbook_defines_the_supplied_and_participant_boundary(self) -> None:
        playbook = (ROOT / "app" / "PLAYBOOK.md").read_text(encoding="utf-8")
        for phrase in (
            "What is supplied and what you build",
            "Participant-owned component",
            "Completion evidence",
            "Official references",
            "1-build-ai-components.ipynb",
        ):
            self.assertIn(phrase, playbook)

    def test_versioned_catalogue_drives_runtime_without_pdf_dependency(self) -> None:
        catalog_path = ROOT / "app" / "data" / "vwi_catalog.json"
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(frozenset(payload["vwi_ids"]), load_indexed_vwi_ids())
        self.assertIn("E-22-onder-sp", payload["vwi_ids"])
        self.assertIn("E-22-sp-loos", payload["vwi_ids"])

    def test_internal_pdf_catalogue_matches_versioned_runtime_contract(self) -> None:
        docs_dir = ROOT / "app" / "docs" / "VWI"
        if not docs_dir.is_dir():
            self.skipTest("participant package intentionally omits source PDFs")

        filename_re = re.compile(
            r"\bE[-_]\d{2}(?:[-_](?:onder[-_]sp|sp[-_]loos))?\b",
            re.IGNORECASE,
        )
        from_pdfs = {
            normalize_vwi_code(match.group(0))
            for path in docs_dir.glob("*.pdf")
            if (match := filename_re.search(path.name))
        }
        self.assertEqual(from_pdfs, set(load_indexed_vwi_ids()))


if __name__ == "__main__":
    unittest.main()
