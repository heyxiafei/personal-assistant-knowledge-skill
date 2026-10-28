"""Synthetic quick-start contracts; no personal accounts or host UI are used."""
import copy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/maintain-personal-context/scripts"
sys.path.insert(0, str(SCRIPTS))
import first_run
import interview
import quick_start as m
import session_bundle
import space_state

FIXTURE = ROOT / "tests/fixtures/quick-start.json"
STAMP = "2026-06-02T09:00:00+00:00"
TOPIC_IDS = ["situation", "goal", "collaboration", "constraints", "identity",
             "capability", "resources", "boundaries"]


def data(case="new"):
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[case]


def claim(key, value, source_id="fictional-current-user", **changes):
    row = {"key": key, "value": value, "source_id": source_id,
           "recorded_at": STAMP, "source_kind": "conversation",
           "author_role": "user", "evidence_tier": "direct"}
    row.update(changes)
    return row


def reply(session, action="answer", **kwargs):
    q = m.question(session)
    args = dict(source_id="fictional-current-user-" + q["id"], recorded_at=STAMP)
    args.update(kwargs)
    return m.respond(session, q["id"], action, "fictional-submit-" + q["id"], **args)


class QuickStartTests(unittest.TestCase):
    def test_later_specific_detail_retires_the_deferred_gap_after_dated_correction(self):
        session = m.start(data())
        while m.question(session)["id"] != "capability":
            session = reply(session, "skip")
        choice = m.question(session)["choices"][0]
        session = reply(session, "choice", choice_id=choice["id"])
        self.assertTrue(any(r["id"] == "capability" for r in m.followups(session)))
        session = m.supplement(session, [claim("skills", "有纸偶制作经验", "fictional-later-detail",
                                correction_of=choice["label"], recorded_at="2026-06-03T09:00:00+00:00")])
        self.assertFalse(any(r["id"] == "capability" for r in m.followups(session)))
        self.assertEqual(next(r for r in m.prefilled(session) if r["id"] == "capability")["state"], "known")

    def test_new_user_can_use_preview_before_any_answers_without_input_mutation(self):
        original = data(); before = copy.deepcopy(original)
        session = m.start(original); view = m.draft(session)
        self.assertEqual(original, before)
        self.assertEqual(session["data"]["claims"], [])
        self.assertEqual(view["question"]["id"], "situation")
        self.assertEqual(len(view["prefilled"]), 8)
        self.assertTrue(all(not row["values"] for row in view["prefilled"]))
        self.assertIn("你是谁", view["markdown"])
        self.assertIn("未", view["markdown"])
        self.assertFalse(session.get("confirmation"))

    def test_empty_first_round_is_bounded_and_unknown_is_never_a_default_fact(self):
        session = m.start(data()); seen = []
        while (question := m.question(session)):
            self.assertNotIn(question["id"], seen)
            seen.append(question["id"])
            session = reply(session, "uncertain")
        self.assertEqual(seen, TOPIC_IDS)
        self.assertEqual(session["data"]["claims"], [])
        self.assertFalse(session.get("confirmation"))
        self.assertTrue(m.followups(session))

    def test_existing_material_prefills_sources_and_skips_reliably_known_topics(self):
        original = data("existing"); before = copy.deepcopy(original)
        session = m.start(original); view = m.draft(session)
        self.assertEqual(original, before)
        self.assertEqual(m.question(session)["id"], "goal")
        rows = {row["id"]: row for row in view["prefilled"]}
        for topic in ("situation", "identity", "capability"):
            self.assertTrue(rows[topic]["values"])
            self.assertTrue(all(v["source_ids"] for v in rows[topic]["values"]))
            self.assertTrue(all(v["evidence"] == "sourced_user" for v in rows[topic]["values"]))
        seen = []
        while (question := m.question(session)):
            seen.append(question["id"]); session = reply(session, "skip")
        self.assertFalse({"situation", "identity", "capability"}.intersection(seen))

    def test_all_known_is_zero_questions_and_original_answers_remain_unchanged(self):
        original = data("all_known"); session = m.start(original)
        self.assertIsNone(m.question(session))
        self.assertIsNone(m.draft(session)["question"])
        self.assertEqual(session["data"]["claims"], original["claims"])
        self.assertEqual(m.followups(session), [])
        self.assertTrue(m.draft(session)["markdown"])

    def test_conflicts_are_prioritized_and_choices_keep_their_source_candidates(self):
        session = m.start(data("conflict")); question = m.question(session)
        self.assertEqual(question["id"], "goal")
        choices = question["choices"]
        self.assertTrue(any("先整理纸偶作品" in c["label"] for c in choices))
        self.assertTrue(any("先整理绘图教程" in c["label"] for c in choices))
        unchanged = reply(session, "choice", choice_id=choices[1]["id"])
        self.assertIn("near_term_goal", m.draft(unchanged)["unresolved_keys"])
        self.assertEqual(len(unchanged["data"]["claims"]), 3)

    def test_explicit_dated_correction_retains_history_and_resolves_only_its_key(self):
        session = m.start(data("conflict"))
        corrected = reply(session, value="先整理绘图教程", correction_of="先整理纸偶作品")
        self.assertNotIn("near_term_goal", m.draft(corrected)["unresolved_keys"])
        self.assertEqual(corrected["data"]["claims"][:2], session["data"]["claims"])
        facts = first_run.reconcile_claims(corrected["data"]["claims"])["facts"]
        self.assertEqual(next(f for f in facts if f["key"] == "near_term_goal")["current_value"], "先整理绘图教程")

    def test_memory_and_assistant_material_are_candidates_not_confirmed_user_facts(self):
        original = data()
        original["claims"] = [claim("current_situation", "记忆推测的方向",
                                    source_kind="memory", author_role="unknown", evidence_tier="summary")]
        session = m.start(original)
        values = next(row for row in m.draft(session)["prefilled"] if row["id"] == "situation")["values"]
        self.assertTrue(values)
        self.assertTrue(all(v["evidence"] == "inferred" for v in values))
        self.assertEqual(m.question(session)["id"], "situation")
        chosen = m.question(session)["choices"][0]
        confirmed = reply(session, "choice", choice_id=chosen["id"])
        values = next(row for row in m.draft(confirmed)["prefilled"] if row["id"] == "situation")["values"]
        self.assertTrue(any(v["evidence"] == "user_confirmed" for v in values))
        self.assertEqual(confirmed["data"]["claims"][0], original["claims"][0])

    def test_source_continuation_and_blocker_do_not_masquerade_as_missing_information(self):
        original = data(); original["source_review"][0].update(status="partial", reason="获准资料还有正文可续读")
        session = m.start(original)
        self.assertIsNone(m.question(session))
        self.assertIn("personal_profile", m.draft(session)["coverage"]["continuation_roles"])
        original["source_review"][0].update(status="permission_denied", reason="实际读取被拒绝")
        session = m.start(original)
        self.assertIsNone(m.question(session))
        self.assertIn("personal_profile", m.draft(session)["coverage"]["blocked_roles"])
        self.assertEqual(session["data"]["claims"], [])

    def test_skip_uncertain_and_do_not_save_retain_gaps_without_reply_text(self):
        session = m.start(data())
        session = reply(session, "skip", value="SYNTHETIC-SKIPPED-TEXT")
        session = reply(session, "uncertain", value="SYNTHETIC-UNCERTAIN-TEXT")
        session = reply(session, "do_not_save", value="SYNTHETIC-SECRET-NOT-FOR-CHECKPOINT")
        encoded = json.dumps(session)
        self.assertNotIn("SYNTHETIC-SKIPPED-TEXT", encoded)
        self.assertNotIn("SYNTHETIC-UNCERTAIN-TEXT", encoded)
        self.assertNotIn("SYNTHETIC-SECRET-NOT-FOR-CHECKPOINT", encoded)
        self.assertEqual(session["data"]["claims"], [])
        self.assertTrue(m.followups(session))

    def test_other_and_free_text_require_a_real_reply_and_remain_this_round_evidence(self):
        session = m.start(data())
        with self.assertRaises(ValueError): reply(session, "other", value=" ")
        with self.assertRaises(ValueError): reply(session, "answer", value="自述", recorded_at=None)
        answered = reply(session, "other", value="当前没有固定项目，想先整理已有作品")
        self.assertEqual(answered["data"]["claims"][-1]["value"], "当前没有固定项目，想先整理已有作品")
        self.assertEqual(answered["data"]["claims"][-1]["author_role"], "user")
        self.assertFalse(answered.get("confirmation"))

    def test_choice_cannot_invent_an_option_or_answer_a_different_question(self):
        session = m.start(data())
        with self.assertRaises(ValueError): reply(session, "choice", choice_id="invented")
        with self.assertRaises(ValueError):
            m.respond(session, "goal", "skip", "fictional-wrong-topic")
        self.assertEqual(session["data"]["claims"], [])

    def test_text_fallback_exposes_the_same_choices_and_explicit_escape_actions(self):
        session = m.start(data()); question = m.question(session)
        rendered = m.render_question(session)
        self.assertIsNone(question["default_choice"])
        self.assertTrue(question["free_text"])
        for index, choice in enumerate(question["choices"]):
            self.assertIn(chr(65 + index) + ". " + choice["label"], rendered)
        self.assertTrue({"other", "skip", "uncertain", "do_not_save", "pause", "finish"}
                        .issubset(question["actions"]))
        for text in ("其他", "跳过", "不确定", "不保存", "暂停", "初稿"):
            self.assertIn(text, rendered)

    def test_one_free_reply_can_fill_multiple_topics_without_followup_repetition(self):
        session = m.start(data())
        additions = [claim("current_situation", "正在整理纸偶作品", "fictional-multi-reply"),
                     claim("near_term_goal", "先完成一页演示说明", "fictional-multi-reply"),
                     claim("time_resources", "本周半小时", "fictional-multi-reply")]
        before = copy.deepcopy(additions)
        supplemented = m.supplement(session, additions)
        self.assertEqual(additions, before)
        self.assertEqual(session["data"]["claims"], [])
        self.assertEqual(m.question(supplemented)["id"], "collaboration")
        seen = []
        while (question := m.question(supplemented)):
            seen.append(question["id"]); supplemented = reply(supplemented, "skip")
        self.assertFalse({"situation", "goal", "constraints"}.intersection(seen))

    def test_reply_clears_stale_advice_without_inventing_a_replacement(self):
        original = data("existing")
        original["recommendations"] = [{"label": "按旧现状整理演示", "basis_keys": ["current_situation"],
                                         "first_step": "列一页提纲", "confirmation_condition": "仅候选", "scope": "general"}]
        session = m.start(original)
        answered = reply(session, value="先整理绘图教程")
        self.assertEqual(answered["data"]["recommendations"], [])
        self.assertTrue(answered["advice_needs_refresh"])
        self.assertEqual(session["data"]["recommendations"], original["recommendations"])

    def test_repeated_submission_is_idempotent_and_changed_replay_is_rejected(self):
        session = m.start(data()); args = dict(value="先整理现有作品", source_id="fictional-reply", recorded_at=STAMP)
        answered = m.respond(session, "situation", "answer", "fictional-replay", **args)
        self.assertEqual(m.respond(answered, "situation", "answer", "fictional-replay", **args), answered)
        with self.assertRaises(ValueError):
            m.respond(answered, "situation", "answer", "fictional-replay", **dict(args, value="另一回答"))

    def test_pause_resume_and_finish_allow_use_without_completing_the_round(self):
        session = reply(m.start(data()), "skip")
        pending = m.question(session)["id"]; paused = m.pause(session)
        self.assertIsNone(m.question(paused))
        self.assertTrue(m.draft(paused)["markdown"])
        resumed = m.resume(paused)
        self.assertEqual(m.question(resumed)["id"], pending)
        finished = m.finish(resumed)
        self.assertIsNone(m.question(finished))
        self.assertTrue(m.followups(finished))
        self.assertTrue(m.draft(finished)["markdown"])
        self.assertFalse(finished.get("confirmation"))

    def test_legacy_answers_and_skips_migrate_idempotently_without_reasking(self):
        legacy = interview.start(data("existing"), checkpoint_consent="fictional-checkpoint-consent")
        legacy = interview.respond(legacy, "goal", "answer", "fictional-old-answer",
                                   value="先写演示说明", source_id="fictional-old-reply", recorded_at=STAMP)
        legacy = interview.respond(legacy, "constraints", "skip", "fictional-old-skip")
        before = copy.deepcopy(legacy); session = m.migrate(legacy)
        self.assertEqual(legacy, before)
        self.assertEqual(session["data"]["claims"], legacy["data"]["claims"])
        self.assertEqual(session["decisions"], legacy["decisions"])
        self.assertEqual(session["submissions"], legacy["submissions"])
        self.assertEqual(session["checkpoint_consent"], legacy["checkpoint_consent"])
        self.assertEqual(m.migrate(session), session)
        seen = []
        while (question := m.question(session)):
            seen.append(question["id"]); session = reply(session, "skip")
        self.assertNotIn("goal", seen); self.assertNotIn("constraints", seen)

    def test_migration_and_resume_invalidate_old_save_confirmation(self):
        legacy = interview.start(data("all_known"), checkpoint_consent="fictional-checkpoint-consent")
        view = interview.draft(legacy, target="fictional-target", reviewed_plan={})
        legacy = interview.confirm(legacy, view["preview_token"], "fictional-target", "fictional-save-source", {})
        session = m.migrate(legacy)
        self.assertNotIn("confirmation", session)
        self.assertEqual(session["phase"], "preview")
        with self.assertRaises(ValueError):
            interview.confirm(session, view["preview_token"], "fictional-target", "fictional-save-source", {})

    def test_questionnaire_state_is_bound_to_the_existing_save_preview_token(self):
        session = m.start(data("all_known"))
        view = interview.draft(session, target="fictional-target", reviewed_plan={})
        finished = m.finish(session)
        with self.assertRaises(ValueError):
            interview.confirm(finished, view["preview_token"], "fictional-target", "fictional-save-source", {})

    def test_checkpoint_resume_retains_answer_status_and_guards_manual_edits(self):
        session = m.start(data(), checkpoint_consent="fictional-private-state-consent")
        session = m.pause(reply(session, "skip"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "quick-start.json"; m.checkpoint(path, session)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded, digest = m.load_checkpoint(path)
            self.assertIsNone(m.question(loaded))
            resumed = m.resume(loaded)
            self.assertEqual(m.question(resumed)["id"], "goal")
            manual = json.loads(path.read_text()); manual["human_note"] = "Keep this manual edit"
            path.write_text(json.dumps(manual)); before = path.read_bytes()
            with self.assertRaises(space_state.StateError): m.checkpoint(path, resumed, expected_hash=digest)
            self.assertEqual(path.read_bytes(), before)

    def test_checkpoint_requires_consent_and_keeps_private_state_outside_source_and_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "no-consent.json"
            with self.assertRaises(ValueError): m.checkpoint(path, m.start(data()))
            self.assertFalse(path.exists())
        consented = m.start(data(), checkpoint_consent="fictional-private-state-consent")
        forbidden = ROOT / "quick-start-private-state.json"
        with self.assertRaises(space_state.StateError): m.checkpoint(forbidden, consented)
        self.assertFalse(forbidden.exists())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); installed = root / "maintain-personal-context"; installed.mkdir()
            (installed / "SKILL.md").write_text("Synthetic Skill marker")
            with self.assertRaises(space_state.StateError): m.checkpoint(installed / "private-state.json", consented)
            self.assertFalse((installed / "private-state.json").exists())

    def test_legacy_private_bundle_answers_still_validate_and_can_be_reviewed_before_prefill(self):
        # Existing portable schema is retained. A real Agent still supplies the
        # source/key mapping; validating a downloaded answer cannot do that work.
        payload = {"schema_version": 1, "title": "Synthetic legacy bundle", "as_of": STAMP,
                   "sources": [], "coverage": {"scope": "Only synthetic fixture", "limits": ["No real accounts"], "uncovered": []},
                   "facts": [], "advice": [], "questions": [{"id": "fictional-situation", "text": "Current focus?",
                   "reason": "Unknown in checked input", "fact_ids": [], "options": [
                       {"id": "paper", "label": "Paper work"}, {"id": "drawing", "label": "Drawing work"}], "answer": None}]}
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp).resolve() / "legacy-bundle"
            receipt = session_bundle.export(payload, dest)
            bundle = session_bundle.read_bundle(dest, receipt["bundle_digest"])
            downloaded = {"kind": "session_responses", "schema_version": 1, "bundle_digest": receipt["bundle_digest"],
                          "responses": [{"question_id": "fictional-situation", "action": "answer",
                                         "answer": {"option_id": None, "text": "我正在整理纸偶作品"}}]}
            reviewed = session_bundle.validate_responses(bundle, downloaded)
            self.assertTrue(reviewed["needs_agent_review"])
            self.assertFalse(reviewed["knowledge_base_updated"])
            original = data(); original["claims"] = [claim("current_situation", reviewed["responses"][0]["answer"]["text"],
                                                           source_id="fictional-agent-reviewed-download")]
            session = m.start(original)
            self.assertEqual(m.question(session)["id"], "goal")
            self.assertFalse(session.get("confirmation"))

    def test_cli_preview_is_deterministic_and_creates_no_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp).resolve()
            args = [sys.executable, "-B", str(SCRIPTS / "quick_start.py"), "--input", str(FIXTURE), "--case", "new"]
            first = subprocess.run(args, cwd=working, check=True, capture_output=True, text=True)
            second = subprocess.run(args, cwd=working, check=True, capture_output=True, text=True)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(list(working.iterdir()), [])
            self.assertEqual(json.loads(first.stdout)["question"]["id"], "situation")

    def test_no_save_removes_prefilled_body_from_preview_state_checkpoint_and_later_supplements(self):
        secret = "SYNTHETIC-PREFILLED-PRIVATE-CANDIDATE"
        original = data()
        original["claims"] = [claim("current_situation", secret, "fictional-private-summary",
                                     source_kind="memory", author_role="unknown", evidence_tier="summary")]
        original["coverage_gaps"].append("A prior note quoted " + secret)
        before = copy.deepcopy(original)
        session = m.start(original, checkpoint_consent="fictional-private-state-consent")
        self.assertIn(secret, json.dumps(m.draft(session), ensure_ascii=False))
        excluded = reply(session, "do_not_save")
        self.assertEqual(original, before)
        self.assertNotIn(secret, json.dumps(excluded, ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(m.draft(excluded), ensure_ascii=False))
        self.assertNotIn("situation", [row["id"] for row in m.followups(excluded)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "excluded.json"; m.checkpoint(path, excluded)
            self.assertNotIn(secret, path.read_text())
            loaded, _ = m.load_checkpoint(path)
            self.assertNotIn(secret, json.dumps(loaded, ensure_ascii=False))
        with self.assertRaises(ValueError):
            m.supplement(excluded, [claim("current_situation", secret, "fictional-new-source")])
        self.assertNotIn(secret, json.dumps(excluded, ensure_ascii=False))

    def test_legacy_no_save_migration_excludes_original_prefilled_candidate_body(self):
        secret = "SYNTHETIC-LEGACY-EXCLUDED-SUMMARY"
        original = data()
        original["claims"] = [claim("current_problem", secret, "fictional-legacy-summary",
                                     source_kind="memory", author_role="unknown", evidence_tier="summary")]
        legacy = interview.start(original, checkpoint_consent="fictional-legacy-checkpoint-consent")
        legacy = interview.respond(legacy, "situation", "do_not_save", "fictional-legacy-exclusion")
        # The prior reducer retained source candidates; migration must honor its
        # explicit exclusion without modifying that earlier caller-owned state.
        self.assertIn(secret, json.dumps(legacy))
        migrated = m.migrate(legacy)
        self.assertNotIn(secret, json.dumps(migrated))
        self.assertNotIn(secret, json.dumps(m.draft(migrated)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "migrated.json"; m.checkpoint(path, migrated)
            self.assertNotIn(secret, path.read_text())
        with self.assertRaises(ValueError):
            m.supplement(migrated, [claim("current_problem", secret, "fictional-later-summary")])
        self.assertIn(secret, json.dumps(legacy))

    def test_no_save_removes_same_source_statement_projected_into_another_topic(self):
        secret = "SYNTHETIC-SAME-SOURCE-EXCLUDED-PROJECTION"
        original = data()
        source = dict(source_kind="memory", author_role="unknown", evidence_tier="summary")
        original["claims"] = [claim(key, secret, "fictional-one-note", **source)
                              for key in ("current_situation", "near_term_goal")]
        for route in ("reply", "legacy_migration", "import_controls"):
            with self.subTest(route=route):
                if route == "reply":
                    session = m.start(original, checkpoint_consent="fictional-checkpoint-consent")
                    excluded = reply(session, "do_not_save")
                elif route == "legacy_migration":
                    legacy = interview.start(original, checkpoint_consent="fictional-checkpoint-consent")
                    legacy = interview.respond(legacy, "situation", "do_not_save", "fictional-legacy-control")
                    excluded = m.migrate(legacy)
                else:
                    session = m.start(original, checkpoint_consent="fictional-checkpoint-consent")
                    excluded = m.import_decisions(session, [{"topic_id": "situation", "action": "do_not_save",
                        "submission_id": "fictional-imported-exclusion", "source_id": "fictional-reviewed-response#situation"}])
                self.assertNotIn(secret, json.dumps(excluded))
                self.assertNotIn(secret, json.dumps(m.draft(excluded)))
                self.assertIn("near_term_goal", excluded["data"][m.META]["excluded_keys"])
                self.assertNotIn("goal", [row["id"] for row in m.followups(excluded)])
                self.assertNotEqual(m.question(excluded)["id"], "goal")
                with self.assertRaises(ValueError):
                    m.supplement(excluded, [claim("near_term_goal", secret, "fictional-later-source")])
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp).resolve() / "projection.json"
                    m.checkpoint(path, excluded)
                    self.assertNotIn(secret, path.read_text())
                    loaded, _ = m.load_checkpoint(path)
                    self.assertEqual(loaded, excluded)
        self.assertEqual(len(original["claims"]), 2)

    def test_no_save_keeps_unlinked_claims_with_different_source_or_body(self):
        secret = "SYNTHETIC-EXCLUDED-CANDIDATE"
        original = data()
        source = dict(source_kind="memory", author_role="unknown", evidence_tier="summary")
        excluded_claim = claim("current_situation", secret, "fictional-one-note", **source)
        other_source = claim("resources", secret, "fictional-independent-note", **source)
        other_body = claim("skills", "SYNTHETIC-INDEPENDENT-CAPABILITY", "fictional-one-note", **source)
        other_kind = claim("introduction", secret, "fictional-one-note")
        original["claims"] = [excluded_claim, other_source, other_body, other_kind]
        excluded = reply(m.start(original), "do_not_save")
        self.assertEqual(excluded["data"]["claims"], [other_source, other_body, other_kind])
        self.assertNotIn("resources", excluded["data"][m.META]["excluded_keys"])
        self.assertNotIn("skills", excluded["data"][m.META]["excluded_keys"])
        self.assertNotIn("introduction", excluded["data"][m.META]["excluded_keys"])

    def test_unused_raw_history_and_receipt_bodies_are_not_copied_but_read_boundaries_survive(self):
        secret = "SYNTHETIC-UNUSED-HISTORY-BODY"
        original = data("existing")
        original["raw_materials"] = {"full_history": {"text": secret}}
        original["unused"] = [{"history": secret}]
        original["claims"][0]["raw_body"] = secret
        original["entry"]["unused_history_body"] = secret
        original["recommendations"] = [{"label": "先整理一页演示", "basis_keys": ["current_situation"],
                                         "first_step": "列三个演示要点", "confirmation_condition": "只作候选",
                                         "scope": "general", "unused_history_body": secret}]
        check = {"source_kind": "codex_thread", "reader_kind": "codex_thread",
                 "scope_kind": "selected_messages", "requested_message_ids": ["fictional-msg-a", "fictional-msg-missing"],
                 "returned_message_ids": ["fictional-msg-a"], "content_omitted": False,
                 "truncated": False, "previous_cursor": None, "next_cursor": "fictional-next-page",
                 "returned_messages": [{"id": "fictional-msg-a", "content_omitted": False,
                                        "truncated": False, "text": secret}], "raw_body": secret}
        original["source_review"][0]["read_check"] = check
        original["source_review"][0]["full_history"] = secret
        before = copy.deepcopy(original)
        session = m.start(original, checkpoint_consent="fictional-minimal-checkpoint-consent")
        self.assertEqual(original, before)
        self.assertNotIn(secret, json.dumps(session))
        expected_claims = copy.deepcopy(original["claims"])
        expected_claims[0].pop("raw_body")
        self.assertEqual(session["data"]["claims"], expected_claims)
        stored_check = session["data"]["source_review"][0]["read_check"]
        self.assertEqual(stored_check["requested_message_ids"], check["requested_message_ids"])
        self.assertEqual(stored_check["returned_message_ids"], check["returned_message_ids"])
        self.assertEqual(stored_check["next_cursor"], check["next_cursor"])
        self.assertEqual(stored_check["returned_messages"], [{"id": "fictional-msg-a", "content_omitted": False,
                                                              "truncated": False}])
        view = m.draft(session)
        receipt = next(r for r in view["coverage"]["items"] if r["role"] == "personal_profile")
        self.assertEqual(receipt["status"], "partial")
        self.assertEqual(receipt["read_check"]["target_completeness"]["fictional-msg-missing"], "missing")
        self.assertIn("personal_profile", view["coverage"]["continuation_roles"])
        self.assertIsNone(view["question"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "minimal.json"; m.checkpoint(path, session)
            self.assertNotIn(secret, path.read_text())
            loaded, _ = m.load_checkpoint(path)
            self.assertEqual(loaded["data"]["claims"], expected_claims)
            self.assertIsNone(m.question(loaded))

    def test_legacy_bundle_control_mapping_handles_known_exclusion_and_out_of_order_skips(self):
        secret = "SYNTHETIC-LEGACY-CONTROL-EXCLUDED-RESOURCE"
        payload = {"schema_version": 1, "title": "Synthetic legacy controls", "as_of": STAMP,
                   "sources": [{"id": "fictional-old-note", "title": "Synthetic old note", "locator": "Synthetic excerpt",
                                "kind": "synthetic", "read_status": "read", "time_range": "event date unknown",
                                "limitations": ["Synthetic fixture only"], "snapshot": secret}],
                   "coverage": {"scope": "Only synthetic controls", "limits": ["No real account access"], "uncovered": []},
                   "facts": [{"id": "fictional-resource", "text": secret, "status": "current", "category": "capability",
                              "source_ids": ["fictional-old-note"], "sensitivity": "sensitive"}], "advice": [],
                   "questions": [{"id": qid, "text": "Synthetic control " + qid, "reason": "Previously requested review",
                                  "fact_ids": ["fictional-resource"] if qid == "old-resources" else [],
                                  "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}], "answer": None}
                                 for qid in ("old-resources", "old-constraints", "old-identity")]}
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp).resolve() / "legacy-controls"
            receipt = session_bundle.export(payload, bundle_path)
            bundle = session_bundle.read_bundle(bundle_path, receipt["bundle_digest"])
            downloaded = {"kind": "session_responses", "schema_version": 1, "bundle_digest": receipt["bundle_digest"],
                          "responses": [{"question_id": qid, "action": action, "answer": None}
                                        for qid, action in (("old-resources", "do_not_save"),
                                                            ("old-constraints", "skip"), ("old-identity", "uncertain"))]}
            reviewed = session_bundle.validate_responses(bundle, downloaded)
            mapping = {"old-resources": "resources", "old-constraints": "constraints", "old-identity": "identity"}
            decisions = [{"topic_id": mapping[row["question_id"]], "action": row["action"],
                          "submission_id": "fictional-control-" + row["question_id"],
                          "source_id": "fictional-reviewed-download#" + row["question_id"]}
                         for row in reviewed["responses"]]
            original = data("existing")
            original["claims"].append(claim("resources", secret, "fictional-reviewed-old-note"))
            session = m.start(original, checkpoint_consent="fictional-private-control-consent")
            self.assertEqual(m.question(session)["id"], "goal")
            self.assertTrue(any(row["id"] == "resources" and row["state"] == "known" for row in m.prefilled(session)))
            migrated = m.import_decisions(session, decisions)
            self.assertEqual(m.question(migrated)["id"], "goal")
            self.assertEqual(migrated["decisions"], {"resources": "do_not_save", "constraints": "skip", "identity": "uncertain"})
            self.assertNotIn(secret, json.dumps(migrated))
            self.assertNotIn(secret, json.dumps(m.draft(migrated)))
            self.assertEqual(m.import_decisions(migrated, decisions), migrated)
            checkpoint = Path(tmp).resolve() / "controls.json"; m.checkpoint(checkpoint, migrated)
            loaded, _ = m.load_checkpoint(checkpoint)
            self.assertEqual(loaded["decisions"], migrated["decisions"])
            self.assertNotIn(secret, checkpoint.read_text())
            seen = []
            while (question := m.question(loaded)):
                seen.append(question["id"]); loaded = reply(loaded, "skip")
            self.assertFalse({"resources", "constraints", "identity"}.intersection(seen))
            self.assertEqual(bundle_path.joinpath("session.json").read_bytes(),
                             session_bundle.encode(bundle))

    def test_imported_controls_are_atomic_idempotent_and_reject_conflicting_replays(self):
        session = m.start(data())
        row = {"topic_id": "resources", "action": "skip", "submission_id": "fictional-import-id",
               "source_id": "fictional-download#resources"}
        imported = m.import_decisions(session, [row]); before = copy.deepcopy(imported)
        self.assertEqual(m.import_decisions(imported, [row]), imported)
        self.assertEqual(m.import_decisions(imported, [row])["revision"], imported["revision"])
        with self.assertRaises(ValueError):
            m.import_decisions(imported, [dict(row, source_id="fictional-other-download")])
        with self.assertRaises(ValueError):
            m.import_decisions(imported, [dict(row, action="uncertain", submission_id="fictional-different-id")])
        # A later conflicting row cannot partly apply an earlier valid control.
        with self.assertRaises(ValueError):
            m.import_decisions(imported, [dict(row, topic_id="constraints", submission_id="fictional-first-valid"),
                                          dict(row, action="do_not_save", submission_id="fictional-then-conflict")])
        self.assertEqual(imported, before)
        for invalid in (dict(row, action="answer"), dict(row, topic_id="invented"),
                        dict(row, source_id=""), dict(row, unused_history_body="SYNTHETIC-UNEXPECTED")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                m.import_decisions(session, [invalid])

    def test_importing_controls_keeps_pause_and_invalidates_old_confirmation(self):
        session = m.start(data("all_known"))
        view = interview.draft(session, target="fictional-target", reviewed_plan={})
        confirmed = interview.confirm(session, view["preview_token"], "fictional-target", "fictional-save-source", {})
        row = {"topic_id": "resources", "action": "skip", "submission_id": "fictional-import-confirmed",
               "source_id": "fictional-download#resources"}
        imported = m.import_decisions(confirmed, [row])
        self.assertEqual(imported["phase"], "preview")
        self.assertNotIn("confirmation", imported)
        with self.assertRaises(ValueError):
            interview.confirm(imported, view["preview_token"], "fictional-target", "fictional-save-source", {})
        paused = m.pause(m.start(data()))
        imported = m.import_decisions(paused, [row])
        self.assertEqual(imported["phase"], "paused")
        self.assertIsNone(m.question(imported))

    def test_generic_choices_leave_deferred_capability_and_resources_as_optional_followups(self):
        session = m.start(data()); seen = []
        while (question := m.question(session)):
            seen.append(question["id"])
            session = reply(session, "choice", choice_id=question["choices"][0]["id"])
        self.assertEqual(seen, TOPIC_IDS)
        pending = {item["id"]: item for item in m.followups(session)}
        self.assertTrue({"capability", "resources"}.issubset(pending))
        for topic in ("capability", "resources"):
            self.assertTrue(pending[topic]["optional"])
            self.assertEqual(pending[topic]["reason"], "details_pending")
        self.assertTrue(m.draft(session)["progress"]["usable_now"])
        self.assertFalse(session.get("confirmation"))

    def test_known_current_problem_also_satisfies_goal_without_reasking_old_fixtures(self):
        fixtures = json.loads((ROOT / "tests/fixtures/first-run.json").read_text())
        for case in ("sparse", "rich"):
            with self.subTest(case=case):
                session = m.start(fixtures[case]); seen = []
                while (question := m.question(session)):
                    seen.append(question["id"]); session = reply(session, "skip")
                self.assertNotIn("goal", seen)
                self.assertNotIn("situation", seen)
                goal = next(row for row in m.draft(session)["prefilled"] if row["id"] == "goal")
                self.assertTrue(any(value["key"] == "current_problem" and value["current"] for value in goal["values"]))

    def test_supplement_replay_is_idempotent_changed_identity_rejected_and_pause_preserved(self):
        session = m.pause(m.start(data()))
        additions = [claim("current_situation", "正在整理纸偶作品", "fictional-replayed-source")]
        supplemented = m.supplement(session, additions)
        before = copy.deepcopy(supplemented)
        self.assertEqual(supplemented["phase"], "paused")
        self.assertIsNone(m.question(supplemented))
        self.assertEqual(m.supplement(supplemented, additions), supplemented)
        self.assertEqual(m.supplement(supplemented, additions)["revision"], supplemented["revision"])
        changed = [dict(additions[0], value="另一件事")]
        with self.assertRaises(ValueError): m.supplement(supplemented, changed)
        self.assertEqual(supplemented, before)
        self.assertEqual(m.question(m.resume(supplemented))["id"], "goal")

    def test_excluding_body_equal_to_read_preserves_source_status_ids_and_receipt_enums(self):
        original = data()
        original["claims"] = [claim("current_situation", "read", "read", source_kind="memory",
                                     author_role="unknown", evidence_tier="summary")]
        original["source_review"][0] = {"role": "personal_profile", "status": "read",
                                         "scope": "The note literally says read", "source_ids": ["read"],
                                         "limitations": ["Only read was returned"],
                                         "read_check": {"source_kind": "codex_thread", "reader_kind": "codex_thread",
                                                        "scope_kind": "selected_messages", "requested_message_ids": ["read"],
                                                        "returned_message_ids": ["read"], "content_omitted": False,
                                                        "truncated": False, "next_cursor": None}}
        session = m.start(original, checkpoint_consent="fictional-private-state-consent")
        excluded = reply(session, "do_not_save")
        self.assertEqual(excluded["decisions"]["situation"], "do_not_save")
        self.assertEqual(excluded["data"]["claims"], [])
        stored = excluded["data"]["source_review"][0]
        self.assertEqual(stored["status"], "read")
        self.assertEqual(stored["source_ids"], ["read"])
        self.assertEqual(stored["read_check"], session["data"]["source_review"][0]["read_check"])
        self.assertNotIn("literally says read", stored["scope"])
        receipt = next(row for row in m.draft(excluded)["coverage"]["items"] if row["role"] == "personal_profile")
        self.assertEqual(receipt["status"], "read")
        self.assertEqual(receipt["read_check"]["target_completeness"], {"read": "complete"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "read-enum.json"; m.checkpoint(path, excluded)
            loaded, _ = m.load_checkpoint(path)
            self.assertEqual(loaded["data"]["source_review"][0]["status"], "read")
            self.assertEqual(loaded["data"]["source_review"][0]["source_ids"], ["read"])


if __name__ == "__main__":
    unittest.main()
