from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = (
    ROOT
    / "labs"
    / "observability-and-evaluation"
    / "2-agent-evaluation.ipynb"
)


def _call_path(call: ast.Call) -> str:
    parts: list[str] = []
    node: ast.expr = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class EvaluationNotebookContractTests(unittest.TestCase):
    def test_quality_criteria_are_defined_before_eval_creation(self) -> None:
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        assignment_position: tuple[int, int] | None = None
        assignment_value: ast.expr | None = None
        create_position: tuple[int, int] | None = None
        create_call: ast.Call | None = None

        for cell_index, cell in enumerate(notebook["cells"]):
            if cell.get("cell_type") != "code":
                continue
            tree = ast.parse("".join(cell.get("source", [])), filename=str(NOTEBOOK))
            for statement_index, statement in enumerate(tree.body):
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "testing_criteria"
                    for target in statement.targets
                ):
                    assignment_position = (cell_index, statement_index)
                    assignment_value = statement.value
                for node in ast.walk(statement):
                    if isinstance(node, ast.Call) and _call_path(node) == "openai_client.evals.create":
                        create_position = (cell_index, statement_index)
                        create_call = node

        self.assertIsNotNone(assignment_position, "testing_criteria must be assigned")
        self.assertIsNotNone(create_position, "the notebook must create a cloud evaluation")
        self.assertLess(assignment_position, create_position)
        self.assertIsInstance(assignment_value, ast.List)

        criteria = []
        for item in assignment_value.elts:
            self.assertIsInstance(item, ast.Call)
            self.assertIsInstance(item.func, ast.Name)
            self.assertEqual(item.func.id, "judge")
            criteria.append(tuple(ast.literal_eval(argument) for argument in item.args[:2]))
        self.assertEqual(
            criteria,
            [
                ("retrieval", "builtin.retrieval"),
                ("groundedness", "builtin.groundedness"),
                ("relevance", "builtin.relevance"),
            ],
        )

        keywords = {keyword.arg: keyword.value for keyword in create_call.keywords}
        self.assertIn("testing_criteria", keywords)
        self.assertIsInstance(keywords["testing_criteria"], ast.Name)
        self.assertEqual(keywords["testing_criteria"].id, "testing_criteria")


if __name__ == "__main__":
    unittest.main()
