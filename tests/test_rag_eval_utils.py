from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "labs"
    / "observability-and-evaluation"
    / "rag_eval_utils.py"
)
SPEC = importlib.util.spec_from_file_location("rag_eval_utils", MODULE_PATH)
assert SPEC and SPEC.loader
rag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rag)


class RagEvalUtilsTests(unittest.TestCase):
    def test_versioned_dataset_contract(self) -> None:
        path = MODULE_PATH.parent / "data" / "rag-evaluation-cases-v2.json"
        metadata, search_cases = rag.load_cases(path, "azure_ai_search")
        _, iq_cases = rag.load_cases(path, "foundry_iq")

        self.assertEqual(metadata["schema_version"], "1.0")
        self.assertEqual(
            metadata["answer_contract_version"],
            "search-required-parts-v2",
        )
        self.assertEqual(metadata["dataset_id"], "synthetic-grid-rag-e2e-v2")
        self.assertEqual(len(search_cases), 3)
        self.assertEqual(len(iq_cases), 2)
        self.assertEqual(
            {
                case["case_id"]: [
                    part["id"] for part in case["required_answer_parts"]
                ]
                for case in search_cases
            },
            {
                "SEARCH-E85": [
                    "assignment_and_competence",
                    "risk_checks",
                    "live_parts_ppe_tools",
                    "replacement_sequence",
                    "handover",
                ],
                "SEARCH-E22-LIVE": [
                    "procedure_variant",
                    "assignment_and_competence",
                    "risk_and_ppe",
                    "removal_controls",
                    "installation_controls",
                    "handover",
                ],
                "SEARCH-E48": [
                    "preparation",
                    "risk_controls",
                    "supervision",
                    "completion",
                ],
            },
        )
        self.assertTrue(
            all(
                set(case["source_filters"])
                == {"procedure", "authorization", "crew"}
                for case in iq_cases
            )
        )
        e22 = next(case for case in search_cases if case["case_id"] == "SEARCH-E22-LIVE")
        self.assertNotIn("execution_controls", {
            part["id"] for part in e22["required_answer_parts"]
        })
        self.assertIn("Removal requires", e22["ground_truth"])
        self.assertIn("Installation requires", e22["ground_truth"])
        self.assertEqual(
            [group["name"] for group in e22["expected_evidence_groups"]],
            ["procedure-page-1", "procedure-page-2", "procedure-page-3"],
        )

    def test_search_dataset_requires_the_versioned_answer_contract(self) -> None:
        def payload(contract_version: str | None) -> dict:
            result = {
                "schema_version": "1.0",
                "answer_contract_version": contract_version,
                "cases": [
                    {
                        "case_id": "SEARCH-CONTRACT",
                        "target": "azure_ai_search",
                        "query": "A synthetic query",
                        "ground_truth": "A synthetic answer",
                        "required_answer_parts": [
                            {"id": "preparation", "description": "Preparation"}
                        ],
                        "expected_evidence_groups": [
                            {"name": "procedure", "any_of": ["E-48"]}
                        ],
                    }
                ],
            }
            if contract_version is None:
                result.pop("answer_contract_version")
            return result

        for contract_version in (None, "search-required-parts-v1"):
            with self.subTest(contract_version=contract_version):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "cases.json"
                    path.write_text(
                        json.dumps(payload(contract_version)), encoding="utf-8"
                    )
                    with self.assertRaisesRegex(
                        ValueError, "Unsupported Search answer contract"
                    ):
                        rag.load_cases(path, "azure_ai_search")

    def test_search_dataset_rejects_missing_or_malformed_answer_parts(self) -> None:
        malformed_parts = {
            "missing": None,
            "empty": [],
            "not-an-object": ["preparation"],
            "missing-id": [{"description": "Preparation"}],
            "missing-description": [{"id": "preparation"}],
            "non-string-id": [{"id": 7, "description": "Preparation"}],
            "non-string-description": [{"id": "preparation", "description": 7}],
            "invalid-id": [{"id": "Preparation step", "description": "Preparation"}],
            "blank-description": [{"id": "preparation", "description": "  "}],
        }
        for name, answer_parts in malformed_parts.items():
            payload = {
                "schema_version": "1.0",
                "answer_contract_version": "search-required-parts-v2",
                "cases": [
                    {
                        "case_id": f"SEARCH-{name}",
                        "target": "azure_ai_search",
                        "query": "A synthetic query",
                        "ground_truth": "A synthetic answer",
                        "expected_evidence_groups": [
                            {"name": "procedure", "any_of": ["E-48"]}
                        ],
                        "required_answer_parts": answer_parts,
                    }
                ],
            }
            if name == "missing":
                payload["cases"][0].pop("required_answer_parts")
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "cases.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, r"answer[_ ]part"):
                        rag.load_cases(path, "azure_ai_search")

    def test_search_dataset_rejects_duplicate_answer_part_ids(self) -> None:
        payload = {
            "schema_version": "1.0",
            "answer_contract_version": "search-required-parts-v2",
            "cases": [
                {
                    "case_id": "SEARCH-DUPLICATE",
                    "target": "azure_ai_search",
                    "query": "A synthetic query",
                    "ground_truth": "A synthetic answer",
                    "required_answer_parts": [
                        {"id": "preparation", "description": "Preparation"},
                        {"id": "preparation", "description": "Still preparation"},
                    ],
                    "expected_evidence_groups": [
                        {"name": "procedure", "any_of": ["E-48"]}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be unique"):
                rag.load_cases(path, "azure_ai_search")

    def test_iq_dataset_contract_requires_all_source_filters(self) -> None:
        payload = {
            "schema_version": "1.0",
            "cases": [
                {
                    "case_id": "IQ-INCOMPLETE",
                    "target": "foundry_iq",
                    "query": "A synthetic query",
                    "ground_truth": "A synthetic answer",
                    "expected_evidence_groups": [
                        {"name": "procedure", "any_of": ["E-85"]}
                    ],
                    "source_filters": {
                        "procedure": "vwi_code eq 'E-85'",
                        "authorization": "id eq 'RA-1'",
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "crew"):
                rag.load_cases(path, "foundry_iq")

    def test_group_recall_accepts_equivalent_keys(self) -> None:
        result = rag.retrieval_recall(
            [
                {"name": "procedure", "any_of": ["E-85", "e85"]},
                {"name": "crew", "any_of": ["crew-001-K-de-Vries"]},
            ],
            {"e-85", "CREW-001-k-de-vries"},
        )

        self.assertEqual(result["recall"], 1.0)
        self.assertEqual(result["matched_groups"], 2)

    def test_document_identity_does_not_leak_relationship_keys(self) -> None:
        crew = {
            "id": "crew-001-K-de-Vries",
            "crew_id": "crew-001-K-de-Vries",
            "raamopdracht_ids": ["RA-NHN-0101"],
        }
        authorization = {
            "id": "RA-NHN-0101",
            "raamopdracht_id": "RA-NHN-0101",
            "covered_vwi_ids": ["E-85"],
        }

        self.assertEqual(
            rag.document_evidence_keys(crew),
            {"crew-001-k-de-vries"},
        )
        self.assertEqual(
            rag.document_evidence_keys(authorization),
            {"ra-nhn-0101"},
        )
        self.assertIn(
            "e-22-onder-sp#p1",
            rag.document_evidence_keys(
                {"vwi_code": "E-22-onder-sp", "page_number": 1}
            ),
        )

    def test_citation_metrics_distinguish_coverage_and_validity(self) -> None:
        answer = (
            "The first claim is supported. [S1]\n"
            "The second claim has no citation.\n"
            "The third claim cites an unknown source. [S9]"
        )
        result = rag.citation_metrics(answer, {"S1", "S2"})

        self.assertEqual(result["factual_units"], 3)
        self.assertEqual(result["coverage"], 2 / 3)
        self.assertEqual(result["valid_coverage"], 1 / 3)
        self.assertEqual(result["validity"], 0.5)
        self.assertEqual(result["invalid_citations"], ["s9"])
        self.assertEqual(
            [claim["status"] for claim in result["claims"]],
            ["resolved", "uncited", "unresolved"],
        )

    def test_iq_citation_ids_accept_raw_reference_ids(self) -> None:
        result = rag.validate_cited_answer(
            "The authorization is active. [ref_id:7]",
            {"7"},
        )

        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["valid_coverage"], 1.0)
        self.assertEqual(result["validity"], 1.0)
        self.assertEqual(result["claims"][0]["valid_citation_ids"], ["ref_id:7"])

    def test_render_cited_answer_emits_inline_references(self) -> None:
        result = rag.validate_cited_answer(
            "The first claim is supported. [ref_id:1]\n"
            "The second claim has no citation.",
            {"1"},
        )
        rendered = rag.render_cited_answer(result)

        self.assertIn("- The first claim is supported. [ref_id:1]", rendered)
        self.assertIn("- The second claim has no citation.", rendered)

        search_result = rag.validate_cited_answer(
            {
                "claims": [
                    {
                        "text": "The Search claim is supported.",
                        "source_ids": ["s1", "[s2]"],
                    }
                ],
                "insufficient_evidence": [],
            },
            {"S1", "S2"},
        )
        self.assertEqual(
            rag.render_cited_answer(search_result),
            "- The Search claim is supported. [S1] [S2]",
        )

    def test_search_generation_documents_follow_top_ranked_vwi_variant(self) -> None:
        ranked = [
            {"id": "under-p2", "vwi_code": "E-22-onder-sp"},
            {"id": "deenergized-p2", "vwi_code": "E-22-sp-loos"},
            {"id": "under-p3", "vwi_code": "e-22-ONDER-sp"},
            {"id": "other", "vwi_code": "E-23-onder-sp"},
        ]

        selected = rag.select_coherent_search_documents(ranked)

        self.assertEqual(
            [document["id"] for document in selected],
            ["under-p2", "under-p3"],
        )

    def test_search_generation_documents_fail_closed_without_top_code(self) -> None:
        ranked = [
            {"id": "unclassified", "title": "General guidance"},
            {"id": "coded", "vwi_code": "E-85"},
        ]

        with self.assertRaisesRegex(ValueError, "no vwi_code"):
            rag.select_coherent_search_documents(ranked)

    def test_search_generation_documents_fail_closed_when_empty(self) -> None:
        with self.assertRaisesRegex(ValueError, "no documents"):
            rag.select_coherent_search_documents([])

    def test_search_generation_selection_uses_rank_one_not_benchmark_labels(self) -> None:
        ranked = [
            {"id": "deenergized", "vwi_code": "E-22-sp-loos"},
            {"id": "energized", "vwi_code": "E-22-onder-sp"},
            {"id": "deenergized-2", "vwi_code": " e-22-SP-loos "},
        ]

        selected = rag.select_coherent_search_documents(ranked)

        self.assertEqual(
            [document["id"] for document in selected],
            ["deenergized", "deenergized-2"],
        )

    def test_ranked_evidence_metrics_expose_wrong_top_variant(self) -> None:
        expected = [{"name": "procedure", "any_of": ["E-22-onder-sp"]}]
        ranked = [
            {"id": "wrong", "vwi_code": "E-22-sp-loos"},
            {"id": "right-p2", "vwi_code": "E-22-onder-sp"},
            {"id": "other", "vwi_code": "E-23-onder-sp"},
            {"id": "right-p3", "vwi_code": "E-22-onder-sp"},
        ]

        metrics = rag.ranked_evidence_metrics(expected, ranked)

        self.assertEqual(metrics["precision"], 0.5)
        self.assertFalse(metrics["top_1_match"])
        self.assertEqual(metrics["first_match_rank"], 2)
        self.assertEqual(metrics["reciprocal_rank"], 0.5)
        self.assertEqual(metrics["matched_ranks"], [2, 4])

    def test_prepared_search_context_rebuilds_source_ids_after_filtering(self) -> None:
        ranked = [
            {
                "id": "under-p2",
                "vwi_code": "E-22-onder-sp",
                "content": "Energized removal controls.",
            },
            {
                "id": "deenergized-p2",
                "vwi_code": "E-22-sp-loos",
                "content": "De-energized controls must stay excluded.",
            },
            {
                "id": "under-p3",
                "vwi_code": "E-22-onder-sp",
                "content": "Energized installation controls.",
            },
        ]

        prepared = rag.prepare_coherent_search_context(ranked)

        self.assertEqual(prepared["primary_vwi_code"], "E-22-onder-sp")
        self.assertEqual(prepared["valid_source_ids"], {"S1", "S2"})
        self.assertIn("[S1] E-22-onder-sp", prepared["context"])
        self.assertIn("[S2] E-22-onder-sp", prepared["context"])
        self.assertNotIn("De-energized controls", prepared["context"])
        invalid = rag.validate_cited_answer(
            "Excluded evidence. [S3]", prepared["valid_source_ids"]
        )
        self.assertEqual(invalid["validity"], 0.0)

    def test_structured_cited_answer_round_trips_for_generation(self) -> None:
        result = rag.validate_cited_answer(
            {
                "claims": [
                    {
                        "text": "The authorization covers E-85.",
                        "source_ids": ["ref_id:2"],
                    },
                    {
                        "text": "The crew is available.",
                        "source_ids": ["ref_id:9"],
                    },
                ],
                "insufficient_evidence": ["The required permit was not found."],
            },
            {"2"},
        )

        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["valid_coverage"], 0.5)
        self.assertEqual(result["invalid_citations"], ["ref_id:9"])
        self.assertEqual(
            result["insufficient_evidence"],
            ["The required permit was not found."],
        )
        self.assertEqual(result["required_answer_part_ids"], [])
        self.assertEqual(result["answered_answer_part_ids"], [])
        self.assertIsNone(result["answer_part_coverage"])
        self.assertEqual(
            rag.render_cited_answer(result),
            "- The authorization covers E-85. [ref_id:2]\n"
            "- The crew is available. [ref_id:9]",
        )

    def test_structured_cited_answer_rejects_empty_source_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one source_id"):
            rag.validate_cited_answer(
                {
                    "claims": [
                        {
                            "text": "This claim has no supporting reference.",
                            "source_ids": [],
                        }
                    ],
                    "insufficient_evidence": [],
                },
                {"S1"},
            )

    def test_required_answer_parts_accept_complete_accounting(self) -> None:
        required_parts = [
            "preparation",
            "risk_controls",
            "supervision",
            "completion",
        ]
        result = rag.validate_cited_answer(
            {
                "claims": [
                    {
                        "answer_part_id": "preparation",
                        "text": "Preparation is required.",
                        "source_ids": ["S1"],
                    },
                    {
                        "answer_part_id": "risk_controls",
                        "text": "Cable-protection controls are required.",
                        "source_ids": ["S1"],
                    },
                    {
                        "answer_part_id": "supervision",
                        "text": "The work must remain supervised.",
                        "source_ids": ["S2"],
                    },
                ],
                "insufficient_evidence": ["completion"],
            },
            {"S1", "S2"},
            required_answer_part_ids=required_parts,
        )

        self.assertEqual(result["required_answer_part_ids"], required_parts)
        self.assertEqual(
            result["answered_answer_part_ids"],
            ["preparation", "risk_controls", "supervision"],
        )
        self.assertEqual(result["insufficient_evidence"], ["completion"])
        self.assertEqual(result["answer_part_coverage"], 0.75)
        self.assertEqual(result["valid_coverage"], 1.0)
        self.assertEqual(result["validity"], 1.0)

    def test_required_answer_parts_reject_missing_or_unknown_claim_part(self) -> None:
        required_parts = ["preparation", "completion"]
        for name, claim in {
            "missing": {
                "text": "Preparation is required.",
                "source_ids": ["S1"],
            },
            "unknown": {
                "answer_part_id": "freeform",
                "text": "Preparation is required.",
                "source_ids": ["S1"],
            },
        }.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "answer_part_id"):
                    rag.validate_cited_answer(
                        {
                            "claims": [claim],
                            "insufficient_evidence": ["completion"],
                        },
                        {"S1"},
                        required_answer_part_ids=required_parts,
                    )

    def test_required_answer_parts_reject_unknown_or_freeform_insufficiency(
        self,
    ) -> None:
        required_parts = [
            "preparation",
            "risk_controls",
            "supervision",
            "completion",
        ]
        prior_e48_failure = (
            "Whether E-48 specifically applies only to third-party civil work near "
            "low-voltage cables is not stated verbatim in the supplied sources."
        )
        for insufficient_value in ("unknown_part", prior_e48_failure):
            with self.subTest(insufficient_value=insufficient_value):
                with self.assertRaisesRegex(
                    ValueError,
                    "Unknown insufficient_evidence answer-part IDs",
                ):
                    rag.validate_cited_answer(
                        {
                            "claims": [
                                {
                                    "answer_part_id": "preparation",
                                    "text": "Preparation is required.",
                                    "source_ids": ["S1"],
                                }
                            ],
                            "insufficient_evidence": [insufficient_value],
                        },
                        {"S1"},
                        required_answer_part_ids=required_parts,
                    )

    def test_required_answer_parts_reject_duplicate_insufficiency(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain duplicates"):
            rag.validate_cited_answer(
                {
                    "claims": [
                        {
                            "answer_part_id": "preparation",
                            "text": "Preparation is required.",
                            "source_ids": ["S1"],
                        }
                    ],
                    "insufficient_evidence": ["completion", "completion"],
                },
                {"S1"},
                required_answer_part_ids=["preparation", "completion"],
            )

    def test_required_answer_parts_reject_answered_and_insufficient_overlap(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "both answered and insufficient"):
            rag.validate_cited_answer(
                {
                    "claims": [
                        {
                            "answer_part_id": "preparation",
                            "text": "Preparation is required.",
                            "source_ids": ["S1"],
                        }
                    ],
                    "insufficient_evidence": ["preparation", "completion"],
                },
                {"S1"},
                required_answer_part_ids=["preparation", "completion"],
            )

    def test_required_answer_parts_reject_unaccounted_part(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "Required answer parts are unaccounted for: completion"
        ):
            rag.validate_cited_answer(
                {
                    "claims": [
                        {
                            "answer_part_id": "preparation",
                            "text": "Preparation is required.",
                            "source_ids": ["S1"],
                        }
                    ],
                    "insufficient_evidence": [],
                },
                {"S1"},
                required_answer_part_ids=["preparation", "completion"],
            )

    def test_required_answer_parts_allow_multiple_claims_for_one_part(self) -> None:
        result = rag.validate_cited_answer(
            {
                "claims": [
                    {
                        "answer_part_id": "preparation",
                        "text": "The work area must be identified.",
                        "source_ids": ["S1"],
                    },
                    {
                        "answer_part_id": "preparation",
                        "text": "The cable location must be checked.",
                        "source_ids": ["S2"],
                    },
                    {
                        "answer_part_id": "completion",
                        "text": "The work area must be handed over safely.",
                        "source_ids": ["S2"],
                    },
                ],
                "insufficient_evidence": [],
            },
            {"S1", "S2"},
            required_answer_part_ids=["preparation", "completion"],
        )

        self.assertEqual(result["factual_units"], 3)
        self.assertEqual(
            [claim["answer_part_id"] for claim in result["claims"]],
            ["preparation", "preparation", "completion"],
        )
        self.assertEqual(
            result["answered_answer_part_ids"], ["preparation", "completion"]
        )
        self.assertEqual(result["answer_part_coverage"], 1.0)
        self.assertEqual(result["insufficient_evidence"], [])

    def test_e22_removal_without_installation_is_unaccounted(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Required answer parts are unaccounted for: installation_controls",
        ):
            rag.validate_cited_answer(
                {
                    "claims": [
                        {
                            "answer_part_id": "removal_controls",
                            "text": "The removal sequence is controlled.",
                            "source_ids": ["S1"],
                        }
                    ],
                    "insufficient_evidence": [],
                },
                {"S1"},
                required_answer_part_ids=[
                    "removal_controls",
                    "installation_controls",
                ],
            )

    def test_e22_removal_and_installation_can_each_have_multiple_claims(self) -> None:
        result = rag.validate_cited_answer(
            {
                "claims": [
                    {
                        "answer_part_id": "removal_controls",
                        "text": "Shield nearby live parts before removal.",
                        "source_ids": ["S1"],
                    },
                    {
                        "answer_part_id": "removal_controls",
                        "text": "Fit the blanking plate after removal.",
                        "source_ids": ["S1"],
                    },
                    {
                        "answer_part_id": "installation_controls",
                        "text": "Inspect the rail before installation.",
                        "source_ids": ["S2"],
                    },
                    {
                        "answer_part_id": "installation_controls",
                        "text": "Check for a short circuit before installation.",
                        "source_ids": ["S2"],
                    },
                ],
                "insufficient_evidence": [],
            },
            {"S1", "S2"},
            required_answer_part_ids=[
                "removal_controls",
                "installation_controls",
            ],
        )

        self.assertEqual(result["answer_part_coverage"], 1.0)
        self.assertEqual(result["insufficient_evidence"], [])

    def test_e22_installation_marked_insufficient_is_not_positive_coverage(self) -> None:
        result = rag.validate_cited_answer(
            {
                "claims": [
                    {
                        "answer_part_id": "removal_controls",
                        "text": "The removal sequence is controlled.",
                        "source_ids": ["S1"],
                    }
                ],
                "insufficient_evidence": ["installation_controls"],
            },
            {"S1"},
            required_answer_part_ids=[
                "removal_controls",
                "installation_controls",
            ],
        )

        self.assertEqual(result["answer_part_coverage"], 0.5)
        self.assertEqual(
            result["insufficient_evidence"], ["installation_controls"]
        )

    def test_required_answer_parts_preserve_legacy_mode_without_contract(
        self,
    ) -> None:
        result = rag.validate_cited_answer(
            {
                "claims": [
                    {
                        "text": "The legacy answer remains valid.",
                        "source_ids": ["S1"],
                    }
                ],
                "insufficient_evidence": [
                    "Legacy callers may still use explanatory free-form text."
                ],
            },
            {"S1"},
        )

        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["validity"], 1.0)
        self.assertEqual(result["required_answer_part_ids"], [])
        self.assertEqual(result["answered_answer_part_ids"], [])
        self.assertIsNone(result["answer_part_coverage"])
        self.assertEqual(
            result["insufficient_evidence"],
            ["Legacy callers may still use explanatory free-form text."],
        )

    def test_iq_activity_metrics_keep_cost_dimensions_separate(self) -> None:
        result = rag.iq_activity_metrics(
            [
                {
                    "type": "modelQueryPlanning",
                    "inputTokens": 100,
                    "outputTokens": 10,
                    "elapsedMs": 50,
                },
                {"type": "searchIndex", "elapsedMs": 20},
                {"type": "searchIndex", "elapsedMs": 30},
                {
                    "type": "modelAnswerSynthesis",
                    "inputTokens": 200,
                    "outputTokens": 40,
                    "elapsedMs": 80,
                },
                {"type": "agenticReasoning", "reasoningTokens": 500},
            ]
        )
        self.assertEqual(
            result,
            {
                "model_input_tokens": 300,
                "model_output_tokens": 50,
                "agentic_retrieval_tokens": 500,
                "semantic_requests": 2,
                "query_planning_ms": 50,
                "search_execution_ms_sum": 50,
                "answer_synthesis_ms": 80,
            },
        )

    def test_response_usage_can_exclude_search_requests(self) -> None:
        class Usage:
            input_tokens = 120
            output_tokens = 30

        class Response:
            usage = Usage()

        result = rag.response_token_usage(Response(), semantic_requests=0)

        self.assertEqual(result["model_input_tokens"], 120)
        self.assertEqual(result["model_output_tokens"], 30)
        self.assertEqual(result["semantic_requests"], 0)

    def test_iq_context_preserves_structured_grounding_fields(self) -> None:
        context = rag.format_iq_context(
            [
                {
                    "ref_id": "4",
                    "source_name": "authorization-source",
                    "doc_key": "RA-NHN-0101",
                    "document": {
                        "raamopdracht_id": "RA-NHN-0101",
                        "covered_vwi_ids": ["E-85"],
                        "geldigheidsgebied_postcodes": ["1704"],
                        "geldigheidsduur_start": "2026-01-01",
                        "geldigheidsduur_end": "2026-12-31",
                        "permits_live_work": False,
                    },
                }
            ]
        )

        self.assertIn("raamopdracht_id: \"RA-NHN-0101\"", context)
        self.assertIn('covered_vwi_ids: [\"E-85\"]', context)
        self.assertIn('geldigheidsgebied_postcodes: [\"1704\"]', context)
        self.assertIn("permits_live_work: false", context)

    def test_iq_reference_resolution_prefers_inline_source_data(self) -> None:
        class MustNotFetch:
            def get_document(self, *, key: str) -> dict:
                raise AssertionError(f"unexpected fallback fetch for {key}")

        resolved = rag.fetch_iq_reference_documents(
            {
                "activity": [
                    {
                        "type": "searchIndex",
                        "id": 3,
                        "knowledgeSourceName": "crew-source",
                    }
                ],
                "references": [
                    {
                        "type": "searchIndex",
                        "id": "7",
                        "activitySource": 3,
                        "docKey": "crew-001",
                        "sourceData": {
                            "id": "crew-001",
                            "crew_id": "crew-001",
                        },
                    }
                ],
            },
            {"crew-source": MustNotFetch()},
        )

        self.assertEqual(resolved[0]["resolution"], "inline_source_data")
        self.assertEqual(resolved[0]["document"]["crew_id"], "crew-001")

    def test_iq_extracted_data_parses_retrieve_and_mcp_envelopes(self) -> None:
        encoded = json.dumps(
            [
                {
                    "ref_id": "0",
                    "vwi_code": "E-85",
                    "content": "Use the prescribed controls.",
                }
            ],
            indent=2,
        )
        retrieve_payload = {
            "response": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": encoded}],
                }
            ]
        }
        mcp_payload = {
            "result": {
                "content": [{"type": "text", "text": encoded}],
            }
        }

        self.assertEqual(
            rag.extract_iq_extracted_documents(retrieve_payload),
            rag.extract_iq_extracted_documents(mcp_payload),
        )
        self.assertEqual(
            rag.extract_iq_extracted_documents(retrieve_payload)[0]["vwi_code"],
            "E-85",
        )

    def test_iq_extracted_data_does_not_parse_synthesized_answer(self) -> None:
        payload = {
            "response": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "The procedure applies. [ref_id:0]",
                        }
                    ],
                }
            ]
        }

        self.assertEqual(rag.extract_iq_extracted_documents(payload), [])

    def test_iq_reference_resolution_can_use_extracted_response(self) -> None:
        extracted = json.dumps(
            [
                {
                    "ref_id": "7",
                    "id": "crew-001",
                    "crew_id": "crew-001",
                }
            ]
        )
        resolved = rag.fetch_iq_reference_documents(
            {
                "response": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": extracted}],
                    }
                ],
                "activity": [
                    {
                        "type": "searchIndex",
                        "id": 3,
                        "knowledgeSourceName": "crew-source",
                    }
                ],
                "references": [
                    {
                        "type": "searchIndex",
                        "id": "7",
                        "activitySource": "3",
                        "docKey": "crew-001",
                        "sourceData": None,
                    }
                ],
            },
            {},
        )

        self.assertEqual(resolved[0]["resolution"], "extracted_response")
        self.assertEqual(resolved[0]["document"]["crew_id"], "crew-001")

    def test_iq_reference_resolution_keeps_exact_extracted_evidence(self) -> None:
        extracted = json.dumps(
            [
                {
                    "ref_id": "7",
                    "content": "Only this extract was supplied to generation.",
                }
            ]
        )
        resolved = rag.fetch_iq_reference_documents(
            {
                "response": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": extracted}],
                    }
                ],
                "activity": [
                    {
                        "type": "searchIndex",
                        "id": 3,
                        "knowledgeSourceName": "crew-source",
                    }
                ],
                "references": [
                    {
                        "type": "searchIndex",
                        "id": "7",
                        "activitySource": 3,
                        "docKey": "crew-001",
                        "sourceData": {
                            "crew_id": "crew-001",
                            "content": "A broader source document.",
                            "shift_status_demo": "available",
                        },
                    }
                ],
            },
            {},
        )

        document = resolved[0]["document"]
        self.assertEqual(resolved[0]["resolution"], "extracted_response")
        self.assertEqual(
            document["content"],
            "Only this extract was supplied to generation.",
        )
        self.assertEqual(document["crew_id"], "crew-001")
        self.assertNotIn("shift_status_demo", document)

    def test_cost_estimate_is_explicit_and_does_not_invent_rates(self) -> None:
        usage = {
            "model_input_tokens": 1_000_000,
            "model_output_tokens": 500_000,
            "agentic_retrieval_tokens": 2_000_000,
            "semantic_requests": 1_000,
        }
        missing = rag.estimate_cost_usd(
            usage,
            {
                "model_input_per_1m": None,
                "model_output_per_1m": None,
                "semantic_per_1k": None,
                "agentic_per_1m": None,
            },
        )
        complete = rag.estimate_cost_usd(
            usage,
            {
                "model_input_per_1m": 1.0,
                "model_output_per_1m": 4.0,
                "semantic_per_1k": 2.0,
                "agentic_per_1m": 0.5,
            },
        )

        self.assertIsNone(missing["estimated_cost_usd"])
        self.assertFalse(missing["rates_complete"])
        self.assertEqual(complete["estimated_cost_usd"], 6.0)
        self.assertTrue(complete["rates_complete"])


if __name__ == "__main__":
    unittest.main()
