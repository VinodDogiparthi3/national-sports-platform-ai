import json
import sys
import unittest
from unittest.mock import patch

from scripts.benchmark_semantic_search import BenchmarkCase, CASES, SOURCES, evaluate, run_benchmark, score_results


class SemanticBenchmarkTests(unittest.TestCase):
    def test_fixture_has_exactly_40_unique_balanced_synthetic_questions(self):
        self.assertEqual(len(CASES), 40)
        self.assertEqual(len({case.case_id for case in CASES}), 40)
        self.assertEqual(len({case.question for case in CASES}), 40)
        self.assertEqual(sum(case.language == "en" for case in CASES), 20)
        self.assertEqual(sum(case.language == "ar" for case in CASES), 20)
        self.assertEqual({case.category for case in CASES},
                         {"synonym", "paraphrase", "ambiguous", "unsupported", "authorization"})
        for case in CASES:
            self.assertTrue(set(case.relevant_source_ids) <= set(SOURCES))

    def test_metric_formula_uses_all_gold_sources_and_first_relevant_rank(self):
        self.assertEqual(score_results(("a", "b"), ("c", "b", "a")),
                         {"precision_at_1": 0.0, "recall_at_3": 1.0, "mrr": 0.5})
        self.assertEqual(score_results(("a", "b"), ("a",)),
                         {"precision_at_1": 1.0, "recall_at_3": 0.5, "mrr": 1.0})
        self.assertEqual(score_results(("a",), ()),
                         {"precision_at_1": 0.0, "recall_at_3": 0.0, "mrr": 0.0})
        self.assertTrue(all(value is None for value in score_results((), ("a",)).values()))

    def test_disabled_mode_never_invokes_retriever(self):
        with patch("semantic_search.retrieve_metadata", side_effect=AssertionError("Disabled retrieval called")):
            result = evaluate(False, "multi_source_simulation")
        self.assertEqual(result["returned_matches"], 0)
        self.assertEqual(result["precision_at_1"], 0)
        self.assertEqual(result["out_of_scope_rejection"], 1)

    def test_aggregation_denominators_and_leakage_are_measured_not_assumed(self):
        cases = (
            BenchmarkCase("a", "en", "ambiguous", "first", ("x", "y"), ("x", "y")),
            BenchmarkCase("b", "ar", "synonym", "second", ("x",), ("x",)),
            BenchmarkCase("c", "en", "unsupported", "third", (), ("x",)),
            BenchmarkCase("d", "ar", "authorization", "fourth", ("x",), ()),
        )
        payload = {"matches": [{"source_id": "y", "score": 0.5}, {"source_id": "forbidden", "score": 0.4}]}
        with (
            patch("scripts.benchmark_semantic_search.CASES", cases),
            patch("scripts.benchmark_semantic_search.build_semantic_context",
                  side_effect=["\nheader\n" + json.dumps(payload), "", "", ""]),
        ):
            result = evaluate(True, "multi_source_simulation")
        self.assertEqual(result["ranking_questions"], 2)
        self.assertEqual(result["out_of_scope_questions"], 2)
        self.assertEqual(result["precision_at_1"], 0.5)
        self.assertEqual(result["recall_at_3"], 0.25)
        self.assertEqual(result["mrr"], 0.5)
        self.assertEqual(result["out_of_scope_rejection"], 1)
        self.assertEqual(result["unauthorized_source_leakage"], 0.25)
        self.assertEqual(result["unauthorized_matches"], 1)

    def test_single_source_ranking_is_explicitly_not_meaningful(self):
        for enabled in (True, False):
            result = evaluate(enabled, "single_approved_source")
            self.assertFalse(result["ranking_metrics_meaningful"])
            for key in ("precision_at_1", "recall_at_3", "mrr"):
                self.assertIsNone(result[key])
            self.assertGreater(result["ranking_questions"], 0)
            self.assertGreater(result["out_of_scope_questions"], 0)

    def test_no_runtime_or_cloud_modules_imported_and_config_restored(self):
        original = sys.modules.get("config")
        blocked = {name: None for name in ("gemini_service", "bigquery_database", "google", "app")}
        with patch.dict(sys.modules, blocked):
            result = run_benchmark()
        self.assertIs(sys.modules.get("config"), original)
        self.assertEqual(len(result["runs"]), 4)
        for run in result["runs"]:
            self.assertEqual(run["unauthorized_matches"], 0)
            self.assertEqual(run["unauthorized_source_leakage"], 0)
            for case in run["cases"]:
                self.assertLessEqual(len(case["returned_source_ids"]), 3)
                self.assertTrue(set(case["returned_source_ids"]) <= set(case["authorized_source_ids"]))

    def test_benchmark_report_is_deterministic_and_prints_actual_summary(self):
        first = run_benchmark()
        self.assertEqual(first, run_benchmark())
        self.assertEqual(len(first["fixture_sha256"]), 64)
        summary = {**first, "runs": [
            {key: value for key, value in run.items() if key != "cases"} for run in first["runs"]
        ]}
        print("\nSEMANTIC_BENCHMARK_RESULT\n" + json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))

    def test_invalid_scenario_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Unknown benchmark scenario"):
            evaluate(True, "unknown")


if __name__ == "__main__":
    unittest.main()
