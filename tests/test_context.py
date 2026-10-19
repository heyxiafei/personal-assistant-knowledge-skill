"""Only fictional adult data. Real storage/CLI invariants, no external services."""
from __future__ import annotations
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills/maintain-personal-context"
spec = importlib.util.spec_from_file_location("context_store", SKILL / "scripts/context_store.py")
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)
ispec = importlib.util.spec_from_file_location("local_install", REPO / "scripts/install.py")
installer = importlib.util.module_from_spec(ispec)
ispec.loader.exec_module(installer)


def fixture(name):
    return json.loads((SKILL / "examples" / name).read_text())


def one(**kwargs):
    r = copy.deepcopy(fixture("first-profile.json")["records"][0])
    r.update(kwargs)
    return {"version": 1, "records": [r]}


def observed(bundle):
    """Synthetic user evidence for tests which replace an unknown fact."""
    r = bundle["records"][0]
    r["text"] = "我确认：" + r["text"].rstrip("。")
    r["assertion"] = {"state": "observed", "author_role": "user",
                      "source": copy.deepcopy(r["sources"][0]),
                      "source_text": r["text"], "statement": r["text"]}
    return bundle


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "private"
        store.initialize(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, bundle):
        return store.apply_plan(self.root, store.make_plan(self.root, bundle))

    def records(self, file="PERSON_PROFILE.md"):
        return store.parse((self.root / file).read_text())[0]

    def test_blank_init_and_second_init_preserve_user_notes(self):
        p = self.root / "INDEX.md"
        p.write_text(p.read_text() + "\n人工备注：保留\n")
        before = {k: v for k, v in store.inventory(self.root).items()}
        self.assertEqual(store.initialize(self.root)["status"], "already_initialized")
        self.assertEqual(store.inventory(self.root), before)
        self.assertEqual(self.records(), [])

    def test_nonempty_destination_is_preserved(self):
        root = self.base / "existing"
        root.mkdir()
        note = root / "note.txt"
        note.write_text("保留")
        with self.assertRaises(store.ContextError):
            store.initialize(root)
        self.assertEqual(note.read_text(), "保留")
        self.assertFalse((root / "INDEX.md").exists())

    def test_first_profile_and_unknown_do_not_invent_fields(self):
        result = self.apply(fixture("first-profile.json"))
        self.assertEqual(result["status"], "committed")
        records = self.records()
        self.assertEqual(len(records), 4)
        unknown = next(r for r in records if r["status"] == "unknown")
        self.assertIsNone(unknown["event_date"])
        self.assertIsNone(unknown["confirmed_on"])
        self.assertEqual(unknown["sources"], [])

    def test_confirmed_answer_replaces_unknown_without_inventing_old_evidence(self):
        first = fixture("first-profile.json")
        unknown = copy.deepcopy(first["records"][-1])
        self.apply(first)
        answer = observed(one(id="collaboration-answer", category=unknown["category"],
                     text="可协作资源：用户明确提供了一份虚构设计资源清单。",
                     record_date="2026-09-03", confirmed_on="2026-09-03",
                     confirmation="虚构用户指令F：保存这份资源答案",
                     supersedes=[unknown["id"]]))
        self.assertEqual(self.apply(answer)["status"], "committed")
        byid = {r["id"]: r for r in self.records()}
        old = byid[unknown["id"]]
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["original_status"], "unknown")
        self.assertEqual(old["superseded_by"], "collaboration-answer")
        restored = copy.deepcopy(old)
        restored["status"] = restored.pop("original_status")
        restored.pop("superseded_by")
        self.assertEqual(restored, unknown)
        self.assertEqual(byid["collaboration-answer"]["status"], "confirmed")
        self.assertEqual(self.apply(answer)["status"], "no_change")
        self.assertEqual(self.apply(first)["status"], "no_change")

    def test_replay_old_source_with_new_id_does_not_reactivate_it(self):
        first = fixture("first-profile.json")
        self.apply(first)
        self.apply(fixture("correction.json"))
        replay = copy.deepcopy(first["records"][1])
        replay["id"] = "illustration-practice-again"
        before = store.inventory(self.root)
        result = self.apply({"version": 1, "records": [replay]})
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(result["outcome"][0]["status"], "historical_duplicate")
        self.assertEqual(result["outcome"][0]["into"], "illustration-practice")
        self.assertEqual(store.inventory(self.root), before)
        byid = {r["id"]: r for r in self.records()}
        self.assertNotIn("illustration-practice-again", byid)
        self.assertEqual(byid["illustration-practice"]["status"], "superseded")
        self.assertEqual(byid["illustration-learning"]["status"], "confirmed")

    def test_historical_source_subset_replay_after_source_merge_is_no_change(self):
        first = fixture("first-profile.json")
        s1 = copy.deepcopy(first["records"][1]["sources"][0])
        s2 = {"label": "第二份虚构自述", "locator": "插画经验第1段"}
        for index, sources in enumerate(([s1], [s2], [s2, s1], [s1, s1])):
            with self.subTest(sources=sources):
                self.root = self.base / ("historical-subset-" + str(index))
                store.initialize(self.root)
                self.apply(first)
                supplemental = dict(first["records"][1], id="illustration-extra-source",
                                    sources=[s2])
                self.apply({"version": 1, "records": [supplemental]})
                self.apply(fixture("correction.json"))
                before = store.inventory(self.root)
                replay = dict(first["records"][1], id="illustration-subset-replay",
                              sources=sources)
                result = self.apply({"version": 1, "records": [replay]})
                self.assertEqual(result["status"], "no_change")
                self.assertEqual(result["outcome"][0]["status"], "historical_duplicate")
                self.assertEqual(result["outcome"][0]["into"], "illustration-practice")
                self.assertEqual(store.inventory(self.root), before)
                byid = {r["id"]: r for r in self.records()}
                self.assertEqual(byid["illustration-practice"]["sources"], [s1, s2])
                self.assertEqual(byid["illustration-practice"]["status"], "superseded")
                self.assertEqual(byid["illustration-learning"]["status"], "confirmed")
                self.assertNotIn("illustration-subset-replay", byid)

    def test_profile_and_longterm_rerecord_date_does_not_reactivate_history(self):
        for layer in ("profile", "longterm"):
            with self.subTest(layer=layer):
                self.root = self.base / ("rerecorded-" + layer)
                store.initialize(self.root)
                first = fixture("first-profile.json")
                first["records"][1]["layer"] = layer
                self.apply(first)
                correction = fixture("correction.json")
                correction["records"][0]["layer"] = layer
                self.apply(correction)
                before = store.inventory(self.root)
                replay = dict(first["records"][1], id="rerecorded-old-statement",
                              record_date="2026-09-04")
                result = self.apply({"version": 1, "records": [replay]})
                self.assertEqual(result["status"], "no_change")
                self.assertEqual(result["outcome"][0]["status"], "historical_duplicate")
                self.assertEqual(store.inventory(self.root), before)

    def test_historical_replay_source_decision_table(self):
        first = fixture("first-profile.json")
        s1 = copy.deepcopy(first["records"][1]["sources"][0])
        s2 = {"label": "已合并的第二份虚构材料", "locator": "第1段"}
        s3 = {"label": "新增的第三份虚构材料", "locator": "第1段"}
        cases = (("no-source-confirmed", [], "source-required"),
                 ("known-subset", [s1], "duplicate"),
                 ("known-reordered", [s2, s1], "duplicate"),
                 ("known-repeated", [s1, s1], "duplicate"),
                 ("new-only", [s3], "review"),
                 ("mixed", [s1, s3], "review"),
                 ("new-repeated", [s3, s3], "review"),
                 ("known-plus-new", [s1, s2, s3], "review"))
        for name, sources, expected in cases:
            with self.subTest(case=name):
                self.root = self.base / name
                store.initialize(self.root)
                self.apply(first)
                supplemental = dict(first["records"][1], id="merge-known-source", sources=[s2])
                self.apply({"version": 1, "records": [supplemental]})
                self.apply(fixture("correction.json"))
                before = store.inventory(self.root)
                replay = dict(first["records"][1], id="decision-table-replay", sources=sources,
                              record_date="2026-09-04")
                bundle = {"version": 1, "records": [replay]}
                if expected == "duplicate":
                    result = self.apply(bundle)
                    self.assertEqual(result["status"], "no_change")
                    self.assertEqual(result["outcome"][0]["status"], "historical_duplicate")
                else:
                    error = "non-unknown records need a source" if expected == "source-required" else "historical statement has new sources"
                    with self.assertRaisesRegex(store.ContextError, error):
                        self.apply(bundle)
                self.assertEqual(store.inventory(self.root), before)
                byid = {r["id"]: r for r in self.records()}
                self.assertEqual(byid["illustration-practice"]["sources"], [s1, s2])
                self.assertEqual(byid["illustration-practice"]["status"], "superseded")
                self.assertEqual(byid["illustration-learning"]["status"], "confirmed")
                self.assertNotIn("decision-table-replay", byid)

    def test_sourceless_unknown_history_replay_is_not_a_current_fact(self):
        first = fixture("first-profile.json")
        unknown = copy.deepcopy(first["records"][-1])
        self.apply(first)
        self.apply(observed(one(id="known-resource-answer", category=unknown["category"],
                       text="一项明确提供的虚构协作资源。", supersedes=[unknown["id"]])))
        before = store.inventory(self.root)
        replay = dict(unknown, id="unknown-replayed", record_date="2026-09-04")
        result = self.apply({"version": 1, "records": [replay]})
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(result["outcome"][0]["status"], "historical_duplicate")
        self.assertEqual(store.inventory(self.root), before)
        replay["sources"] = [{"label": "新增的虚构资源材料", "locator": "第1段"}]
        with self.assertRaisesRegex(store.ContextError, "historical statement has new sources"):
            self.apply({"version": 1, "records": [replay]})
        self.assertEqual(store.inventory(self.root), before)

    def test_dated_replacement_requires_explicit_caller_date(self):
        self.apply(one())
        before = store.inventory(self.root)
        future = one(id="future-role", text="下月起改为虚构新身份。",
                     valid_from="2026-10-01", supersedes=["adult-role"])
        with self.assertRaisesRegex(store.ContextError, "as_of"):
            self.apply(future)
        self.assertEqual(store.inventory(self.root), before)

    def test_future_change_is_a_plan_until_explicit_effective_update(self):
        self.apply(one())
        future = one(id="future-role", text="计划从下月1日起改为虚构新身份。",
                     valid_from="2026-10-01", supersedes=["adult-role"])
        future["as_of"] = "2026-09-01"
        before = store.inventory(self.root)
        with self.assertRaisesRegex(store.ContextError, "future-effective"):
            self.apply(future)
        self.assertEqual(store.inventory(self.root), before)
        future["records"][0].update(layer="daily", supersedes=[])
        self.apply(future)
        self.assertEqual(self.records()[0]["status"], "confirmed")
        planned = self.records("daily/2026-09-01.md")[0]
        self.assertEqual(planned["valid_from"], "2026-10-01")
        self.assertEqual(planned["supersedes"], [])
        effective = one(id="effective-role", text="现已改为虚构新身份。",
                        record_date="2026-10-01", confirmed_on="2026-10-01",
                        confirmation="虚构用户指令G：生效后明确更新身份",
                        valid_from="2026-10-01", supersedes=["adult-role"])
        effective["as_of"] = "2026-10-01"
        self.apply(effective)
        byid = {r["id"]: r for r in self.records()}
        self.assertEqual(byid["adult-role"]["superseded_by"], "effective-role")
        self.assertEqual(byid["effective-role"]["status"], "confirmed")

    def test_invalid_comparison_date_and_future_profile_are_rejected(self):
        self.apply(one())
        before = store.inventory(self.root)
        for as_of in (None, "2026-02-30", "yesterday"):
            bundle = one(id="dated-entry", valid_from="2026-10-01")
            bundle["as_of"] = as_of
            with self.subTest(as_of=as_of), self.assertRaises(store.ContextError):
                self.apply(bundle)
        bundle["as_of"] = "2026-09-01"
        with self.assertRaisesRegex(store.ContextError, "future-effective"):
            self.apply(bundle)
        self.assertEqual(store.inventory(self.root), before)

    def test_unknown_history_remains_readable_after_a_second_correction(self):
        self.apply(fixture("first-profile.json"))
        first = observed(one(id="resource-answer", category="resources-collaboration",
                    text="已确认的一份虚构资源清单。", supersedes=["collaboration-unknown"]))
        self.apply(first)
        second = one(id="resource-answer-corrected", category="resources-collaboration",
                     text="经明确更正后的虚构资源清单。", supersedes=["resource-answer"])
        self.apply(second)
        byid = {r["id"]: r for r in self.records()}
        self.assertEqual(byid["collaboration-unknown"]["sources"], [])
        self.assertIsNone(byid["collaboration-unknown"]["confirmed_on"])
        self.assertEqual(byid["collaboration-unknown"]["original_status"], "unknown")
        self.assertEqual(byid["resource-answer"]["original_status"], "confirmed")
        self.assertEqual(self.apply(first)["status"], "no_change")
        self.assertEqual(self.apply(fixture("first-profile.json"))["status"], "no_change")
        store.validate_state(byid)

    def test_sourceless_history_exception_cannot_bypass_evidence_or_graph(self):
        unknown = copy.deepcopy(fixture("first-profile.json")["records"][-1])
        unknown.update(status="superseded", original_status="unknown", superseded_by="answer")
        with self.assertRaisesRegex(store.ContextError, "replacement target"):
            store.validate_state({unknown["id"]: unknown})
        for change in ({"original_status": "confirmed"}, {"original_status": "pending"},
                       {"original_status": None}, {"original_status": "unknown", "confirmation": "编造确认"}):
            altered = dict(unknown, **change)
            with self.subTest(change=change), self.assertRaises(store.ContextError):
                store.validate(altered, incoming=False)
        with self.assertRaises(store.ContextError):
            self.apply(one(original_status="unknown", sources=[]))

    def test_legacy_confirmed_history_new_id_replay_is_recognized(self):
        first = fixture("first-profile.json")
        self.apply(first)
        self.apply(fixture("correction.json"))
        p = self.root / "PERSON_PROFILE.md"
        records, prefix, suffix = store.parse(p.read_text())
        for record in records:
            record.pop("original_status", None)
        p.write_text(prefix + store.render(records) + suffix)
        before = store.inventory(self.root)
        replay = dict(first["records"][1], id="legacy-replay")
        self.assertEqual(self.apply({"version": 1, "records": [replay]})["status"], "no_change")
        self.assertEqual(store.inventory(self.root), before)

    def test_new_confirmation_or_distinct_dates_are_not_historical_replays(self):
        changes = ({"record_date": "2026-09-04", "confirmed_on": "2026-09-04",
                    "confirmation": "虚构用户新指令：现在重新确认该表述"},
                   {"confirmation": "虚构用户同日新指令：现在明确重新确认"},
                   {"event_date": "2026-09-04"}, {"valid_from": "2026-09-04"})
        for index, change in enumerate(changes):
            with self.subTest(change=change):
                self.root = self.base / ("distinct-" + str(index))
                store.initialize(self.root)
                first = fixture("first-profile.json")
                self.apply(first)
                supplemental = dict(first["records"][1], id="supplemental-before-correction",
                                    sources=[{"label": "补充虚构材料", "locator": "第1段"}])
                self.apply({"version": 1, "records": [supplemental]})
                self.apply(fixture("correction.json"))
                new = dict(first["records"][1], id="new-explicit-record", **change)
                new["sources"] += [{"label": "新提供的虚构材料", "locator": "第1段"}]
                self.apply({"version": 1, "as_of": "2026-09-04", "records": [new]})
                byid = {r["id"]: r for r in self.records()}
                self.assertEqual(byid["new-explicit-record"]["status"], "confirmed")
                self.assertEqual(byid["illustration-practice"]["status"], "superseded")

    def test_incremental_log_and_longterm_separation(self):
        self.apply(fixture("first-profile.json"))
        self.apply(fixture("daily-update.json"))
        self.assertEqual(len(self.records()), 4)
        self.assertEqual(len(self.records("daily/2026-09-02.md")), 3)
        self.assertEqual(self.records("LONG_TERM.md"), [])
        self.assertNotIn("疲惫", (self.root / "PERSON_PROFILE.md").read_text())

    def test_repeated_input_is_no_change(self):
        data = fixture("first-profile.json")
        self.apply(data)
        before = store.inventory(self.root)
        self.assertEqual(self.apply(data)["status"], "no_change")
        self.assertEqual(store.inventory(self.root), before)

    def test_semantic_duplicate_merges_sources(self):
        self.apply(one())
        new = one(id="adult-role-again", sources=[{"label": "另一次虚构自述", "locator": "第1段"}])
        self.apply(new)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(self.records()[0]["sources"]), 2)
        self.assertEqual(self.apply(new)["status"], "no_change")

    def test_same_text_on_distinct_event_days_not_deduped(self):
        self.apply(one(id="event-one", layer="daily", category="daily-projects",
                       event_date="2026-09-01", text="今天试了排版。"))
        self.apply(one(id="event-two", layer="daily", category="daily-projects",
                       event_date="2026-09-02", record_date="2026-09-02", text="今天试了排版。"))
        self.assertEqual(len(self.records("daily/2026-09-01.md")), 1)
        self.assertEqual(len(self.records("daily/2026-09-02.md")), 1)
        self.apply(one(id="event-one-corrected", layer="daily", category="daily-projects",
                       record_date="2026-09-03", event_date="2026-09-01",
                       text="更正：那天只检查了排版。", supersedes=["event-one"],
                       confirmed_on="2026-09-03", confirmation="虚构用户更正那天的事件"))
        self.apply(one(id="event-three", layer="daily", category="daily-projects",
                       record_date="2026-09-04", event_date="2026-09-04", text="今天试了排版。",
                       confirmed_on="2026-09-04", confirmation="虚构用户确认今天的新事件"))
        self.assertEqual(self.records("daily/2026-09-01.md")[0]["status"], "superseded")
        self.assertEqual(self.records("daily/2026-09-04.md")[0]["id"], "event-three")
        self.assertEqual(self.records("daily/2026-09-04.md")[0]["status"], "confirmed")

    def test_confirmed_correction_supersedes_and_preserves_old_evidence(self):
        self.apply(fixture("first-profile.json"))
        self.apply(fixture("correction.json"))
        records = {r["id"]: r for r in self.records()}
        old, new = records["illustration-practice"], records["illustration-learning"]
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by"], new["id"])
        self.assertEqual(new["valid_from"], "2026-09-03")
        self.assertEqual(old["sources"][0]["locator"], "第2段")
        self.assertEqual(self.records("LONG_TERM.md")[0]["kind"], "inference")

    def test_two_corrections_replay_and_increment_remain_readable(self):
        self.apply(fixture("first-profile.json"))
        correction = fixture("correction.json")
        self.apply(correction)
        third = copy.deepcopy(correction["records"][0])
        third.update(id="illustration-third", text="正在学习插画基础，并完成了一份练习。",
                     supersedes=["illustration-learning"], record_date="2026-09-04",
                     valid_from="2026-09-04", confirmed_on="2026-09-04",
                     confirmation="虚构用户指令E：再次更正插画学习进度")
        bundle = {"version": 1, "as_of": "2026-09-04", "records": [third]}
        self.apply(bundle)
        byid = {r["id"]: r for r in self.records()}
        self.assertEqual(byid["illustration-practice"]["superseded_by"], "illustration-learning")
        self.assertEqual(byid["illustration-learning"]["superseded_by"], "illustration-third")
        self.assertEqual(byid["illustration-learning"]["status"], "superseded")
        self.assertEqual(byid["illustration-third"]["status"], "confirmed")
        self.assertEqual(self.apply(bundle)["status"], "no_change")
        self.assertEqual(self.apply(correction)["status"], "no_change")
        self.assertEqual(self.apply(fixture("first-profile.json"))["status"], "no_change")
        self.apply(one(id="new-increment", text="另一项虚构角色说明。"))
        self.assertEqual(len(self.records()), 7)

    def test_conflicting_id_rejected_without_overwrite(self):
        self.apply(one())
        before = store.inventory(self.root)
        with self.assertRaisesRegex(store.ContextError, "existing id differs"):
            self.apply(one(text="另一种表述"))
        self.assertEqual(store.inventory(self.root), before)

    def test_replay_old_source_after_correction_does_not_reactivate_it(self):
        self.apply(fixture("first-profile.json"))
        self.apply(fixture("correction.json"))
        before = store.inventory(self.root)
        self.assertEqual(self.apply(fixture("first-profile.json"))["status"], "no_change")
        self.assertEqual(store.inventory(self.root), before)
        old = next(r for r in self.records() if r["id"] == "illustration-practice")
        self.assertEqual(old["status"], "superseded")

    def test_planning_uninitialized_root_does_not_create_files(self):
        root = self.base / "not-created"
        with self.assertRaises(store.ContextError):
            with store.lock(root):
                pass
        self.assertFalse(root.exists())

    def test_pending_longterm_and_unconfirmed_replacement_rejected(self):
        for bundle in (one(layer="longterm", status="pending", confirmed_on=None, confirmation=None),
                       one(layer="daily", status="pending", confirmed_on=None, confirmation=None,
                           supersedes=["other"])):
            with self.assertRaises(store.ContextError):
                self.apply(bundle)
        self.assertEqual(self.records(), [])

    def test_confirmation_source_and_dates_required(self):
        for change in ({"confirmation": None}, {"confirmed_on": None}, {"sources": []},
                       {"event_date": "2026-02-30"}, {"record_date": None}):
            with self.assertRaises(store.ContextError):
                self.apply(one(**change))
        self.assertEqual(self.records(), [])

    def test_replacement_cannot_turn_advice_into_fact(self):
        self.apply(one(kind="recommendation"))
        with self.assertRaises(store.ContextError):
            self.apply(one(id="replacement", kind="fact", supersedes=["adult-role"]))
        self.assertEqual(self.records()[0]["status"], "confirmed")

    def test_user_notes_outside_block_preserved(self):
        p = self.root / "PERSON_PROFILE.md"
        p.write_text(p.read_text() + "\n人工追加：这个注释必须保留\n")
        self.apply(one())
        self.assertTrue(p.read_text().endswith("人工追加：这个注释必须保留\n"))

    def test_user_edit_inside_block_rejected_and_retained(self):
        self.apply(one())
        p = self.root / "PERSON_PROFILE.md"
        original = p.read_bytes()
        p.write_text(p.read_text().replace("## 已整理条目", "## 我的人工标题"))
        before = p.read_bytes()
        for _ in range(2):
            with self.assertRaisesRegex(store.ContextError, "user changed managed block"):
                self.apply(one(id="new-role"))
            self.assertEqual(p.read_bytes(), before)
        preserved = self.base / "hand-edited-copy.md"
        preserved.write_bytes(before)
        # Explicit manual reconciliation: retain edits, then restore a known valid block.
        p.write_bytes(original + "\n人工备注：我的人工标题已保存在手改副本。\n".encode())
        self.apply(one(id="after-manual-reconciliation", text="另一项虚构新资料。"))
        self.assertEqual(preserved.read_bytes(), before)
        self.assertIn("人工备注：我的人工标题", p.read_text())

    def test_stale_plan_does_not_overwrite_human_change(self):
        plan = store.make_plan(self.root, one())
        p = self.root / "INDEX.md"
        p.write_text(p.read_text() + "\n手动改动\n")
        before = store.inventory(self.root)
        with self.assertRaisesRegex(store.ContextError, "file conflict"):
            store.apply_plan(self.root, plan)
        self.assertEqual(store.inventory(self.root), before)

    def test_modified_plan_rejected(self):
        plan = store.make_plan(self.root, one())
        plan["changes"]["../outside.md"] = "不得写出"
        with self.assertRaises(store.ContextError):
            store.apply_plan(self.root, plan)
        self.assertFalse((self.base / "outside.md").exists())

    def test_symlink_and_hardlink_content_rejected(self):
        target = self.base / "external.md"
        target.write_text("外部资料")
        p = self.root / "PERSON_PROFILE.md"
        p.unlink()
        p.symlink_to(target)
        with self.assertRaises(store.ContextError):
            self.apply(one())
        self.assertEqual(target.read_text(), "外部资料")
        p.unlink()
        os.link(target, p)
        with self.assertRaises(store.ContextError):
            self.apply(one())
        self.assertEqual(target.read_text(), "外部资料")

    def test_private_output_in_public_repo_rejected(self):
        with self.assertRaises(store.ContextError):
            store.check_root(str(REPO / "my-private-context"))
        with self.assertRaises(store.ContextError):
            store.check_root(str(SKILL / "output"))

    def test_symlink_ancestor_cannot_put_private_context_in_public_copy(self):
        import shutil
        clone = self.base / "public-copy"
        shutil.copytree(REPO, clone, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        alias = self.base / "alias"
        alias.symlink_to(clone / "docs", target_is_directory=True)
        indirect = alias / "private-context"
        with self.assertRaises(store.ContextError):
            checked = store.check_root(str(indirect))
            store.initialize(checked)
        self.assertFalse((clone / "docs/private-context").exists())

    def test_symlink_ancestor_cannot_receive_private_plan(self):
        import shutil
        clone = self.base / "public-copy"
        shutil.copytree(REPO, clone, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        alias = self.base / "alias"
        alias.symlink_to(clone / "docs", target_is_directory=True)
        indirect = alias / "nested"
        indirect.mkdir()
        input_path = self.root / "input.json"
        input_path.write_text(json.dumps(one(), ensure_ascii=False))
        result = store.main(["--root", str(self.root), "plan", "--input", str(input_path),
                             "--output", str(indirect / "private-plan.json")])
        self.assertEqual(result, 2)
        self.assertFalse((clone / "docs/nested/private-plan.json").exists())

    def test_permission_failure_is_reported_without_data_loss(self):
        # Injection makes the OS permission failure reproducible across owner/root environments.
        plan = store.make_plan(self.root, one())
        before = store.inventory(self.root)
        with patch.object(store, "atomic_write", side_effect=PermissionError("denied")):
            with self.assertRaises(store.ContextError):
                store.apply_plan(self.root, plan)
        self.assertEqual((self.root / "PERSON_PROFILE.md").read_bytes(), before["PERSON_PROFILE.md"])
        journals = list((self.root / ".context/transactions").glob("*/journal.json"))
        self.assertEqual(len(journals), 1)
        result = store.recover(self.root, journals[0].parent.name)
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(store.inventory(self.root), before)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "OS owner permission test is not meaningful as root")
    def test_real_readonly_directory_keeps_original(self):
        plan = store.make_plan(self.root, one())
        before = store.inventory(self.root)
        os.chmod(self.root, 0o500)
        try:
            with self.assertRaises(PermissionError):
                store.apply_plan(self.root, plan)
        finally:
            os.chmod(self.root, 0o700)
        self.assertEqual(store.inventory(self.root), before)

    def test_partial_multi_file_failure_rolls_back(self):
        data = fixture("first-profile.json")
        data["records"] += fixture("daily-update.json")["records"]
        plan = store.make_plan(self.root, data)
        before = store.inventory(self.root)
        original = store.atomic_write
        writes = []
        def fail_one(path, payload):
            writes.append(str(path))
            if len(writes) == 2:
                raise PermissionError("simulated second write failure")
            return original(path, payload)
        with patch.object(store, "atomic_write", side_effect=fail_one):
            with self.assertRaises(PermissionError):
                store.apply_plan(self.root, plan)
        self.assertEqual(store.inventory(self.root), before)
        self.assertFalse((self.root / "daily/2026-09-02.md").exists())

    def test_recovery_retains_later_human_edit(self):
        self.apply(one())
        journals = list((self.root / ".context/transactions").glob("*/journal.json"))
        journal = journals[0]
        state = json.loads(journal.read_text())
        state["status"] = "pending"
        journal.write_bytes(store.encode(state))
        p = self.root / "PERSON_PROFILE.md"
        p.write_text(p.read_text() + "\n后续人工修改\n")
        before = p.read_bytes()
        with self.assertRaisesRegex(store.ContextError, "unfinished transaction"):
            store.make_plan(self.root, one(id="next"))
        with self.assertRaisesRegex(store.ContextError, "preserved human changes"):
            store.recover(self.root, journal.parent.name)
        self.assertEqual(p.read_bytes(), before)

    def test_cli_init_plan_apply_and_no_plan_overwrite(self):
        root = self.base / "cli-private"
        script = SKILL / "scripts/context_store.py"
        def run(*args):
            return subprocess.run([sys.executable, str(script), "--root", str(root), *args],
                                  text=True, capture_output=True)
        self.assertEqual(run("init").returncode, 0)
        input_path, output = root / "input.json", root / "plan.json"
        input_path.write_text(json.dumps(one(), ensure_ascii=False))
        self.assertEqual(run("plan", "--input", str(input_path), "--output", str(output)).returncode, 0)
        previous = output.read_bytes()
        self.assertEqual(run("plan", "--input", str(input_path), "--output", str(output)).returncode, 2)
        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(run("apply", "--plan", str(output)).returncode, 0)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = str(Path(self.tmp.name).resolve() / "project/.agents/skills")

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_install_reject_overwrite_and_recoverable_uninstall(self):
        result = installer.install(self.dest)
        self.assertEqual(result["status"], "installed")
        target = Path(self.dest) / installer.NAME
        self.assertEqual((target / "SKILL.md").read_bytes(), (SKILL / "SKILL.md").read_bytes())
        self.assertTrue((target / "assets/templates/PERSON_PROFILE.md").exists())
        before = installer.digest_tree(target)
        with self.assertRaises(ValueError):
            installer.install(self.dest)
        self.assertEqual(installer.digest_tree(target), before)
        private = Path(self.tmp.name) / "private.md"
        private.write_text("虚构私人记录")
        outcome = installer.uninstall(self.dest)
        self.assertFalse(target.exists())
        self.assertTrue(Path(outcome["recoverable_folder"]).exists())
        self.assertNotIn(Path(self.dest), Path(outcome["recoverable_folder"]).parents)
        self.assertEqual(private.read_text(), "虚构私人记录")

    def test_modified_install_not_removed(self):
        installer.install(self.dest)
        target = Path(self.dest) / installer.NAME
        note = target / "my-note.txt"
        note.write_text("人工备注")
        with self.assertRaises(ValueError):
            installer.uninstall(self.dest)
        self.assertTrue(note.exists())

    def test_system_style_install_needs_manual_recoverable_move(self):
        import shutil
        target = Path(self.dest) / installer.NAME
        shutil.copytree(SKILL, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.assertFalse((target / installer.MARKER).exists())
        self.assertFalse((target / "scripts/install.py").exists())
        note = target / "user-note.md"
        note.write_text("匿名手改：保留")
        before = installer.digest_tree(target)
        with self.assertRaisesRegex(ValueError, "no verified install marker"):
            installer.uninstall(self.dest)
        self.assertEqual(installer.digest_tree(target), before)
        backup = Path(self.dest).parent / "skill-backups/maintain-personal-context-manual"
        backup.parent.mkdir()
        target.rename(backup)
        self.assertNotIn(Path(self.dest), backup.parents)
        self.assertEqual(installer.digest_tree(backup), before)
        self.assertFalse(target.exists())
        backup.rename(target)
        self.assertEqual(note.read_text(), "匿名手改：保留")

    def test_symlink_install_and_relative_dest_rejected(self):
        with self.assertRaises(ValueError):
            installer.install("relative/skills")
        parent = Path(self.dest)
        parent.mkdir(parents=True)
        external = Path(self.tmp.name) / "outside"
        external.mkdir()
        (parent / installer.NAME).symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install(self.dest)
        self.assertEqual(list(external.iterdir()), [])

    def test_install_destination_with_symlink_ancestor_rejected(self):
        base = Path(self.tmp.name).resolve()
        outside = base / "outside"
        (outside / "nested").mkdir(parents=True)
        alias = base / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install(str(alias / "nested/skills"))
        self.assertFalse((outside / "nested/skills").exists())

    def test_source_file_and_directory_symlinks_rejected_without_reading_external_files(self):
        import shutil
        base = Path(self.tmp.name).resolve()
        outside = base / "invented-external"
        outside.mkdir()
        sentinel = outside / "fixture.txt"
        sentinel.write_text("FICTIONAL_EXTERNAL_FILE_MUST_NOT_BE_COPIED")
        before = sentinel.read_bytes()
        original_copyfile = shutil.copyfile
        def guarded_copy(src, dst, *args, **kwargs):
            resolved = Path(src).resolve()
            if resolved == sentinel or outside in resolved.parents:
                raise AssertionError("external fixture must not be copied")
            return original_copyfile(src, dst, *args, **kwargs)
        for kind in ("file", "directory"):
            with self.subTest(kind=kind):
                source = base / ("source-" + kind)
                source.mkdir()
                (source / "SKILL.md").write_text("fictional source stub")
                (source / "linked").symlink_to(sentinel if kind == "file" else outside,
                                              target_is_directory=kind == "directory")
                dest = base / ("target-" + kind) / "skills"
                with patch.object(installer, "SOURCE", source), patch.object(shutil, "copyfile", side_effect=guarded_copy):
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        installer.install(str(dest))
                self.assertFalse((dest / installer.NAME).exists())
                self.assertEqual(sentinel.read_bytes(), before)

    def test_source_root_symlink_and_source_hardlink_are_rejected(self):
        base = Path(self.tmp.name).resolve()
        source = base / "source"
        source.mkdir()
        sentinel = base / "invented-external.txt"
        sentinel.write_text("FICTIONAL_EXTERNAL_FILE")
        alias = base / "source-alias"
        alias.symlink_to(source, target_is_directory=True)
        with patch.object(installer, "SOURCE", alias):
            with self.assertRaisesRegex(ValueError, "symlink source root"):
                installer.install(self.dest)
        os.link(sentinel, source / "linked.txt")
        with patch.object(installer, "SOURCE", source):
            with self.assertRaisesRegex(ValueError, "hard-linked"):
                installer.install(self.dest)
        self.assertFalse((Path(self.dest) / installer.NAME).exists())
        self.assertEqual(sentinel.read_text(), "FICTIONAL_EXTERNAL_FILE")

    def test_symlink_introduced_after_source_preflight_is_never_dereferenced(self):
        base = Path(self.tmp.name).resolve()
        source = base / "source"
        source.mkdir()
        (source / "SKILL.md").write_text("fictional source stub")
        sentinel = base / "invented-external.txt"
        sentinel.write_text("FICTIONAL_EXTERNAL_FILE")
        original_digest = installer.digest_tree
        def introduce_link(root):
            result = original_digest(root)
            if root == source:
                (source / "late-link").symlink_to(sentinel)
            return result
        with patch.object(installer, "SOURCE", source), patch.object(installer, "digest_tree", side_effect=introduce_link):
            with self.assertRaisesRegex(ValueError, "symlink"):
                installer.install(self.dest)
        self.assertFalse((Path(self.dest) / installer.NAME).exists())
        self.assertEqual(sentinel.read_text(), "FICTIONAL_EXTERNAL_FILE")
        self.assertEqual(list(Path(self.dest).glob(".personal-context-install-*")), [])

    def test_source_symlink_ancestor_is_rejected_before_reading_source(self):
        base = Path(self.tmp.name).resolve()
        outside = base / "invented-outside-skills"
        source = outside / installer.NAME
        source.mkdir(parents=True)
        (source / "fixture.txt").write_text("FICTIONAL_EXTERNAL_CONTENT")
        alias = base / "skills"
        alias.symlink_to(outside, target_is_directory=True)
        nested_source = alias / installer.NAME
        self.assertFalse(nested_source.is_symlink())
        with patch.object(installer, "SOURCE", nested_source), patch.object(installer, "digest_tree") as digest:
            with self.assertRaisesRegex(ValueError, "symlink source root or ancestor"):
                installer.install(self.dest)
            digest.assert_not_called()
        self.assertFalse((Path(self.dest) / installer.NAME).exists())


if __name__ == "__main__":
    unittest.main()
