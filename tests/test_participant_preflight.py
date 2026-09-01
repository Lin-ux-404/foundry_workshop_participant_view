from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "setup"))

from validate_participant_resources import (
    FOUNDRY_IQ_SETTINGS,
    STORAGE_SETTINGS,
    ResourceValidationError,
    foundry_iq_configured,
    load_settings,
    participant_agent_names,
    resolve_project_connection,
    storage_configured,
    validate_foundry_iq,
    validate_participant_agent_names_empty,
)


class ParticipantPreflightContractTests(unittest.TestCase):
    def write_environment_without(
        self,
        directory: str,
        excluded_names: tuple[str, ...],
    ) -> Path:
        environment_file = Path(directory) / ".env"
        excluded = set(excluded_names)
        lines = [
            line
            for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if not ("=" in line and line.split("=", 1)[0] in excluded)
        ]
        environment_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return environment_file

    def test_example_environment_satisfies_the_exact_resource_contract(self) -> None:
        settings = load_settings(ROOT / ".env.example")

        for name in (
            "WORKSHOP_RESOURCE_NAMESPACE",
            "FOUNDRY_MODEL",
            "AZURE_SEARCH_CONNECTION_NAME",
            "AZURE_SEARCH_INDEX",
            "FOUNDRY_IQ_KNOWLEDGE_BASE",
            "AZURE_STORAGE_CREW_CONTAINER",
        ):
            self.assertTrue(settings[name])

    def test_missing_exact_resource_setting_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment_file = Path(directory) / ".env"
            environment_file.write_text("TENANT_ID=placeholder\n", encoding="utf-8")

            with self.assertRaisesRegex(
                ResourceValidationError,
                "AZURE_SEARCH_INDEX",
            ):
                load_settings(environment_file)

    def test_optional_extensions_can_be_entirely_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment_file = self.write_environment_without(
                directory,
                FOUNDRY_IQ_SETTINGS + STORAGE_SETTINGS,
            )

            settings = load_settings(environment_file)

            self.assertFalse(foundry_iq_configured(settings))
            self.assertFalse(storage_configured(settings))

    def test_strict_mode_requires_the_requested_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment_file = self.write_environment_without(directory, FOUNDRY_IQ_SETTINGS)

            with self.assertRaisesRegex(ResourceValidationError, "Foundry IQ is required"):
                load_settings(environment_file, require_foundry_iq=True)

        with tempfile.TemporaryDirectory() as directory:
            environment_file = self.write_environment_without(directory, STORAGE_SETTINGS)

            with self.assertRaisesRegex(ResourceValidationError, "Blob Storage is required"):
                load_settings(environment_file, require_storage=True)

    def test_partial_optional_extension_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment_file = self.write_environment_without(directory, FOUNDRY_IQ_SETTINGS)
            with environment_file.open("a", encoding="utf-8") as stream:
                stream.write(f"{FOUNDRY_IQ_SETTINGS[0]}=partially-configured\n")

            with self.assertRaisesRegex(ResourceValidationError, "configuration is partial"):
                load_settings(environment_file)

    def test_connection_resolution_retries_an_empty_fresh_project_listing(self) -> None:
        expected = type("Connection", (), {"name": "search", "id": "connection-id"})()

        class Connections:
            def __init__(self) -> None:
                self.calls = 0

            def list(self):
                self.calls += 1
                return [] if self.calls == 1 else [expected]

        project = type("Project", (), {"connections": Connections()})()
        with patch("validate_participant_resources.time.sleep") as sleep:
            actual = resolve_project_connection(project, "search", "Search")

        self.assertIs(actual, expected)
        self.assertEqual(project.connections.calls, 2)
        sleep.assert_called_once_with(5)

    def test_participant_agent_names_follow_runtime_overrides_and_namespace_rules(self) -> None:
        settings = load_settings(ROOT / ".env.example")
        self.assertEqual(
            participant_agent_names(settings),
            (
                settings["DRAAD_RETRIEVER_AGENT"],
                settings["DRAAD_MATCHER_AGENT"],
                settings["DRAAD_REVIEWER_AGENT"],
                settings["DRAAD_QA_AGENT"],
            ),
        )

        derived = {
            key: value
            for key, value in settings.items()
            if not key.startswith("DRAAD_")
        }
        derived["WORKSHOP_RESOURCE_NAMESPACE"] = "Team 01"
        self.assertEqual(
            participant_agent_names(derived),
            (
                "draad-procedure-retriever-team-01",
                "draad-dispatch-matcher-team-01",
                "draad-dispatch-reviewer-team-01",
                "draad-qa-assistant-team-01",
            ),
        )

        duplicated = dict(settings)
        duplicated["DRAAD_QA_AGENT"] = duplicated["DRAAD_RETRIEVER_AGENT"]
        with self.assertRaisesRegex(ResourceValidationError, "four distinct values"):
            participant_agent_names(duplicated)

    def test_empty_agent_handoff_gate_rejects_only_exact_participant_names(self) -> None:
        settings = load_settings(ROOT / ".env.example")
        expected = participant_agent_names(settings)

        class Agents:
            def __init__(self, names: list[str]) -> None:
                self.names = names
                self.calls = 0

            def list(self, *, limit: int):
                self.limit = limit
                self.calls += 1
                return [type("Agent", (), {"name": name})() for name in self.names]

        unrelated = type("Project", (), {"agents": Agents(["unrelated-agent"])})()
        with patch("validate_participant_resources.time.sleep"):
            validate_participant_agent_names_empty(unrelated, settings, attempts=2)
        self.assertEqual(unrelated.agents.limit, 100)
        self.assertEqual(unrelated.agents.calls, 2)

        conflict = type("Project", (), {"agents": Agents([expected[0]])})()
        with patch("validate_participant_resources.time.sleep"):
            with self.assertRaisesRegex(
                ResourceValidationError,
                "participant agent names already contain deployed versions",
            ):
                validate_participant_agent_names_empty(conflict, settings, attempts=2)

    def test_empty_agent_handoff_gate_requires_stable_absence(self) -> None:
        settings = load_settings(ROOT / ".env.example")
        conflict_name = participant_agent_names(settings)[0]

        class EventuallyConsistentAgents:
            def __init__(self) -> None:
                self.observations = [[], [conflict_name], [], []]
                self.calls = 0

            def list(self, *, limit: int):
                names = self.observations[self.calls]
                self.calls += 1
                return [type("Agent", (), {"name": name})() for name in names]

        project = type("Project", (), {"agents": EventuallyConsistentAgents()})()
        with patch("validate_participant_resources.time.sleep") as sleep:
            validate_participant_agent_names_empty(project, settings, attempts=4)

        self.assertEqual(project.agents.calls, 4)
        self.assertEqual(sleep.call_count, 3)

    def test_foundry_iq_validation_checks_each_source_index_mapping(self) -> None:
        settings = load_settings(ROOT / ".env.example")
        source_index_pairs = [
            (
                settings["FOUNDRY_IQ_PROCEDURES_SOURCE"],
                settings["FOUNDRY_IQ_PROCEDURES_INDEX"],
            ),
            (
                settings["FOUNDRY_IQ_RAAMOPDRACHTEN_SOURCE"],
                settings["FOUNDRY_IQ_RAAMOPDRACHTEN_INDEX"],
            ),
            (
                settings["FOUNDRY_IQ_CREW_SOURCE"],
                settings["FOUNDRY_IQ_CREW_INDEX"],
            ),
        ]
        credential = type(
            "Credential",
            (),
            {"get_token": lambda self, _scope: type("Token", (), {"token": "token"})()},
        )()

        for wrong_pair_index in range(len(source_index_pairs)):
            responses = [
                {
                    "name": settings["FOUNDRY_IQ_KNOWLEDGE_BASE"],
                    "knowledgeSources": [
                        {"name": source_name}
                        for source_name, _ in source_index_pairs
                    ],
                },
                *[
                    {
                        "name": source_name,
                        "searchIndexParameters": {
                            "searchIndexName": (
                                "wrong-index"
                                if pair_index == wrong_pair_index
                                else index_name
                            )
                        },
                    }
                    for pair_index, (source_name, index_name) in enumerate(
                        source_index_pairs
                    )
                ],
            ]

            with self.subTest(source=source_index_pairs[wrong_pair_index][0]):
                with patch(
                    "validate_participant_resources._get_json",
                    side_effect=responses,
                ):
                    with self.assertRaisesRegex(
                        ResourceValidationError,
                        "targets 'wrong-index'",
                    ):
                        validate_foundry_iq(settings, credential)

    def test_foundry_iq_validation_uses_exact_source_membership(self) -> None:
        settings = load_settings(ROOT / ".env.example")
        source_names = [
            settings["FOUNDRY_IQ_PROCEDURES_SOURCE"],
            settings["FOUNDRY_IQ_RAAMOPDRACHTEN_SOURCE"],
            settings["FOUNDRY_IQ_CREW_SOURCE"],
        ]
        credential = type(
            "Credential",
            (),
            {"get_token": lambda self, _scope: type("Token", (), {"token": "token"})()},
        )()
        definition = {
            "name": settings["FOUNDRY_IQ_KNOWLEDGE_BASE"],
            "knowledgeSources": [{"name": f"{name}-wrong"} for name in source_names],
        }

        with patch(
            "validate_participant_resources._get_json",
            return_value=definition,
        ) as get_json:
            with self.assertRaisesRegex(
                ResourceValidationError,
                "source membership mismatch",
            ):
                validate_foundry_iq(settings, credential)

        self.assertEqual(get_json.call_count, 1)

    def test_foundry_iq_validation_fetches_exact_complete_contract(self) -> None:
        settings = load_settings(ROOT / ".env.example")
        source_index_pairs = [
            (
                settings["FOUNDRY_IQ_PROCEDURES_SOURCE"],
                settings["FOUNDRY_IQ_PROCEDURES_INDEX"],
            ),
            (
                settings["FOUNDRY_IQ_RAAMOPDRACHTEN_SOURCE"],
                settings["FOUNDRY_IQ_RAAMOPDRACHTEN_INDEX"],
            ),
            (
                settings["FOUNDRY_IQ_CREW_SOURCE"],
                settings["FOUNDRY_IQ_CREW_INDEX"],
            ),
        ]
        credential = type(
            "Credential",
            (),
            {"get_token": lambda self, _scope: type("Token", (), {"token": "token"})()},
        )()
        responses = [
            {
                "name": settings["FOUNDRY_IQ_KNOWLEDGE_BASE"],
                "knowledgeSources": [
                    {"name": source_name} for source_name, _ in source_index_pairs
                ],
            },
            *[
                {
                    "name": source_name,
                    "searchIndexParameters": {"searchIndexName": index_name},
                }
                for source_name, index_name in source_index_pairs
            ],
        ]

        with patch(
            "validate_participant_resources._get_json",
            side_effect=responses,
        ) as get_json:
            validate_foundry_iq(settings, credential)

        self.assertEqual(get_json.call_count, 4)

    def test_powershell_gate_matches_the_frontend_node_floor(self) -> None:
        preflight = (ROOT / "setup" / "Test-WorkshopPrerequisites.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('[version]"20.9.0"', preflight)
        self.assertIn('"WORKSHOP_RESOURCE_NAMESPACE"', preflight)
        self.assertIn('"AZURE_SEARCH_CONNECTION_NAME"', preflight)
        required_block = preflight.split("$requiredNames = @(", 1)[1].split(")", 1)[0]
        self.assertNotIn('"FOUNDRY_IQ_KNOWLEDGE_BASE"', required_block)
        self.assertNotIn('"AZURE_STORAGE_ACCOUNT_URL"', required_block)
        self.assertIn("RequireFoundryIQ", preflight)
        self.assertIn("RequireStorage", preflight)
        self.assertIn("RequireEmptyAgentNames", preflight)
        self.assertIn('"--require-foundry-iq"', preflight)
        self.assertIn('"--require-storage"', preflight)
        self.assertIn('"--require-empty-agent-names"', preflight)
        self.assertIn("$storageConfigured", preflight)
        self.assertIn("validate_participant_resources.py", preflight)

    def test_optional_extension_contract_is_documented_for_participants(self) -> None:
        environment_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        participant_setup = (ROOT / "setup" / "README.md").read_text(encoding="utf-8")
        contents = [environment_example, participant_setup]

        internal_template = ROOT / "public-package" / "setup-README.md"
        if internal_template.is_file():
            contents.append(internal_template.read_text(encoding="utf-8"))

        for content in contents:
            self.assertIn("all-or-none", content)
            self.assertIn("RequireFoundryIQ", content)
            self.assertIn("RequireStorage", content)

    def test_operator_seeding_keeps_participant_agent_names_empty(self) -> None:
        setup_guide = (ROOT / "setup" / "README.md").read_text(encoding="utf-8")
        if "## Seed the environment" in setup_guide:
            seed_section = setup_guide.split("## Seed the environment", 1)[1].split(
                "## Authentication model", 1
            )[0]
            self.assertIn("setup_search.py --all", seed_section)
            self.assertIn("RequireEmptyAgentNames", seed_section)
            self.assertIn("Do **not** run `app/scripts/deploy_agents.py`", seed_section)
        else:
            self.assertIn("RequireEmptyAgentNames", setup_guide)
            self.assertNotIn("deploy_agents.py", setup_guide)

    def test_resource_validator_checks_exact_assets_not_generic_access(self) -> None:
        validator = (ROOT / "setup" / "validate_participant_resources.py").read_text(
            encoding="utf-8"
        )

        for required_operation in (
            "project.deployments.get",
            "project.connections.list",
            "client.get_document_count",
            '"knowledgebases"',
            '"knowledgesources"',
            '"searchIndexName"',
            "get_container_properties",
            "get_blob_properties",
        ):
            self.assertIn(required_operation, validator)

    def test_fresh_project_assets_never_use_singular_connection_get(self) -> None:
        participant_assets = (
            ROOT / "setup" / "validate_participant_resources.py",
            ROOT / "app" / "participant" / "1-build-ai-components.ipynb",
            ROOT / "labs" / "azure-ai-agents" / "5-agents-aisearch.ipynb",
        )
        for path in participant_assets:
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn("connections.get(", content)
                self.assertIn("connections.list()", content)

    def test_evaluation_notebooks_accept_injected_environment(self) -> None:
        lab = ROOT / "labs" / "observability-and-evaluation"
        for name in (
            "1-telemetry.ipynb",
            "2-agent-evaluation.ipynb",
            "3-agent-evaluation-with-function-tools.ipynb",
            "4-tool-call-accuracy-evaluation.ipynb",
            "5-red-team-security-testing.ipynb",
        ):
            notebook = json.loads((lab / name).read_text(encoding="utf-8"))
            source = "".join(
                "".join(cell.get("source", []))
                if isinstance(cell.get("source", []), list)
                else str(cell.get("source", ""))
                for cell in notebook.get("cells", [])
                if cell.get("cell_type") == "code"
            )
            with self.subTest(notebook=name):
                self.assertIn("def load_repo_env", source)
                self.assertNotIn("Repository-root .env not found", source)
                self.assertIn("return None", source)


if __name__ == "__main__":
    unittest.main()
