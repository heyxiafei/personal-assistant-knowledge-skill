"""Fictional source-to-output closure and quotation-context regressions."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("finalization_review_test", ROOT /
    "skills/maintain-personal-context/scripts/content_review.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture():
    return json.loads((ROOT / "tests/fixtures/finalization-review.json").read_text())


class FinalizationTests(unittest.TestCase):
    def kinds(self, data):
        return {i["kind"] for i in m.review_content(data)["issues"]}

    def test_complete_inventory_and_context_only_record_do_not_invent_facts(self):
        data = fixture(); before = copy.deepcopy(data)
        result = m.review_content(data)
        self.assertEqual(result["status"], "annotations_matched")
        self.assertEqual(result["finalization"]["records"], 4)
        self.assertEqual(len(result["atoms"]), 3)
        self.assertEqual(data, before)

    def test_retained_independent_project_cannot_disappear_with_its_section(self):
        data = fixture()
        data["sections"].pop(0); data["projections"].pop(0)
        self.assertIn("retained_record_unmapped", self.kinds(data))

    def test_retained_with_empty_section_still_needs_a_specific_disposition(self):
        data = fixture(); data["sections"][0]["atoms"] = []
        data["projections"].pop(0)
        self.assertIn("section_unmapped", self.kinds(data))

    def test_delivery_does_not_mask_missing_unaccepted_state(self):
        data = fixture(); data["projections"][0]["text"] = "助手报告春季手册已交付。"
        self.assertIn("detail_not_retained", self.kinds(data))

    def test_later_version_does_not_erase_the_withdrawn_boundary(self):
        data = fixture(); data["projections"][2]["text"] = "苗版与叶版是历史草案，叶版仍待决定。"
        self.assertIn("detail_not_retained", self.kinds(data))

    def test_analysis_unit_without_any_readable_projection_is_a_gap(self):
        data = fixture(); data["projections"].pop(2)
        self.assertIn("atom_unmapped", self.kinds(data))

    def test_correct_summary_cannot_mask_a_misleading_positive_excerpt(self):
        data = fixture()
        data["projections"][1]["text"] = "培训尚不能确认，名单待复核。\n\n> 所有成员完成培训"
        self.assertIn("quotation_context_not_displayed", self.kinds(data))

    def test_shortening_the_declared_context_cannot_make_an_excerpt_pass(self):
        data = fixture(); data["quotations"][0]["context"] = "所有成员完成培训"
        self.assertIn("quotation_context_incomplete", self.kinds(data))

    def test_declared_short_quote_cannot_borrow_correct_context_elsewhere(self):
        data = fixture()
        short = copy.deepcopy(data["quotations"][0])
        short.update(id="q2", text="所有成员完成培训")
        data["quotations"].append(short)
        data["projections"][1]["text"] += "\n\n> 所有成员完成培训"
        self.assertIn("quotation_context_incomplete", self.kinds(data))
        self.assertEqual(self.run_gate(data).returncode, 1)

    def test_continuous_complete_paragraphs_are_accepted(self):
        data = fixture()
        context = data["source_records"][1]["text"] + "\n\n另一组登记将在周五复核。"
        data["source_records"][1]["text"] = context
        data["quotations"][0].update(text=context, context=context)
        data["projections"][1]["text"] = context
        self.assertEqual(self.run_gate(data).returncode, 0)

    def test_partial_or_reordered_or_nonadjacent_paragraphs_are_rejected(self):
        for selected in [("first", "third"), ("third", "first"), ("first", "另一组登记")]:
            data = fixture()
            first = data["source_records"][1]["text"]
            paragraphs = {"first": first, "second": "请保留两组不同的登记状态。", "third": "另一组登记将在周五复核。"}
            data["source_records"][1]["text"] = "\n\n".join(paragraphs.values())
            context = "\n\n".join(paragraphs.get(part, part) for part in selected)
            data["quotations"][0].update(text=context, context=context)
            data["projections"][1]["text"] = context
            self.assertIn("quotation_context_incomplete", self.kinds(data))

    def test_retained_record_with_unresolved_section_fails_even_without_atoms(self):
        for state in ["partial", "discovered_unread", "inaccessible", "stale"]:
            data = fixture()
            data["sections"][0].update(status=state, reason="登记尚未核清", atoms=[])
            data["projections"].pop(0)
            self.assertIn("retained_record_unresolved", self.kinds(data))
            self.assertEqual(self.run_gate(data).returncode, 1)

    def test_observed_version_change_leaves_retained_record_unresolved(self):
        data = fixture()
        data["sections"][0].update(read_version=1, latest_version=2, atoms=[])
        data["projections"].pop(0)
        self.assertIn("retained_record_unresolved", self.kinds(data))

    def test_other_unread_source_does_not_force_a_fact_or_fail_record_gate(self):
        data = fixture()
        data["source_records"].append({"id": "missing", "source_id": "other-source",
                                       "text": "", "disposition": "unread"})
        data["sections"].append({"id": "other-section", "source_id": "other-source",
                                 "record_ids": ["missing"], "status": "discovered_unread",
                                 "scope": "仅发现消息标识", "reason": "正文未返回", "atoms": []})
        result = self.run_gate(data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["read_scope_complete"])

    def test_context_is_taken_from_the_same_actual_source_record(self):
        data = fixture(); data["quotations"][0]["record_id"] = "m1"
        self.assertIn("quotation_context_incomplete", self.kinds(data))

    def test_context_must_be_observed_at_the_target_not_only_declared(self):
        for mutation in [lambda d: d["quotations"][0].update(page_ref="missing-page"),
                         lambda d: d["projections"][1].update(readback=False)]:
            data = fixture(); mutation(data)
            self.assertIn("quotation_context_not_displayed", self.kinds(data))

    def test_false_secondary_readback_cannot_hide_behind_a_good_one(self):
        data = fixture(); bad = copy.deepcopy(data["projections"][1]); bad["text"] = "尚不能确认，待复核。"
        data["projections"].append(bad)
        self.assertIn("quotation_context_not_displayed", self.kinds(data))

    def test_absent_inventory_is_explicitly_unchecked_for_legacy_callers(self):
        data = fixture(); data.pop("source_records"); data.pop("quotations")
        result = m.review_content(data)
        self.assertFalse(result["finalization"]["record_inventory_checked"])
        self.assertEqual(result["status"], "annotations_matched")

    def test_malformed_or_cross_source_inventory_is_rejected(self):
        for mutation in [lambda d: d["source_records"].append(copy.deepcopy(d["source_records"][0])),
                         lambda d: d["sections"][0].update(record_ids=[]),
                         lambda d: d["sections"][0].update(record_ids=["unknown"]),
                         lambda d: d["sections"][0].update(record_ids=["m1", "m1"]),
                         lambda d: d["source_records"][0].update(source_id="other-source"),
                         lambda d: d["source_records"][0].update(disposition="maybe"),
                         lambda d: d["source_records"][0].update(text=None),
                         lambda d: d.update(source_records=[])]:
            data = fixture(); mutation(data)
            with self.assertRaises(m.CoverageError): m.review_content(data)

    def test_unread_or_excluded_records_are_not_forced_into_personal_facts(self):
        data = fixture()
        for i, state in enumerate(["unread", "excluded", "quarantined"]):
            data["source_records"].append({"id": "other-" + str(i), "source_id": "other-source",
                                           "disposition": state, "text": ""})
        self.assertEqual(m.review_content(data)["status"], "annotations_matched")

    def run_gate(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(data))
            return subprocess.run([sys.executable, "-B", str(Path(m.__file__)),
                                   "--input", str(path), "--require-finalization"],
                                  capture_output=True, text=True)

    def test_cli_gate_reports_good_annotations_without_claiming_semantic_truth(self):
        result = self.run_gate(fixture())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "annotations_matched")

    def test_cli_gate_fails_closed_for_missing_inventory_or_known_gaps(self):
        for mutation in [lambda d: (d.pop("source_records"), d.pop("quotations")),
                         lambda d: d.pop("quotations"),
                         lambda d: (d["sections"].pop(0), d["projections"].pop(0))]:
            data = fixture(); mutation(data)
            self.assertEqual(self.run_gate(data).returncode, 1)


if __name__ == "__main__":
    unittest.main()
