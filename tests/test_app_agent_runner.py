from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "app" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from utils.agent_runner import _citation_labels
from utils.agent_runner import _transient_agent_error


class AgentRunnerCitationTests(unittest.TestCase):
    def test_only_retryable_statuses_or_propagation_errors_are_retried(self) -> None:
        transient = RuntimeError("Agent not found while the new version propagates")
        setattr(transient, "status_code", 404)
        bad_request = RuntimeError("Prompt schema is invalid")
        setattr(bad_request, "status_code", 400)

        self.assertTrue(_transient_agent_error(transient))
        self.assertTrue(_transient_agent_error(RuntimeError("status code: 503")))
        self.assertFalse(_transient_agent_error(bad_request))

    def test_citation_titles_are_deduplicated(self) -> None:
        update = SimpleNamespace(
            contents=[
                SimpleNamespace(
                    annotations=[
                        {
                            "type": "citation",
                            "title": "E-85 — E-85",
                            "url": "https://search.example/",
                        },
                        {
                            "type": "citation",
                            "title": "E-85 — E-85",
                            "url": "https://search.example/",
                        },
                    ]
                )
            ]
        )
        self.assertEqual(_citation_labels(update), ["E-85 — E-85"])

    def test_citation_url_is_used_when_title_is_missing(self) -> None:
        annotation = SimpleNamespace(
            type="citation",
            title=None,
            url="https://search.example/source",
        )
        update = SimpleNamespace(
            contents=[SimpleNamespace(annotations=[annotation])]
        )
        self.assertEqual(
            _citation_labels(update),
            ["https://search.example/source"],
        )

    def test_non_citation_annotations_are_ignored(self) -> None:
        update = SimpleNamespace(
            contents=[
                SimpleNamespace(
                    annotations=[{"type": "other", "title": "ignore"}]
                )
            ]
        )
        self.assertEqual(_citation_labels(update), [])


if __name__ == "__main__":
    unittest.main()
