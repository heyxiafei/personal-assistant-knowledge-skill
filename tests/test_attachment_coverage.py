"""Fictional attachment receipts; no media decoding, account access or private data."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_corpus_ledger import SCRIPT, bundle, m


def item(attachment_id="attachment-a", kind="pdf", **changes):
    unit = m.ATTACHMENT_KINDS.get(kind, "item")
    total, start = (12.5, 0) if unit == "second" else (1, 1)
    row = {"attachment_id": attachment_id, "kind": kind, "status": "read",
           "reason": "fictional complete reading receipt", "reader": "fictional_local_reader",
           "reference": "receipt:fictional-item", "extent": {"unit": unit, "total": total},
           "read_ranges": [{"start": start, "end": total}], "sampled": False, "limitations": []}
    row.update(changes)
    return row


def receipt(items, status="read", inventory_complete=True):
    return {"status": status, "reason": "fictional bounded attachment inventory",
            "inventory_complete": inventory_complete, "items": items}


def with_receipt(value, target="sources"):
    data = bundle()
    data[target][0]["access"]["attachments"] = value
    return data


class AttachmentCoverageTests(unittest.TestCase):
    def test_legacy_summary_is_accepted_but_not_per_attachment_evidence(self):
        for status in ("read", "none", "partial", "unread", "unknown"):
            with self.subTest(status=status):
                original = {"status": status, "reason": "legacy fictional summary"}
                result = m.build_ledger(with_receipt(original))
                source = result["sources"][0]
                self.assertEqual(source["access"]["attachments"], original)
                self.assertEqual(source["attachment_coverage"], {
                    "basis": "legacy_summary", "inventory_complete": None,
                    "unique_items": None, "duplicates_removed": 0, "status": status})

    def test_complete_receipts_for_each_supported_media_kind(self):
        for kind in ("image", "screenshot", "pdf", "audio", "video", "file"):
            with self.subTest(kind=kind):
                result = m.build_ledger(with_receipt(receipt([item(kind=kind)])))
                source = result["sources"][0]
                self.assertEqual(source["status"], "read")
                self.assertEqual(source["attachment_coverage"]["basis"], "per_attachment")
                self.assertEqual(source["attachment_coverage"]["unique_items"], 1)

    def test_empty_inventory_only_means_none_when_explicitly_complete(self):
        for complete, status in ((True, "none"), (False, "unknown"), (None, "unknown")):
            with self.subTest(complete=complete):
                result = m.build_ledger(with_receipt(receipt([], status, complete)))
                self.assertEqual(result["sources"][0]["attachment_coverage"]["status"], status)
        for complete, status in ((True, "read"), (False, "none"), (None, "read")):
            with self.subTest(invalid=(complete, status)):
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(with_receipt(receipt([], status, complete)))

    def test_partial_page_reading_preserves_exact_observed_ranges_and_boundary(self):
        observed = item(status="partial", extent={"unit": "page", "total": 5},
                        read_ranges=[{"start": 2, "end": 3}], limitations=["pages 1, 4 and 5 not read"])
        result = m.build_ledger(with_receipt(receipt([observed], "partial")))
        source = result["sources"][0]
        self.assertEqual(source["access"]["attachments"]["items"], [observed])
        self.assertEqual(source["reported_status"], "read")
        self.assertEqual(source["status"], "partial")
        self.assertEqual(result["channels"][0]["status"], "partial")
        self.assertIn("attachments_not_fully_read", source["coverage_gaps"])

    def test_sampled_audio_video_is_partial_even_with_full_temporal_extent(self):
        for kind in ("audio", "video"):
            sampled = item(kind=kind, status="partial", sampled=True,
                           limitations=["selected frames or audio samples only"])
            result = m.build_ledger(with_receipt(receipt([sampled], "partial")))
            self.assertEqual(result["sources"][0]["status"], "partial")
            sampled["status"] = "read"
            with self.assertRaises(m.LedgerError):
                m.build_ledger(with_receipt(receipt([sampled])))

    def test_unknown_extent_cannot_be_promoted_to_complete_reading(self):
        for kind in ("pdf", "audio", "video"):
            observed = item(kind=kind, status="partial", limitations=["total extent unknown"])
            observed["extent"]["total"] = None
            result = m.build_ledger(with_receipt(receipt([observed], "partial")))
            self.assertIsNone(result["sources"][0]["access"]["attachments"]["items"][0]["extent"]["total"])
            observed["status"] = "read"
            with self.assertRaises(m.LedgerError):
                m.build_ledger(with_receipt(receipt([observed])))

    def test_known_content_limitations_remain_partial_despite_full_time_range(self):
        for kind, limitation in (("video", "only subtitles read; picture and sound not reviewed"),
                                 ("video", "only audio heard; picture not reviewed"),
                                 ("audio", "low-confidence words require review"),
                                 ("pdf", "table cells unreadable on the observed page")):
            with self.subTest(kind=kind, limitation=limitation):
                observed = item(kind=kind, status="partial", limitations=[limitation])
                result = m.build_ledger(with_receipt(receipt([observed], "partial")))
                self.assertEqual(result["sources"][0]["status"], "partial")
                observed["status"] = "read"
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(with_receipt(receipt([observed])))

    def test_unread_blocked_and_unknown_receipts_are_preserved_without_claimed_reading(self):
        for item_status, summary in (("unread", "unread"), ("blocked", "unread"), ("unknown", "unknown")):
            with self.subTest(item_status=item_status):
                blocked = item(status=item_status, reader=None, reference=None, read_ranges=[],
                               limitations=["fictional reader unavailable or denied"])
                result = m.build_ledger(with_receipt(receipt([blocked], summary)))
                source = result["sources"][0]
                self.assertEqual(source["access"]["attachments"]["items"], [blocked])
                self.assertEqual(source["status"], "partial")
                self.assertEqual(result["counts"]["retained"], 1)  # Independent source text stays readable.

    def test_mixed_receipts_and_incomplete_inventory_aggregate_conservatively(self):
        unread = item("attachment-b", status="blocked", reader=None, reference=None,
                      read_ranges=[], limitations=["permission denied"])
        cases = [([item(), unread], True, "partial"), ([item()], False, "partial"),
                 ([item()], None, "partial"), ([unread], None, "unknown")]
        for items, complete, status in cases:
            with self.subTest(complete=complete, status=status):
                result = m.build_ledger(with_receipt(receipt(items, status, complete)))
                self.assertEqual(result["sources"][0]["attachment_coverage"]["status"], status)

    def test_inconsistent_summary_cannot_hide_unread_sampled_or_unknown_items(self):
        cases = [item(status="unread", reader=None, reference=None, read_ranges=[], limitations=["not read"]),
                 item(status="unknown", reader=None, reference=None, read_ranges=[], limitations=["no receipt"]),
                 item(status="partial", sampled=True, limitations=["sample only"])]
        for observed in cases:
            for summary in ("read", "none"):
                with self.subTest(status=observed["status"], summary=summary):
                    with self.assertRaises(m.LedgerError):
                        m.build_ledger(with_receipt(receipt([observed], summary)))
        with self.assertRaises(m.LedgerError):
            m.build_ledger(with_receipt(receipt([item()], "partial")))

    def test_adjacent_page_or_time_ranges_are_full_but_gaps_are_not(self):
        cases = [("pdf", "page", 4, [{"start": 1, "end": 2}, {"start": 3, "end": 4}]),
                 ("audio", "second", 4.5, [{"start": 0, "end": 1.5}, {"start": 1.5, "end": 4.5}]),
                 ("video", "second", 4.5, [{"start": 0, "end": 1.5}, {"start": 1.5, "end": 4.5}])]
        for kind, unit, total, ranges in cases:
            with self.subTest(kind=kind):
                observed = item(kind=kind, extent={"unit": unit, "total": total}, read_ranges=list(reversed(ranges)))
                self.assertEqual(m.build_ledger(with_receipt(receipt([observed])))["sources"][0]["status"], "read")
                observed["read_ranges"] = ranges[1:]
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(with_receipt(receipt([observed])))

    def test_identical_attachment_duplicates_count_once_and_do_not_mutate_input(self):
        observed = item()
        data = with_receipt(receipt([observed, copy.deepcopy(observed)]))
        before = copy.deepcopy(data)
        result = m.build_ledger(data)
        source = result["sources"][0]
        self.assertEqual(source["attachment_coverage"]["duplicates_removed"], 1)
        self.assertEqual(source["attachment_coverage"]["unique_items"], 1)
        self.assertEqual(source["access"]["attachments"]["items"], [observed])
        self.assertEqual(result, m.build_ledger(data))
        self.assertEqual(data, before)

    def test_conflicting_duplicate_identity_rejected_before_aggregation(self):
        for changes in ({"reason": "different fictional receipt"}, {"reference": "receipt:different"},
                        {"kind": "screenshot"}, {"sampled": True, "status": "partial", "limitations": ["sample"]}):
            with self.subTest(changes=changes):
                duplicate = item(**changes)
                data = with_receipt(receipt([item(), duplicate]))
                before = copy.deepcopy(data)
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(data)
                self.assertEqual(data, before)

    def test_attachment_ids_are_unique_within_each_receipt_not_across_sources(self):
        data = with_receipt(receipt([item()]))
        other = copy.deepcopy(data["sources"][0])
        other["source_id"] = "source-b"
        other["access"]["attachments"]["items"][0]["reference"] = "receipt:source-b"
        data["sources"].append(other)
        self.assertEqual(len(m.build_ledger(data)["sources"]), 2)

    def test_required_receipt_fields_and_inventory_pairing_are_validated(self):
        for field in item():
            with self.subTest(field=field):
                observed = item(); del observed[field]
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(with_receipt(receipt([observed])))
        for field in ("items", "inventory_complete"):
            invalid = receipt([item()]); del invalid[field]
            with self.assertRaises(m.LedgerError):
                m.build_ledger(with_receipt(invalid))
        for changes in ({"items": {}}, {"inventory_complete": "yes"}, {"inventory_complete": 1}):
            invalid = receipt([item()]); invalid.update(changes)
            with self.assertRaises(m.LedgerError):
                m.build_ledger(with_receipt(invalid))

    def test_invalid_identity_provenance_status_sampling_and_limitations_fail(self):
        for changes in ({"attachment_id": " "}, {"attachment_id": None}, {"reader": None},
                        {"reference": ""}, {"kind": "made-up"}, {"status": "complete"},
                        {"sampled": 1}, {"limitations": "none"}, {"limitations": [""]},
                        {"status": "partial"}, {"read_ranges": []}, {"read_ranges": "all"}):
            with self.subTest(changes=changes):
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(with_receipt(receipt([item(**changes)])))

    def test_unknown_kind_requires_explicit_unread_boundary(self):
        observed = item(kind="unknown", status="unknown", reader=None, reference=None,
                        read_ranges=[], extent={"unit": "item", "total": None}, limitations=["format not identified"])
        self.assertEqual(m.build_ledger(with_receipt(receipt([observed], "unknown")))["sources"][0]["status"], "partial")
        with self.assertRaises(m.LedgerError):
            m.build_ledger(with_receipt(receipt([item(kind="unknown")])))

    def test_malformed_ranges_and_extents_are_rejected(self):
        page_ranges = [[{"start": 0, "end": 1}], [{"start": 1, "end": 6}],
                       [{"start": 3, "end": 2}], [{"start": 1.0, "end": 2}],
                       [{"start": True, "end": 2}], [{"start": 1, "end": 2}, {"start": 2, "end": 3}],
                       [{"start": 1}], [{"start": 1, "end": 1, "unexpected": "fictional"}]]
        time_ranges = [[{"start": -1, "end": 2}], [{"start": 0, "end": 0}],
                       [{"start": 3, "end": 2}], [{"start": 0, "end": 6}],
                       [{"start": 0, "end": 2.5}, {"start": 2, "end": 3}]]
        for kind, unit, ranges in [("pdf", "page", r) for r in page_ranges] + [("audio", "second", r) for r in time_ranges]:
            with self.subTest(kind=kind, ranges=ranges):
                observed = item(kind=kind, status="partial", extent={"unit": unit, "total": 5},
                                read_ranges=ranges, limitations=["fictional incomplete range"])
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(with_receipt(receipt([observed], "partial")))
        for extent in ({"unit": "second", "total": 1}, {"unit": "page", "total": 0},
                       {"unit": "page", "total": True}, {"unit": "page", "total": 1.5}):
            with self.subTest(extent=extent):
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(with_receipt(receipt([item(extent=extent)])))
        for total, ranges in ((2, [{"start": 1, "end": 1}]), (1, [{"start": 1, "end": 2}])):
            with self.assertRaises(m.LedgerError):
                m.build_ledger(with_receipt(receipt([item(kind="file", extent={"unit": "item", "total": total}, read_ranges=ranges)])))

    def test_nonfinite_range_values_are_rejected_as_non_json(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                observed = item(kind="audio", extent={"unit": "second", "total": value})
                with self.assertRaises(m.LedgerError):
                    m.build_ledger(with_receipt(receipt([observed])))

    def test_nonread_items_cannot_claim_ranges_or_sampling(self):
        for status in ("unread", "blocked", "unknown"):
            for ranges, sampled in (([{"start": 1, "end": 1}], False), ([], True)):
                with self.subTest(status=status, sampled=sampled):
                    observed = item(status=status, reader=None, reference=None, read_ranges=ranges,
                                    sampled=sampled, limitations=["fictional boundary"])
                    with self.assertRaises(m.LedgerError):
                        m.build_ledger(with_receipt(receipt([observed], "unknown" if status == "unknown" else "unread")))

    def test_channel_receipts_work_and_excluded_channel_is_not_reopened(self):
        data = with_receipt(receipt([item(status="partial", sampled=True, limitations=["sample only"])], "partial"), "channels")
        self.assertEqual(m.build_ledger(data)["channels"][0]["status"], "partial")
        data["channels"][0].update(status="user_excluded", reason="fictional explicit exclusion")
        data["records"][0].update(disposition="excluded", reason="channel excluded")
        self.assertEqual(m.build_ledger(data)["channels"][0]["status"], "user_excluded")

    def test_annotation_validation_does_not_open_media_or_prove_its_semantics(self):
        observed = item(reference="receipt:fictional-nonexistent-media")
        with patch("pathlib.Path.read_text", side_effect=AssertionError("must not open a media reference")):
            result = m.build_ledger(with_receipt(receipt([observed])))
        self.assertEqual(result["coverage"]["basis"], "supplied_agent_annotations_only")
        self.assertEqual(result["coverage"]["account_enumeration_complete"], "unknown")

    def test_invalid_receipt_cli_preserves_existing_output_and_redacts_error(self):
        marker = "FICTIONAL_PRIVATE_CONTENT_DO_NOT_PRINT"
        observed = item(attachment_id=marker, reason=marker, reference=marker,
                        status="partial", sampled=True, limitations=[marker])
        data = with_receipt(receipt([observed], "read"))  # Contradictory aggregate must fail.
        data["records"][0]["text"] = marker
        before = copy.deepcopy(data)
        with self.assertRaises(m.LedgerError) as error:
            m.build_ledger(data)
        self.assertNotIn(marker, str(error.exception))
        self.assertEqual(data, before)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            inp, out = base / "input.json", base / "ledger.json"
            inp.write_text(json.dumps(data), encoding="utf-8")
            out.write_bytes(b"fictional hand edited output\n")
            command = [sys.executable, "-B", str(SCRIPT), "--input", str(inp), "--output", str(out)]
            result = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(marker, result.stdout + result.stderr)
            self.assertEqual(out.read_bytes(), b"fictional hand edited output\n")
            out.unlink()
            result = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
