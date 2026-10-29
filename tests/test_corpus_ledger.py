"""Fictional corpus accounting regressions; no account access or personal data."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/maintain-personal-context/scripts/corpus_ledger.py"
spec = importlib.util.spec_from_file_location("corpus_ledger", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def access():
    return {"tool": "fictional_reader", "environment": "fictional-local",
            "method": "selected_sources", "query": {"project": "fictional-project"},
            "scope": "provided source text only; account total unknown",
            "pagination": {"pages_read": 1, "next_cursor": None, "exhausted": True},
            "truncated": False, "attachments": {"status": "none", "reason": "explicitly no attachments"},
            "limitations": ["tool outputs and pre-compaction history not assessed"]}


def record(message="message-a", **changes):
    row = {"channel_id": "native", "host_id": "host-a", "kind": "codex",
           "source_id": "source-a", "message_id": message, "text": "I completed a fictional exercise.",
           "truncated": False, "content_omitted": False, "author_role": "user",
           "author_basis": "source_metadata", "author_reference": "receipt:fictional-item",
           "disposition": "retained", "reason": "reviewed source statement"}
    row.update(changes)
    return row


def bundle():
    return {"schema_version": 1,
            "channels": [{"channel_id": "native", "status": "read", "reason": "", "access": access()}],
            "sources": [{"channel_id": "native", "host_id": "host-a", "kind": "codex",
                         "source_id": "source-a", "status": "read", "reason": "", "access": access()}],
            "records": [record()]}


class CorpusLedgerTests(unittest.TestCase):
    def test_final_partitions_recompute_drift_instead_of_inheriting_counts(self):
        data = bundle()
        data["records"] += [record("b", disposition="excluded", reason="unrelated scope"),
                            record("c", author_role="unknown", author_basis="unknown", author_reference=None),
                            record("d", text="", truncated=True, disposition="excluded", reason="mistaken no information")]
        data["reported_counts"] = {"total": 7, "retained": 3, "excluded": 3, "quarantined": 1, "unread": 0}
        result = m.build_ledger(data)
        expected = {"total": 4, "retained": 1, "excluded": 1, "quarantined": 1, "unread": 1}
        self.assertEqual(result["counts"], expected)
        self.assertEqual(result["channels"][0]["counts"], expected)
        self.assertEqual(result["sources"][0]["counts"], expected)
        comparison = result["reported_counts_comparison"]
        self.assertFalse(comparison["matches_final"])
        self.assertEqual(comparison["delta_final_minus_reported"]["total"], -3)
        self.assertEqual(result["records"][-1]["reported_disposition"], "excluded")
        self.assertEqual(result["records"][-1]["disposition"], "unread")

    def test_duplicate_records_sources_channels_count_once(self):
        data = bundle()
        for field in ("records", "sources", "channels"):
            data[field].append(copy.deepcopy(data[field][0]))
        result = m.build_ledger(data)
        self.assertEqual(result["counts"]["total"], 1)
        self.assertEqual(result["duplicates_removed"], {"records": 1, "sources": 1, "channels": 1})

    def test_any_conflicting_duplicate_rejected_before_normalization(self):
        changes = [("records", "text", "different text"), ("records", "disposition", "excluded"),
                   ("sources", "reason", "different receipt"), ("channels", "status", "partial")]
        for field, key, value in changes:
            with self.subTest(field=field, key=key):
                data = bundle(); duplicate = copy.deepcopy(data[field][0])
                duplicate[key] = value
                if field == "channels": duplicate["reason"] = "partial scope"
                data[field].append(duplicate)
                with self.assertRaises(m.LedgerError): m.build_ledger(data)
        data = bundle()
        data["records"][0].update(text="", truncated=True, disposition="excluded")
        data["records"].append(dict(data["records"][0], disposition="retained"))
        with self.assertRaises(m.LedgerError): m.build_ledger(data)

    def test_host_kind_source_and_message_all_participate_in_identity(self):
        data = bundle()
        for field, value in (("host_id", "host-b"), ("host_id", None), ("kind", "chatgpt"), ("source_id", "source-b")):
            data["sources"].append(dict(copy.deepcopy(data["sources"][0]), **{field: value}))
            data["records"].append(record(**{field: value}))
        data["records"].append(record("message-b"))
        result = m.build_ledger(data)
        self.assertEqual(result["counts"]["total"], 6)
        self.assertEqual(len(result["sources"]), 5)

    def test_multiple_channels_sum_to_final_records_not_source_estimates(self):
        data = bundle()
        data["channels"].append(dict(copy.deepcopy(data["channels"][0]), channel_id="documents"))
        data["sources"].append(dict(copy.deepcopy(data["sources"][0]), channel_id="documents", kind="document"))
        data["records"].append(record(channel_id="documents", kind="document", disposition="excluded"))
        result = m.build_ledger(data)
        for name in ("total", *m.DISPOSITIONS):
            self.assertEqual(sum(c["counts"][name] for c in result["channels"]), result["counts"][name])
            self.assertEqual(sum(s["counts"][name] for s in result["sources"]), result["counts"][name])

    def test_empty_incomplete_is_unread_for_every_annotation_and_author(self):
        for flag in ("truncated", "content_omitted"):
            for disposition in m.DISPOSITIONS:
                for role in ("user", "unknown"):
                    with self.subTest(flag=flag, disposition=disposition, role=role):
                        data = bundle(); row = data["records"][0]
                        row.update(text=" \n", disposition=disposition, **{flag: True})
                        if role == "unknown": row.update(author_role=role, author_basis="unknown", author_reference=None)
                        result = m.build_ledger(data)
                        self.assertEqual(result["counts"]["unread"], 1)
                        self.assertEqual(result["sources"][0]["status"], "partial")
                        self.assertEqual(result["channels"][0]["status"], "partial")

    def test_unknown_structured_text_keeps_text_and_author_quarantined(self):
        data = bundle()
        body = "# Fictional structured notes\n" + "- A source statement requiring review.\n" * 80
        data["records"][0].update(text=body, author_role="unknown", author_basis="unknown",
                                  author_reference=None, disposition="retained", reason="candidate pending author review")
        result = m.build_ledger(data)
        self.assertEqual(result["records"][0]["text"], body)
        self.assertEqual(result["records"][0]["author_role"], "unknown")
        self.assertEqual(result["records"][0]["disposition"], "quarantined")
        self.assertEqual(result["counts"]["excluded"], 0)

    def test_formatting_is_not_allowed_as_author_evidence(self):
        for changes in ({"author_role": "assistant", "author_basis": "formatting"},
                        {"author_basis": "unknown"}, {"author_reference": None},
                        {"author_role": "unknown"}):
            with self.subTest(changes=changes):
                data = bundle(); data["records"][0].update(changes)
                with self.assertRaises(m.LedgerError): m.build_ledger(data)

    def test_unknown_already_unread_not_promoted_to_read_quarantine(self):
        data = bundle()
        data["records"][0].update(author_role="unknown", author_basis="unknown", author_reference=None, disposition="unread")
        self.assertEqual(m.build_ledger(data)["counts"]["unread"], 1)

    def test_unknown_author_explicitly_excluded_keeps_scope_reason(self):
        data = bundle()
        row = data["records"][0]
        row.update(author_role="unknown", author_basis="unknown", author_reference=None,
                   disposition="excluded", reason="user explicitly excluded this document")
        result = m.build_ledger(data)
        self.assertEqual(result["counts"]["excluded"], 1)
        self.assertEqual(result["counts"]["quarantined"], 0)
        self.assertEqual(result["records"][0]["reason"], row["reason"])
        self.assertEqual(result["records"][0]["author_role"], "unknown")

    def test_unknown_completeness_flags_preserved_and_make_nonempty_text_partial(self):
        for changes in ({"truncated": None}, {"content_omitted": None},
                        {"truncated": None, "content_omitted": None}):
            with self.subTest(changes=changes):
                data = bundle(); data["records"][0].update(changes)
                result = m.build_ledger(data)
                self.assertEqual(result["counts"]["retained"], 1)
                for field in changes:
                    self.assertIsNone(result["records"][0][field])
                self.assertEqual(result["sources"][0]["status"], "partial")
                self.assertEqual(result["channels"][0]["status"], "partial")

    def test_empty_text_with_true_and_unknown_flags_is_unread(self):
        for changes in ({"truncated": True, "content_omitted": None},
                        {"truncated": None, "content_omitted": True}):
            data = bundle(); data["records"][0].update(text="", disposition="excluded", **changes)
            self.assertEqual(m.build_ledger(data)["counts"]["unread"], 1)

    def test_partial_nonempty_retained_text_keeps_boundary(self):
        data = bundle(); data["records"][0]["truncated"] = True
        result = m.build_ledger(data)
        self.assertEqual(result["counts"]["retained"], 1)
        self.assertEqual(result["sources"][0]["status"], "partial")
        self.assertIn("record_text_incomplete_or_unknown", result["channels"][0]["coverage_gaps"])

    def test_channel_exclusions_not_attempted_blocked_unavailable_remain_distinct(self):
        data = bundle()
        for status in m.ACCESS_STATUSES[1:]:
            row = {"channel_id": status, "status": status, "reason": "fictional reason", "access": access()}
            if status != "partial":
                row["access"].update(tool=None, pagination={"pages_read": 0, "next_cursor": None, "exhausted": None})
            data["channels"].append(row)
        result = m.build_ledger(data)
        self.assertEqual(result["coverage"]["channels_by_status"], dict.fromkeys(m.ACCESS_STATUSES, 1))
        self.assertEqual(result["coverage"]["account_total"], "unknown")
        self.assertEqual(result["coverage"]["account_enumeration_complete"], "unknown")
        self.assertEqual(result["counts"]["total"], 1)

    def test_reported_read_is_downgraded_for_unknown_or_remaining_receipts(self):
        changes = [{"pagination": {"pages_read": 1, "next_cursor": "next", "exhausted": False}},
                   {"pagination": {"pages_read": 1, "next_cursor": None, "exhausted": None}},
                   {"truncated": None}, {"truncated": True},
                   {"attachments": {"status": "unknown", "reason": "no attachment receipt"}}]
        for change in changes:
            with self.subTest(change=change):
                data = bundle(); data["sources"][0]["access"].update(change)
                result = m.build_ledger(data)
                self.assertEqual(result["sources"][0]["reported_status"], "read")
                self.assertEqual(result["sources"][0]["status"], "partial")
                self.assertEqual(result["channels"][0]["status"], "partial")

    def test_receipt_metadata_preserved_and_missing_boundary_fields_rejected(self):
        data = bundle(); data["sources"][0]["metadata"] = {"receipt_file": "fictional-receipt.json"}
        result = m.build_ledger(data)
        self.assertEqual(result["sources"][0]["access"], data["sources"][0]["access"])
        self.assertEqual(result["sources"][0]["metadata"], data["sources"][0]["metadata"])
        for field in access():
            with self.subTest(field=field):
                data = bundle(); del data["sources"][0]["access"][field]
                with self.assertRaises(m.LedgerError): m.build_ledger(data)

    def test_invalid_flags_pagination_and_mutually_exclusive_dispositions_fail(self):
        for changes in ({"truncated": "true"}, {"content_omitted": 0},
                        {"disposition": ["retained", "excluded"]}, {"retained": True},
                        {"message_id": ""}, {"host_id": ""}, {"text": ""}):
            with self.subTest(changes=changes):
                data = bundle(); data["records"][0].update(changes)
                with self.assertRaises(m.LedgerError): m.build_ledger(data)
        for changes in ({"pages_read": True}, {"pages_read": -1}, {"pages_read": 0},
                        {"next_cursor": "next"}, {"exhausted": "yes"}):
            with self.subTest(changes=changes):
                data = bundle(); data["sources"][0]["access"]["pagination"].update(changes)
                with self.assertRaises(m.LedgerError): m.build_ledger(data)

    def test_orphan_and_cross_channel_record_references_fail(self):
        for changes in ({"source_id": "missing"}, {"channel_id": "wrong"}, {"host_id": None}):
            data = bundle(); data["records"][0].update(changes)
            with self.assertRaises(m.LedgerError): m.build_ledger(data)
        data = bundle(); data["sources"][0]["channel_id"] = "missing"
        with self.assertRaises(m.LedgerError): m.build_ledger(data)

    def test_explicitly_excluded_channel_cannot_contain_retained_content(self):
        for status in ("user_excluded", "experiment_excluded"):
            data = bundle(); data["channels"][0].update(status=status, reason="fictional boundary")
            with self.assertRaises(m.LedgerError): m.build_ledger(data)

    def test_unavailable_channel_does_not_hide_an_independently_readable_child(self):
        for status in ("not_attempted", "blocked", "unavailable"):
            with self.subTest(status=status):
                data = bundle(); data["channels"][0].update(status=status, reason="original discovery route failed")
                data["channels"][0]["access"].update(tool=None,
                    pagination={"pages_read": 0, "next_cursor": None, "exhausted": None})
                result = m.build_ledger(data)
                self.assertEqual(result["channels"][0]["reported_status"], status)
                self.assertEqual(result["channels"][0]["status"], "partial")
                self.assertEqual(result["sources"][0]["status"], "read")
                self.assertEqual(result["counts"]["retained"], 1)
                self.assertIn("readable_source_despite_channel_status", result["channels"][0]["coverage_gaps"])

    def test_same_source_or_message_in_different_channels_is_a_conflict(self):
        for field in ("sources", "records"):
            data = bundle()
            data["channels"].append(dict(copy.deepcopy(data["channels"][0]), channel_id="alternate-route"))
            data[field].append(dict(copy.deepcopy(data[field][0]), channel_id="alternate-route"))
            with self.assertRaises(m.LedgerError): m.build_ledger(data)

    def test_duplicate_metadata_boolean_is_not_equal_to_integer(self):
        data = bundle(); data["records"][0]["metadata"] = {"version": True}
        data["records"].append(dict(data["records"][0], metadata={"version": 1}))
        with self.assertRaises(m.LedgerError): m.build_ledger(data)

    def test_deterministic_and_input_unchanged_after_success_and_failure(self):
        data = bundle(); before = copy.deepcopy(data)
        self.assertEqual(m.build_ledger(data), m.build_ledger(data))
        self.assertEqual(data, before)
        data["records"].append(dict(data["records"][0], text="conflict")); before = copy.deepcopy(data)
        with self.assertRaises(m.LedgerError): m.build_ledger(data)
        self.assertEqual(data, before)

    def test_empty_corpus_has_zero_counts_without_claiming_all_history(self):
        result = m.build_ledger({"schema_version": 1, "channels": [], "sources": [], "records": []})
        self.assertEqual(result["counts"], dict.fromkeys(("total", *m.DISPOSITIONS), 0))
        self.assertEqual(result["coverage"]["account_enumeration_complete"], "unknown")

    def test_cli_private_file_digest_counts_no_text_leak_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve(); inp, out = base / "input.json", base / "ledger.json"
            data = bundle(); inp.write_text(json.dumps(data), encoding="utf-8")
            cmd = [sys.executable, "-B", str(SCRIPT), "--input", str(inp), "--output", str(out)]
            result = subprocess.run(cmd, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout); encoded = out.read_bytes()
            self.assertEqual(receipt["sha256"], hashlib.sha256(encoded).hexdigest())
            self.assertEqual(json.loads(encoded)["counts"], receipt["counts"])
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(data["records"][0]["text"], result.stdout)
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual(out.read_bytes(), encoded)

    def test_cli_invalid_annotation_creates_no_output_and_does_not_print_body(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve(); inp, out = base / "input.json", base / "ledger.json"
            data = bundle(); data["records"].append(dict(data["records"][0], text="fictional private conflict"))
            inp.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--input", str(inp), "--output", str(out)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("fictional private conflict", result.stderr)
            self.assertFalse(out.exists())

    def test_cli_rejects_symlink_source_repo_and_skill_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve(); inp = base / "input.json"
            inp.write_text(json.dumps(bundle()), encoding="utf-8")
            actual = base / "actual"; actual.mkdir()
            link = base / "link"; link.symlink_to(actual, target_is_directory=True)
            repo = base / "public"; skill = repo / "skills/maintain-personal-context"
            skill.mkdir(parents=True); (skill / "SKILL.md").write_text("fictional skill")
            installed = base / "maintain-personal-context"; installed.mkdir()
            (installed / "SKILL.md").write_text("fictional skill")
            for out in (link / "ledger.json", repo / "ledger.json", installed / "ledger.json"):
                with self.subTest(out=out):
                    result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--input", str(inp), "--output", str(out)], capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
