from __future__ import annotations

import json
import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, patch

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`.*")

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "app" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import main
from models.responses import StepEvent
from utils.parsing import IncidentPayloadError


def _sse_events(response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


class ApplicationHttpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main.app)

    def test_health_and_empty_message_contracts(self) -> None:
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        response = self.client.post("/api/chat", json={"message": "   "})
        self.assertEqual(response.status_code, 400)

    def test_structured_input_error_is_reported_as_422(self) -> None:
        with patch.object(
            main,
            "run_chat",
            new=AsyncMock(side_effect=IncidentPayloadError("invalid incident")),
        ):
            response = self.client.post("/api/chat", json={"message": "{}"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "invalid incident")

    def test_stream_preserves_sse_result_payload(self) -> None:
        async def fake_stream(_message: str):
            yield StepEvent(
                type="result",
                agent="qa_assistant",
                summary="Q&A response",
                data={"type": "qa", "response": "grounded", "sources": ["E-85"]},
            )

        with patch.object(main, "run_chat_stream", new=fake_stream):
            response = self.client.post(
                "/api/chat/stream",
                json={"message": "What is E-85?"},
            )
        self.assertEqual(response.status_code, 200)
        events = _sse_events(response)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["sources"], ["E-85"])

    def test_stream_failure_returns_reference_without_exception_text(self) -> None:
        async def failing_stream(_message: str):
            if False:
                yield
            raise RuntimeError("super-secret provider detail")

        with self.assertLogs(main.__name__, level="ERROR"):
            with patch.object(main, "run_chat_stream", new=failing_stream):
                response = self.client.post(
                    "/api/chat/stream",
                    json={"message": "trigger"},
                )
        event = _sse_events(response)[0]
        self.assertEqual(event["type"], "error")
        self.assertIn("Reference:", event["summary"])
        self.assertNotIn("super-secret", event["summary"])


if __name__ == "__main__":
    unittest.main()
