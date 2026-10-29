"""Synthetic offline files only; not real ChatGPT export/account acceptance."""
import copy
import io
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/maintain-personal-context/scripts/chatgpt_export.py"
spec = importlib.util.spec_from_file_location("chatgpt_export", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def conversation(identity="synthetic-conversation"):
    def node(identity, parent, children, role, text):
        return {"id": identity, "parent": parent, "children": children,
                "message": {"id": "message-" + identity, "author": {"role": role},
                            "create_time": 1700000000.25, "update_time": None,
                            "content": {"content_type": "text", "parts": [text]},
                            "metadata": {}}}
    return {"id": identity, "title": "Synthetic private title", "current_node": "answer-b",
            "mapping": {"root": {"id": "root", "parent": None, "children": ["user"], "message": None},
                        "user": node("user", "root", ["answer-a", "answer-b"], "user", "Synthetic private body"),
                        "answer-a": node("answer-a", "user", [], "assistant", "Earlier branch"),
                        "answer-b": node("answer-b", "user", [], "assistant", "Current branch")}}


class ChatGPTExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / "conversations.json"
        self.output = self.base / "private-import"
        self.data = [conversation()]
        self.write_source()

    def write_source(self):
        self.source.write_bytes(m.encode(self.data))

    def run_import(self, **kwargs):
        args = {"accepted": True, "account_scope": "synthetic-personal-account"}
        args.update(kwargs)
        return m.import_export(self.source, self.output, **args)

    def manifest(self):
        return json.loads((self.output / "manifest.json").read_text())

    def shards(self, rows=None):
        directory = self.base / "synthetic-export"
        directory.mkdir()
        entries = rows or [("conversations-1.json", [conversation("first")]),
                           ("conversations-2.json", [conversation("second")])]
        for name, data in entries:
            (directory / name).write_bytes(m.encode(data))
        self.source = directory
        return {"schema_version": 1, "expected_parts": len(entries),
                "members": [{"path": name, "ordinal": i + 1} for i, (name, _) in enumerate(entries)]}

    def test_acceptance_required_before_any_output(self):
        with self.assertRaisesRegex(m.ExportError, "explicit_user_acceptance"):
            self.run_import(accepted=False)
        self.assertFalse(self.output.exists())

    def test_all_branches_scaffolding_and_original_fields_preserved(self):
        result = self.run_import()
        self.assertEqual(result["status"], "file_import_complete")
        self.assertEqual(result["expected_file_counts"], {"conversations": 1, "mapping_nodes": 4, "messages": 3})
        rows = [json.loads(x) for x in (self.output / "messages/0.jsonl").read_text().splitlines()]
        self.assertEqual({x["node_id"] for x in rows}, {"user", "answer-a", "answer-b"})
        self.assertEqual(json.loads((self.output / "conversations/0.json").read_text()), self.data[0])
        self.assertEqual((self.output / "original-conversations.json").read_bytes(), self.source.read_bytes())
        self.assertIsNone(rows[0]["event_date"])
        self.assertIsNone(rows[0]["confirmation_date"])

    def test_never_claims_account_memory_attachment_completeness(self):
        result = self.run_import()
        self.assertTrue(result["coverage"]["supplied_file_import_complete"])
        self.assertEqual(result["coverage"]["account_enumeration_complete"], "unknown")
        self.assertEqual(result["coverage"]["chatgpt_memory_complete"], "not_assessed")
        self.assertEqual(result["coverage"]["attachment_content_complete"], "not_assessed")
        self.assertEqual(result["cutoff_basis"], "unknown")
        self.assertEqual(result["account_scope_basis"], "user_declared_not_authenticated_by_adapter")

    def test_text_and_multimodal_part_boundaries_and_attachments(self):
        message = self.data[0]["mapping"]["user"]["message"]
        message["content"] = {"content_type": "multimodal_text", "parts": ["one", "two", {"content_type": "image_asset_pointer", "asset_pointer": "synthetic-asset"}]}
        message["metadata"]["attachments"] = [{"id": "synthetic-attachment"}]
        self.write_source()
        result = self.run_import()
        receipt = result["conversations"][0]
        self.assertEqual(receipt["nontext_parts_unread"], 1)
        self.assertEqual(receipt["attachment_references_unread"], 1)
        rows = [json.loads(x) for x in (self.output / "messages/0.jsonl").read_text().splitlines()]
        user = next(x for x in rows if x["node_id"] == "user")
        self.assertEqual(user["text_parts"], ["one", "two"])
        self.assertEqual(user["text"], "one\ntwo")

    def test_unknown_content_preserved_raw_never_full_text(self):
        self.data[0]["mapping"]["user"]["message"]["content"] = {"content_type": "future_audio", "opaque": "retained"}
        self.write_source()
        result = self.run_import()
        self.assertTrue(result["coverage"]["supplied_file_import_complete"])
        self.assertFalse(result["coverage"]["text_extraction_complete_for_supported_file"])
        self.assertEqual(result["conversations"][0]["raw_only_messages"], 1)
        self.assertIn("retained", (self.output / "conversations/0.json").read_text())

    def test_explicit_partial_content_blocks_text_complete(self):
        self.data[0]["mapping"]["user"]["message"]["content"]["truncated"] = True
        self.write_source()
        self.assertFalse(self.run_import()["coverage"]["text_extraction_complete_for_supported_file"])

    def test_source_node_part_and_alternate_limit_markers_never_disappear(self):
        for target_name, marker in (("conversation", "partial_results"), ("node", "isTruncated"),
                                    ("part", "incomplete"), ("empty", "content_omitted")):
            with self.subTest(target=target_name):
                self.data = [conversation()]
                self.output = self.base / ("limited-" + target_name)
                if target_name == "conversation":
                    target = self.data[0]
                elif target_name == "node":
                    target = self.data[0]["mapping"]["root"]
                elif target_name == "empty":
                    self.data[0]["mapping"] = {}
                    self.data[0]["current_node"] = None
                    target = self.data[0]
                else:
                    target = {"content_type": "image_asset_pointer"}
                    self.data[0]["mapping"]["user"]["message"]["content"]["parts"].append(target)
                target[marker] = True
                self.write_source()
                result = self.run_import()
                self.assertTrue(result["coverage"]["source_truncation_observed"])
                self.assertFalse(result["coverage"]["text_extraction_complete_for_supported_file"])

    def test_export_cutoff_is_declared_and_does_not_filter_originals(self):
        result = self.run_import(export_cutoff_utc="2023-01-01T00:00:00Z")
        self.assertEqual(result["cutoff_basis"], "user_declared")
        self.assertEqual(result["next_index"], 1)
        self.assertEqual(result["conversations"][0]["messages"], 3)

    def test_bad_cutoff_rejected_before_output(self):
        with self.assertRaisesRegex(m.ExportError, "cutoff_requires"):
            self.run_import(export_cutoff_utc="2023-01-01")
        self.assertFalse(self.output.exists())

    def test_empty_export_is_file_complete_only(self):
        self.data = []
        self.write_source()
        result = self.run_import()
        self.assertEqual(result["next_index"], 0)
        self.assertTrue(result["coverage"]["supplied_file_import_complete"])
        self.assertEqual(result["coverage"]["account_enumeration_complete"], "unknown")

    def test_zip_inventory_not_extracted_or_claimed_read(self):
        archive = self.base / "synthetic-export.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("export/conversations.json", self.source.read_bytes())
            stream.writestr("images/synthetic.png", b"synthetic-binary")
            stream.writestr("user.json", '{"synthetic": true}')
        self.source = archive
        result = self.run_import()
        self.assertEqual(result["source"]["format"], "zip_mapping_json")
        inventory = json.loads((self.output / "archive-inventory.json").read_text())
        self.assertEqual(len(inventory), 2)
        self.assertTrue(all(x["status"] == "present_in_archive_unread" for x in inventory))
        self.assertFalse((self.output / "images").exists())

    def test_numbered_conversation_shards_and_canonical_all_imported(self):
        archive = self.base / "synthetic-export.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("conversations.json", self.source.read_bytes())
            stream.writestr("conversations-001.json", self.source.read_bytes())
        self.source = archive
        result = self.run_import()
        self.assertEqual(result["next_record"], 2)
        self.assertEqual(result["next_index"], 1)
        self.assertEqual(len(result["source"]["files"]), 2)
        self.assertEqual(result["source_selection"]["shard_set_completeness"], "unknown")

    def test_zip_path_traversal_rejected_without_extraction(self):
        archive = self.base / "synthetic-export.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("conversations.json", self.source.read_bytes())
            stream.writestr("../bad", "synthetic")
        self.source = archive
        with self.assertRaisesRegex(m.ExportError, "unsafe_or_duplicate"):
            self.run_import()

    def test_zip_symlink_member_rejected(self):
        archive = self.base / "synthetic-export.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("conversations.json", self.source.read_bytes())
            symlink = zipfile.ZipInfo("link")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            stream.writestr(symlink, "target")
        self.source = archive
        with self.assertRaisesRegex(m.ExportError, "unsafe_or_duplicate"):
            self.run_import()

    def test_zip_multiple_canonical_members_preserved_and_deduplicated(self):
        archive = self.base / "synthetic-export.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("one/conversations.json", self.source.read_bytes())
            stream.writestr("two/conversations.json", self.source.read_bytes())
        self.source = archive
        result = self.run_import()
        self.assertEqual(result["next_record"], 2)
        self.assertEqual(result["next_index"], 1)
        self.assertEqual(len(list((self.output / "original-files").iterdir())), 2)

    def test_duplicate_json_keys_fail_closed(self):
        self.source.write_text('[{"id":"a","id":"b","mapping":{}}]')
        with self.assertRaisesRegex(m.ExportError, "duplicate_json_key"):
            self.run_import()
        self.assertEqual(self.manifest()["status"], "failed")
        self.assertEqual((self.output / "original-conversations.json").read_bytes(), self.source.read_bytes())

    def test_unknown_top_level_fails_closed(self):
        self.source.write_text('{"conversations": []}')
        with self.assertRaisesRegex(m.ExportError, "unsupported_export_top_level"):
            self.run_import()

    def test_identical_duplicate_conversations_keep_all_occurrences(self):
        self.data.append(copy.deepcopy(self.data[0]))
        self.write_source()
        result = self.run_import()
        self.assertEqual(result["next_record"], 2)
        self.assertEqual(result["next_index"], 1)
        self.assertTrue(result["occurrences"][1]["duplicate"])
        self.assertEqual(result["raw_source_counts"]["message_occurrences"], 6)
        self.assertEqual(result["expected_file_counts"]["messages"], 3)

    def test_malformed_node_leaves_failed_checkpoint_and_preserves_source(self):
        self.data[0]["mapping"]["user"].pop("id")
        self.write_source()
        with self.assertRaisesRegex(m.ExportError, "unsupported_mapping_node"):
            self.run_import()
        result = self.manifest()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["next_index"], 0)
        self.assertFalse(result["coverage"]["supplied_file_import_complete"])
        self.assertEqual((self.output / "original-conversations.json").read_bytes(), self.source.read_bytes())

    def test_pause_resume_same_source_and_independent_verify(self):
        self.data.append(conversation("synthetic-second"))
        self.write_source()
        first = self.run_import(max_conversations=1)
        self.assertEqual(first["status"], "paused")
        self.assertEqual(first["next_index"], 1)
        partial = m.verify_import(self.source, self.output, accepted=True)
        self.assertFalse(partial["supplied_file_import_complete"])
        finished = self.run_import(resume=True)
        self.assertEqual(finished["next_index"], 2)
        final = m.verify_import(self.source, self.output, accepted=True)
        self.assertTrue(final["supplied_file_import_complete"])
        self.assertEqual(final["raw_source_counts"], final["persisted_output_counts"])

    def test_resume_source_changed_never_overwrites_previous(self):
        self.run_import()
        before = (self.output / "manifest.json").read_bytes()
        self.data.append(conversation("synthetic-second"))
        self.write_source()
        with self.assertRaisesRegex(m.ExportError, "resume_source_or_scope_changed"):
            self.run_import(resume=True)
        self.assertEqual((self.output / "manifest.json").read_bytes(), before)

    def test_resume_output_hand_edit_rejected(self):
        self.run_import()
        (self.output / "messages/0.jsonl").write_text("changed")
        with self.assertRaisesRegex(m.ExportError, "resume_output_integrity_failure"):
            self.run_import(resume=True)

    def test_verify_detects_changed_output_even_with_success_manifest(self):
        self.run_import()
        (self.output / "messages/0.jsonl").write_text("changed")
        with self.assertRaisesRegex(m.ExportError, "verify_output_content_mismatch"):
            m.verify_import(self.source, self.output, accepted=True)

    def test_verify_read_only_and_does_not_promote_account_claim(self):
        self.run_import()
        before = {p: p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        result = m.verify_import(self.source, self.output, accepted=True)
        self.assertEqual(result["account_enumeration_complete"], "unknown")
        self.assertEqual(before, {p: p.read_bytes() for p in self.output.rglob("*") if p.is_file()})

    def test_private_permissions_on_all_generated_files(self):
        self.run_import()
        for path in [self.output] + list(self.output.rglob("*")):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_relative_output_rejected(self):
        self.output = Path("relative-private")
        with self.assertRaisesRegex(m.ExportError, "absolute_path"):
            self.run_import()

    def test_repository_and_skill_output_rejected(self):
        for marker in (".git", "SKILL.md"):
            with self.subTest(marker=marker):
                container = self.base / ("code" + marker.replace(".", ""))
                container.mkdir()
                (container / marker).touch()
                self.output = container / "private"
                with self.assertRaisesRegex(m.ExportError, "private_output_inside"):
                    self.run_import()

    def test_installed_skill_directory_output_rejected(self):
        container = self.base / ".codex/skills"
        container.mkdir(parents=True)
        self.output = container / "new"
        with self.assertRaisesRegex(m.ExportError, "private_output_inside_skill"):
            self.run_import()

    def test_source_archive_package_without_git_rejects_sibling_private_output(self):
        package = self.base / "downloaded-source"
        skill = package / "skills/maintain-personal-context"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Synthetic skill")
        self.output = package / "private"
        with self.assertRaisesRegex(m.ExportError, "private_output_inside_source_package"):
            self.run_import()

    def test_receipt_coverage_tampering_cannot_promote_complete_text(self):
        self.data[0]["isTruncated"] = True
        self.write_source()
        self.run_import()
        manifest = self.manifest()
        manifest["conversations"][0]["source_truncation_observed"] = False
        manifest["conversations"][0]["partial_messages"] = 0
        (self.output / "manifest.json").write_bytes(m.encode(manifest))
        with self.assertRaisesRegex(m.ExportError, "resume_output_integrity_failure"):
            self.run_import(resume=True)
        with self.assertRaisesRegex(m.ExportError, "verify_receipt_coverage_mismatch"):
            m.verify_import(self.source, self.output, accepted=True)

    def test_source_and_output_symlinks_rejected(self):
        link = self.base / "source-link.json"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(m.ExportError, "symlink_path"):
            m.import_export(link, self.output, accepted=True, account_scope="synthetic")
        parent = self.base / "link-parent"
        parent.symlink_to(self.base, target_is_directory=True)
        self.output = parent / "private"
        with self.assertRaisesRegex(m.ExportError, "symlink_path"):
            self.run_import()

    def test_resume_symlink_and_permission_changes_rejected(self):
        self.run_import()
        (self.output / "messages/0.jsonl").chmod(0o644)
        with self.assertRaisesRegex(m.ExportError, "private_checkpoint_permissions"):
            self.run_import(resume=True)

    def test_hardlinked_source_and_private_checkpoint_are_rejected(self):
        link = self.base / "source-hardlink.json"
        os.link(self.source, link)
        with self.assertRaisesRegex(m.ExportError, "hardlinked_source"):
            self.run_import()
        link.unlink()
        self.run_import()
        os.link(self.output / "messages/0.jsonl", self.base / "checkpoint-hardlink")
        with self.assertRaisesRegex(m.ExportError, "hardlinked_private"):
            self.run_import(resume=True)
        with self.assertRaisesRegex(m.ExportError, "hardlinked_private"):
            m.verify_import(self.source, self.output, accepted=True)

    def test_numbered_direct_conversation_file_is_not_treated_as_full_export(self):
        shard = self.base / "conversations-001.json"
        shard.write_bytes(self.source.read_bytes())
        self.source = shard
        result = self.run_import()
        self.assertTrue(result["coverage"]["supplied_file_import_complete"])
        self.assertEqual(result["coverage"]["shard_set_completeness"], "unknown")
        self.assertFalse(result["source_selection"]["final_part_known"])

    def test_output_existing_without_explicit_resume_rejected(self):
        self.run_import()
        with self.assertRaisesRegex(m.ExportError, "explicit_resume"):
            self.run_import()

    def test_cli_does_not_print_private_text_or_labels(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.source), "--output", str(self.output),
                                 "--accept-user-provided-export", "--account-scope", "synthetic-secret-label"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Synthetic private", result.stdout + result.stderr)
        self.assertNotIn("synthetic-secret-label", result.stdout + result.stderr)
        verified = subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.source), "--output", str(self.output),
                                   "--accept-user-provided-export", "--account-scope", "synthetic-secret-label", "--verify"],
                                  capture_output=True, text=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertTrue(json.loads(verified.stdout)["supplied_file_import_complete"])

    def test_directory_manifest_imports_every_shard_and_preserves_hashes(self):
        declared = self.shards()
        result = self.run_import(source_manifest=declared)
        self.assertEqual(result["expected_file_counts"]["conversations"], 2)
        self.assertTrue(result["source_selection"]["declared_manifest_matched"])
        self.assertFalse(result["source_selection"]["final_part_known"])
        for shard in result["source"]["files"]:
            self.assertEqual(m.digest((self.source / shard["member"]).read_bytes()), shard["sha256"])
            self.assertEqual(m.digest((self.output / shard["preserved_path"]).read_bytes()), shard["sha256"])
        self.assertTrue(m.verify_import(self.source, self.output, accepted=True, source_manifest=declared)["supplied_file_import_complete"])

    def test_missing_manifest_member_has_structured_missing_ordinal(self):
        declared = self.shards()
        (self.source / "conversations-2.json").unlink()
        with self.assertRaisesRegex(m.ExportError, "source_manifest_missing_members") as raised:
            self.run_import(source_manifest=declared)
        public = m._public_selection(raised.exception.details)
        self.assertEqual(public["missing_member_count"], 1)
        self.assertEqual(public["missing_ordinals"], [2])
        self.assertNotIn("conversations-2", json.dumps(public))
        self.assertFalse(self.output.exists())

    def test_manifest_cannot_silently_omit_discovered_sibling(self):
        declared = self.shards()
        declared["members"].pop()
        declared["expected_parts"] = 1
        with self.assertRaisesRegex(m.ExportError, "unexpected_conversation") as raised:
            self.run_import(source_manifest=declared)
        self.assertEqual(m._public_selection(raised.exception.details)["unexpected_conversation_member_count"], 1)

    def test_unknown_shard_names_require_explicit_selection(self):
        declared = self.shards([("part-a.json", [conversation("first")]), ("part-b.json", [conversation("second")])])
        with self.assertRaisesRegex(m.ExportError, "supply_manifest"):
            self.run_import()
        self.assertEqual(self.run_import(source_manifest=declared)["next_index"], 2)

    def test_manifest_ordinal_gaps_duplicates_bools_and_traversal_rejected(self):
        declared = self.shards()
        mutations = [lambda d: d["members"][1].update(ordinal=3),
                     lambda d: d["members"][1].update(ordinal=1),
                     lambda d: d["members"][0].update(ordinal=True),
                     lambda d: d["members"][0].update(path="../escape.json"),
                     lambda d: d.update(expected_parts=1000000000)]
        for mutate in mutations:
            current = copy.deepcopy(declared)
            mutate(current)
            with self.assertRaises(m.ExportError):
                self.run_import(source_manifest=current)
        self.assertFalse(self.output.exists())

    def test_manifest_expected_hash_mismatch_rejected(self):
        declared = self.shards()
        declared["members"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(m.ExportError, "source_manifest_hash_mismatch"):
            self.run_import(source_manifest=declared)

    def test_visible_number_gap_reported_but_tail_stays_unknown(self):
        self.shards([("conversations-1.json", [conversation("first")]),
                     ("conversations-3.json", [conversation("third")])])
        result = self.run_import()
        public = m._public_selection(result["source_selection"])
        self.assertEqual(public["observed_internal_gaps"], [{"missing_numbers": [2]}])
        self.assertEqual(public["trailing_completeness"], "unknown")
        self.assertTrue(result["coverage"]["supplied_file_import_complete"])
        self.assertEqual(result["coverage"]["shard_set_completeness"], "unknown")

    def test_huge_number_range_reported_without_unbounded_gap_allocation(self):
        self.shards([("conversations-1.json", []), ("conversations-9999999999.json", [])])
        result = self.run_import()
        gaps = m._public_selection(result["source_selection"])["observed_internal_gaps"]
        self.assertEqual(gaps[0]["status"], "range_too_large_to_enumerate")

    def test_conflicting_duplicate_conversation_blocks_and_keeps_both_originals(self):
        changed = conversation("same")
        changed["mapping"]["user"]["message"]["content"]["parts"] = ["conflicting synthetic text"]
        self.shards([("conversations-1.json", [conversation("same")]), ("conversations-2.json", [changed])])
        with self.assertRaisesRegex(m.ExportError, "conflicting_duplicate_conversation"):
            self.run_import()
        result = self.manifest()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["coverage"]["supplied_file_import_complete"])
        self.assertEqual(len(list((self.output / "original-files").iterdir())), 2)

    def test_duplicate_message_identical_body_merges_placements_only(self):
        mapping = self.data[0]["mapping"]
        duplicate = copy.deepcopy(mapping["answer-a"])
        duplicate["id"] = "duplicate-placement"
        mapping["duplicate-placement"] = duplicate
        mapping["user"]["children"].append("duplicate-placement")
        self.write_source()
        result = self.run_import()
        self.assertEqual(result["expected_file_counts"]["messages"], 3)
        self.assertEqual(result["raw_source_counts"]["message_occurrences"], 4)
        self.assertEqual(result["conversations"][0]["duplicate_message_occurrences"], 1)
        rows = [json.loads(row) for row in (self.output / "messages/0.jsonl").read_text().splitlines()]
        repeated = next(row for row in rows if row["message_id"] == "message-answer-a")
        self.assertEqual(len(repeated["node_occurrences"]), 2)

    def test_duplicate_message_conflict_cannot_be_deduplicated(self):
        mapping = self.data[0]["mapping"]
        mapping["answer-b"]["message"]["id"] = mapping["answer-a"]["message"]["id"]
        self.write_source()
        with self.assertRaisesRegex(m.ExportError, "conflicting_duplicate_message_id"):
            self.run_import()
        self.assertEqual(self.manifest()["status"], "failed")

    def test_resume_after_partial_original_copy_completes_missing_shard(self):
        self.shards()
        original = m._copy_member
        count = 0
        def interrupted(catalog, shard, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("synthetic interrupted copy")
            return original(catalog, shard, target)
        with mock.patch.object(m, "_copy_member", side_effect=interrupted):
            with self.assertRaisesRegex(m.ExportError, "filesystem_operation_failed"):
                self.run_import()
        self.assertEqual(len(list((self.output / "original-files").iterdir())), 1)
        self.assertEqual(self.run_import(resume=True)["status"], "file_import_complete")

    def test_cross_shard_pause_resume_and_new_shard_rejected(self):
        self.shards()
        result = self.run_import(max_conversations=1)
        self.assertEqual(result["status"], "paused")
        (self.source / "conversations-3.json").write_bytes(m.encode([conversation("third")]))
        with self.assertRaisesRegex(m.ExportError, "resume_source_or_scope_changed"):
            self.run_import(resume=True)
        (self.source / "conversations-3.json").unlink()
        self.assertEqual(self.run_import(resume=True)["next_record"], 2)

    def test_source_growth_after_scan_is_sanitized_and_checkpoint_failed(self):
        original = m._scan_catalog
        def mutate_after_scan(catalog, limit):
            result = original(catalog, limit)
            self.data.append(conversation("late"))
            self.write_source()
            return result
        with mock.patch.object(m, "_scan_catalog", side_effect=mutate_after_scan):
            with self.assertRaisesRegex(m.ExportError, "source_changed_during_import"):
                self.run_import()
        self.assertEqual(self.manifest()["status"], "failed")

    def test_record_limit_can_increase_on_resume_without_changing_source(self):
        self.data[0]["padding"] = "synthetic" * 100
        self.write_source()
        with self.assertRaisesRegex(m.ExportError, "record_size_limit_exceeded"):
            self.run_import(max_record_bytes=128)
        self.assertEqual(self.manifest()["status"], "failed")
        result = self.run_import(resume=True, max_record_bytes=10000)
        self.assertEqual(result["status"], "file_import_complete")

    def test_total_budget_is_explicit_and_checked_during_stream_hashing(self):
        with self.assertRaisesRegex(m.ExportError, "total_source_bytes_limit_exceeded"):
            self.run_import(max_total_bytes=10)
        stream = io.BytesIO(b"x" * 100000)
        with self.assertRaisesRegex(m.ExportError, "total_source_bytes_limit_exceeded"):
            m._stream_sha(stream, 10)
        self.assertEqual(stream.tell(), 11)

    def test_stream_never_uses_unbounded_read(self):
        class Bounded(io.BytesIO):
            def read(self, size=-1):
                if size < 0:
                    raise AssertionError("unbounded read")
                return super().read(size)
        source = Bounded(m.encode(self.data * 20))
        self.assertEqual(len(list(m.iter_json_array(source, chunk_size=7))), 20)

    def test_output_inside_directory_source_is_refused(self):
        self.shards()
        self.output = self.source / "private-output"
        with self.assertRaisesRegex(m.ExportError, "source_and_output_must_be_separate"):
            self.run_import()

    def test_nonfinite_and_surrogate_json_fail_with_sanitized_checkpoint(self):
        for label, suffix in (("number", b'1e99999'), ("surrogate", b'"\\ud800"')):
            with self.subTest(label=label):
                self.output = self.base / ("malformed-" + label)
                raw = m.encode(conversation()).rstrip()
                self.source.write_bytes(b'[' + raw[:-1] + b',"extra":' + suffix + b'}]')
                with self.assertRaises(m.ExportError):
                    self.run_import()
                self.assertEqual(self.manifest()["status"], "failed")

    def test_cli_checks_acceptance_before_reading_manifest(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.source), "--output", str(self.output),
                                 "--account-scope", "synthetic", "--source-manifest", str(self.base / "not-present.json")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["reason"], "explicit_user_acceptance_required")

    def test_cli_missing_shard_report_has_ordinals_without_private_names(self):
        declared = self.shards()
        (self.source / "conversations-2.json").unlink()
        manifest_path = self.base / "declared-source.json"
        manifest_path.write_bytes(m.encode(declared))
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.source), "--output", str(self.output),
                                 "--account-scope", "synthetic", "--accept-user-provided-export",
                                 "--source-manifest", str(manifest_path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["source_inventory"]["missing_ordinals"], [2])
        self.assertNotIn("conversations-2", result.stderr)


if __name__ == "__main__":
    unittest.main()
