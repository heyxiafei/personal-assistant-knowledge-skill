"""Observable zero/sparse/rich first-result behavior; fictional annotations, no API."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/maintain-personal-context/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("first_run", SCRIPTS / "first_run.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
FIXTURE = json.loads((ROOT / "tests/fixtures/first-run.json").read_text())


def project_fixture(status="active", action="advance"):
    data = copy.deepcopy(FIXTURE["rich"])
    data["claims"].append({"key": "fictional_poster_state", "value": status,
                           "source_id": "fictional-state-message", "source_kind": "conversation",
                           "author_role": "user", "evidence_tier": "direct",
                           "recorded_at": "2026-03-10T09:00:00+00:00"})
    data["recommendations"] = [dict(data["recommendations"][0], scope="project", project_context={
        "project_key": "fictional_poster_state", "status": status, "action_type": action,
        "allowed_actions": [action], "source_ids": ["fictional-state-message"]})]
    return data


def add_restart_authorization(data):
    context = data["recommendations"][0]["project_context"]
    data["claims"].append({"key": "fictional_restart", "value": context["action_type"],
                           "source_id": "fictional-restart-message", "source_kind": "conversation",
                           "author_role": "user", "evidence_tier": "direct",
                           "recorded_at": "2026-03-11T09:00:00+00:00",
                           "project_key": context["project_key"]})
    context["restart_authorization"] = {"claim_key": "fictional_restart", "source_id": "fictional-restart-message"}


class FirstRunTests(unittest.TestCase):
    def test_explicit_exclusion_is_not_missing_or_an_instruction_to_read(self):
        for state in ("user_excluded", "experiment_excluded"):
            data = copy.deepcopy(FIXTURE["rich"])
            data["source_review"][0].update(status=state, reason="This run explicitly excludes the prior library")
            result = m.build_result(data)
            self.assertEqual(result["source_review"]["items"][0]["status"], state)
            self.assertEqual(result["source_review"]["continuation_roles"], [])
            self.assertTrue(result["coverage_gaps"])
            self.assertTrue(all(a["kind"] == "recommendation" for a in result["actions"]))

    def test_unattempted_source_remains_a_continuation_gap(self):
        data = copy.deepcopy(FIXTURE["rich"])
        data["source_review"][0].update(status="not_attempted", reason="Discovered, not yet attempted")
        result = m.build_result(data)
        self.assertEqual(result["actions"][0]["kind"], "source_review_action")
        self.assertTrue(result["source_review"]["continuation_roles"])

    def test_read_metadata_cannot_reopen_explicit_parent_or_child_exclusions(self):
        checks = [
            {"source_kind": "space_page", "reader_kind": "codex_thread"},
            {"source_kind": "space_page", "reader_kind": "space_page",
             "read_version": 1, "latest_version": 2},
        ]
        for state in ("user_excluded", "experiment_excluded"):
            for check in checks:
                for child in (False, True):
                    with self.subTest(state=state, check=check, child=child):
                        data = copy.deepcopy(FIXTURE["rich"])
                        row = data["source_review"][0]
                        excluded = {"status": state, "reason": "Explicit input exclusion",
                                    "read_check": check}
                        if child:
                            row.update(status="read", source_ids=["fictional-already-read"])
                            row["discovered_sources"] = [{"source_id": "fictional-excluded", **excluded}]
                        else:
                            row.update(excluded)
                        result = m.build_result(data)
                        reviewed = result["source_review"]["items"][0]
                        self.assertEqual((reviewed["discovered_sources"][0] if child else reviewed)["status"], state)
                        self.assertEqual(result["source_review"]["continuation_roles"], [])
                        self.assertTrue(all(a["kind"] == "recommendation" for a in result["actions"]))

    def test_zero_data_unknown_identity_three_simple_information_actions(self):
        result = m.build_result(FIXTURE["zero"])
        self.assertTrue(all(row["fact"] is None for row in result["identity"]))
        self.assertIsNone(result["priority"]["fact"])
        self.assertEqual([a["key"] for a in result["actions"]],
                         ["current_problem", "representative_experience", "time_resources"])
        for action in result["actions"]:
            self.assertEqual(action["kind"], "information_action")
            for field in ("what", "why", "how", "first_step", "confirmation_condition"):
                self.assertTrue(action[field].strip())
        self.assertNotIn("https://", m.render_result(result))

    def test_sparse_data_uses_known_problem_without_reasking(self):
        result = m.build_result(FIXTURE["sparse"])
        self.assertEqual(result["priority"]["fact"]["value"], FIXTURE["sparse"]["claims"][0]["value"])
        self.assertEqual(len(result["actions"]), 3)
        self.assertEqual(result["actions"][0]["kind"], "recommendation")
        self.assertEqual([a["key"] for a in result["actions"] if a["kind"] == "information_action"],
                         ["representative_experience", "time_resources"])
        self.assertTrue(all(row["fact"] is None for row in result["identity"]))

    def test_rich_data_uses_correction_evidence_three_advice_and_working_entry(self):
        result = m.build_result(FIXTURE["rich"])
        identity = {row["key"]: row["fact"] for row in result["identity"]}
        self.assertEqual(identity["name"]["value"], "林远")
        self.assertEqual(identity["skills"]["value"], "两年展台设计经历")
        self.assertEqual(identity["skills"]["source_ids"], ["fictional-source-12", "fictional-source-31"])
        self.assertTrue(any("已存记忆摘要" in s for s in identity["name"]["source_notes"]))
        self.assertTrue(any("用户原话" in s for s in identity["name"]["source_notes"]))
        self.assertEqual(len(result["actions"]), 3)
        self.assertTrue(all(a["kind"] == "recommendation" for a in result["actions"]))
        for action in result["actions"]:
            self.assertTrue(action["basis"])
            self.assertTrue(action["first_step"])
            self.assertTrue(action["confirmation_condition"])
        rendered = m.render_result(result)
        self.assertIn("<https://example.invalid/fictional-root>", rendered)
        self.assertNotIn("五年", rendered)
        self.assertNotIn("八十件", rendered)

    def test_recommendations_never_mutate_facts_and_replay_is_identical(self):
        data = copy.deepcopy(FIXTURE["rich"]); before = copy.deepcopy(data)
        expected = m.build_result(data)
        self.assertEqual(m.build_result(data), expected)
        self.assertEqual(data, before)
        data["claims"] += copy.deepcopy(data["claims"])
        self.assertEqual(m.build_result(data), expected)
        self.assertFalse(any(a["label"] in f["fact"]["value"] for a in expected["actions"]
                             for f in expected["identity"] if f["fact"]))

    def test_summary_assistant_or_conflict_cannot_ground_priority_or_advice(self):
        for change in (dict(author_role="assistant"),
                       dict(source_kind="memory", author_role="unknown", evidence_tier="summary")):
            data = copy.deepcopy(FIXTURE["sparse"]); data["claims"][0].update(change)
            with self.assertRaises(m.ResultError): m.build_result(data)
            data["recommendations"] = []
            result = m.build_result(data)
            self.assertIsNone(result["priority"]["fact"])
            self.assertEqual(result["actions"][0]["key"], "current_problem")
        data = copy.deepcopy(FIXTURE["sparse"])
        other = copy.deepcopy(data["claims"][0]); other.update(value="另一个问题", source_id="fictional-conflict")
        data["claims"].append(other)
        with self.assertRaises(m.ResultError): m.build_result(data)

    def test_each_advice_needs_known_basis_first_step_and_confirmation(self):
        for field, bad in (("basis_keys", []), ("basis_keys", ["unknown"]),
                           ("first_step", " "), ("confirmation_condition", "")):
            data = copy.deepcopy(FIXTURE["rich"]); data["recommendations"][0][field] = bad
            with self.assertRaises(m.ResultError): m.build_result(data)
        data = copy.deepcopy(FIXTURE["rich"]); data["recommendations"] *= 2
        with self.assertRaises(m.ResultError): m.build_result(data)
        data["recommendations"] = []
        with self.assertRaises(m.ResultError): m.build_result(data)

    def test_information_actions_reserve_space_and_total_never_exceeds_three(self):
        data = copy.deepcopy(FIXTURE["sparse"]); data["recommendations"] *= 3
        actions = m.build_result(data)["actions"]
        self.assertEqual(len(actions), 3)
        self.assertEqual(sum(a["kind"] == "recommendation" for a in actions), 1)
        self.assertEqual(sum(a["kind"] == "information_action" for a in actions), 2)

    def test_legacy_advice_remains_compatible_but_state_not_assessed_is_visible(self):
        result = m.build_result(FIXTURE["rich"])
        self.assertTrue(all(a["state_check"] == "not_assessed" for a in result["actions"]))
        self.assertIn("项目状态：未核（旧格式）", m.render_result(result))

    def test_general_advice_is_not_blocked_by_unrelated_cancelled_project(self):
        data = project_fixture("cancelled")
        data["recommendations"][0].update(scope="general")
        del data["recommendations"][0]["project_context"]
        action = m.build_result(data)["actions"][0]
        self.assertEqual(action["state_check"], "not_applicable")
        self.assertEqual(action["basis"][0]["key"], data["recommendations"][0]["basis_keys"][0])

    def test_project_scope_requires_context_and_cannot_be_hidden_as_general(self):
        data = project_fixture()
        del data["recommendations"][0]["project_context"]
        with self.assertRaises(m.ResultError): m.build_result(data)
        for scope in (None, "general", "other"):
            data = project_fixture(); data["recommendations"][0]["scope"] = scope
            with self.assertRaises(m.ResultError): m.build_result(data)

    def test_planned_and_active_can_advance_only_with_matching_sourced_state(self):
        for status in ("planned", "active"):
            data = project_fixture(status); before = copy.deepcopy(data)
            action = m.build_result(data)["actions"][0]
            self.assertEqual(action["state_check"], "annotations_checked")
            self.assertEqual(action["project_context"]["source_ids"], ["fictional-state-message"])
            self.assertEqual(data, before)
            self.assertEqual(m.build_result(data)["actions"][0], action)

    def test_completed_paused_cancelled_unknown_cannot_advance_even_with_known_basis(self):
        for status in ("completed", "paused", "cancelled", "unknown"):
            with self.subTest(status=status):
                data = project_fixture(status)
                data["recommendations"][0]["basis_keys"] = ["fictional_poster_state"]
                with self.assertRaises(m.ResultError): m.build_result(data)

    def test_stopped_projects_can_have_selected_non_restarting_actions(self):
        for status in ("completed", "paused", "cancelled"):
            for action_type in ("review", "archive", "clarify"):
                result = m.build_result(project_fixture(status, action_type))
                context = result["actions"][0]["project_context"]
                self.assertEqual(context["status"], status)
                self.assertEqual(context["action_type"], action_type)
                self.assertNotIn("restart_authorization", context)

    def test_context_rejects_conflicting_unknown_or_unsourced_state(self):
        for change in ({"status": "completed"}, {"project_key": "unknown"},
                       {"source_ids": []}, {"source_ids": ["made-up"]},
                       {"allowed_actions": []}, {"allowed_actions": ["review"]},
                       {"action_type": "send_without_permission"}):
            data = project_fixture(); data["recommendations"][0]["project_context"].update(change)
            with self.assertRaises(m.ResultError): m.build_result(data)
        data = project_fixture()
        data["claims"].append(dict(data["claims"][-1], value="cancelled", source_id="fictional-conflict"))
        with self.assertRaises(m.ResultError): m.build_result(data)

    def test_project_context_cannot_cite_only_memory_when_direct_state_exists(self):
        data = project_fixture()
        data["claims"].append(dict(data["claims"][-1], source_kind="memory", evidence_tier="summary",
                                   author_role="unknown", source_id="fictional-state-summary"))
        data["recommendations"][0]["project_context"]["source_ids"] = ["fictional-state-summary"]
        with self.assertRaises(m.ResultError): m.build_result(data)

    def test_restart_requires_new_direct_scoped_user_authorization(self):
        for status in ("completed", "paused", "cancelled"):
            for action_type in ("resume", "reopen"):
                data = project_fixture(status, action_type)
                with self.assertRaises(m.ResultError): m.build_result(data)
                add_restart_authorization(data)
                result = m.build_result(data)
                auth = result["actions"][0]["project_context"]["restart_authorization"]
                self.assertEqual(auth["source_id"], "fictional-restart-message")
                self.assertIn("本次重启授权来源：fictional", m.render_result(result))

    def test_restart_rejects_assistant_memory_old_unknown_and_other_project_authorization(self):
        changes = ({"author_role": "assistant"},
                   {"source_kind": "memory", "author_role": "unknown", "evidence_tier": "summary"},
                   {"source_kind": "saved_material", "evidence_tier": "saved"},
                   {"recorded_at": "2026-03-09T09:00:00+00:00"},
                   {"recorded_at": "2026-03-10T09:00:00+00:00"},
                   {"recorded_at": None}, {"recorded_at": "2026-03-11T09:00:00"},
                   {"project_key": "fictional_other_project"}, {"value": "review"})
        for change in changes:
            with self.subTest(change=change):
                data = project_fixture("paused", "resume"); add_restart_authorization(data)
                data["claims"][-1].update(change)
                with self.assertRaises(m.ResultError): m.build_result(data)

    def test_restart_cannot_select_memory_citation_from_an_otherwise_known_user_claim(self):
        data = project_fixture("paused", "resume"); add_restart_authorization(data)
        data["claims"].append(dict(data["claims"][-1], source_kind="memory", evidence_tier="summary",
                                   author_role="unknown", source_id="fictional-restart-summary"))
        data["recommendations"][0]["project_context"]["restart_authorization"]["source_id"] = "fictional-restart-summary"
        with self.assertRaises(m.ResultError): m.build_result(data)

    def test_restart_cannot_omit_later_current_state_evidence_from_date_check(self):
        data = project_fixture("paused", "resume"); add_restart_authorization(data)
        state = next(c for c in data["claims"] if c["key"] == "fictional_poster_state")
        for date in (None, "2026-03-12T09:00:00+00:00"):
            changed = copy.deepcopy(data)
            changed["claims"].append(dict(state, source_id="fictional-later-state", recorded_at=date))
            with self.assertRaises(m.ResultError): m.build_result(changed)

    def test_unverified_missing_unsafe_entry_does_not_print_a_fake_working_link(self):
        for entry in ({"status": "verified"}, {"status": "verified", "url": "https://user:secret" + "@example.invalid/root"},
                      {"status": "verified", "url": "https://example.invalid/root?" + "sig=secret"},
                      {"status": "pending", "reason": "not read back", "url": "https://example.invalid/root"},
                      {"status": "unavailable"}):
            data = copy.deepcopy(FIXTURE["zero"]); data["entry"] = entry
            with self.assertRaises(m.ResultError): m.build_result(data)

    def test_markup_in_claims_is_literal_not_an_extra_action_or_link(self):
        data = copy.deepcopy(FIXTURE["rich"])
        data["claims"][1]["value"] = "简介\n# injected\n[open](https://example.invalid) <script>"
        rendered = m.render_result(m.build_result(data))
        self.assertNotIn("\n# injected", rendered)
        self.assertNotIn("[open](", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_cli_reads_only_selected_fixture_no_file_changes(self):
        before = {p.relative_to(ROOT): p.read_bytes() for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "selected.json"; selected.write_text(json.dumps(FIXTURE["zero"]))
            completed = subprocess.run([sys.executable, "-B", str(SCRIPTS / "first_run.py"), "--input", str(selected)],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, m.render_result(m.build_result(FIXTURE["zero"])))
            self.assertEqual(list(Path(tmp).iterdir()), [selected])
        after = {p.relative_to(ROOT): p.read_bytes() for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
