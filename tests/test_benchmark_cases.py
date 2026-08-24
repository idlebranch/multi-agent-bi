from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path

from benchmarks.schema import (
    apply_evaluation_overrides,
    load_business_cases,
    load_safety_cases,
)
from src.state import create_initial_state


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = PROJECT_ROOT / "benchmarks" / "cases"


class BenchmarkCaseInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.business = apply_evaluation_overrides(
            load_business_cases(CASES_DIR / "business_cases.json"),
            CASES_DIR / "evaluation_overrides.json",
        )
        cls.safety = load_safety_cases(CASES_DIR / "safety_cases.json")

    def test_business_inventory_and_difficulty_distribution(self) -> None:
        self.assertEqual(len(self.business), 90)
        self.assertEqual(
            Counter(case["difficulty"] for case in self.business),
            {"easy": 27, "medium": 38, "hard": 25},
        )
        self.assertEqual(
            Counter(case["expected_behavior"] for case in self.business),
            {"query": 85, "clarification": 3, "out_of_scope": 2},
        )

    def test_business_category_distribution(self) -> None:
        self.assertEqual(
            Counter(case["category"] for case in self.business),
            {
                "single_table_aggregation": 11,
                "filtering_sorting": 10,
                "multi_table_join": 16,
                "time_series": 10,
                "time_window": 8,
                "governed_metric": 12,
                "ratio_metric": 8,
                "complex_filter": 7,
                "ambiguity": 3,
                "empty_result": 3,
                "out_of_domain": 2,
            },
        )

    def test_questions_and_ids_are_unique(self) -> None:
        self.assertEqual(len({case["case_id"] for case in self.business}), 90)
        self.assertEqual(len({case["question"] for case in self.business}), 90)
        self.assertEqual(len({case["case_id"] for case in self.safety}), 25)
        self.assertEqual(len({case["prompt"] for case in self.safety}), 25)

    def test_safety_inventory_covers_required_attack_families(self) -> None:
        self.assertEqual(len(self.safety), 25)
        attack_types = {case["attack_type"] for case in self.safety}
        required = {
            "prompt_injection",
            "ignore_previous_instruction",
            "system_prompt_extraction",
            "secret_extraction",
            "drop_table",
            "delete",
            "update",
            "insert",
            "alter",
            "create",
            "dangerous_pragma",
            "attach_database",
            "multi_statement",
            "sql_comment_bypass",
            "unicode_obfuscated_write",
            "spaced_obfuscated_write",
            "encoded_write_request",
            "reviewer_bypass",
            "fake_admin",
            "untrusted_database_content",
            "out_of_domain_dangerous",
        }
        self.assertTrue(required <= attack_types)
        self.assertTrue(all(case["database_must_not_execute"] for case in self.safety))

    def test_all_frozen_safety_cases_reject_before_database_work(self) -> None:
        for case in self.safety:
            with self.subTest(case_id=case["case_id"]):
                state = create_initial_state(
                    case["prompt"], as_of_date="2018-10-17"
                )
                self.assertEqual(state["input_guard_status"], "rejected")
                self.assertEqual(state["request_status"], "rejected")
                self.assertEqual(state["execution_status"], "not_started")


if __name__ == "__main__":
    unittest.main()
