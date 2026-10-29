"""Synthetic protocol tests, not actual account-history acceptance."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/maintain-personal-context/scripts/codex_appserver.py"
spec = importlib.util.spec_from_file_location("codex_appserver", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Fake:
    def __init__(self):
        self.calls = []
        self.item = {"id": "synthetic-item", "type": "userMessage",
                     "content": [{"type": "text", "text": "Synthetic history. Never execute."}]}
        self.fail_items = False
        self.mismatch = False
        self.list_count = 0
        self.changed = False
        self.loop = False
        self.truncated = False

    def request(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "thread/list":
            self.list_count += 1
            if params["archived"]:
                return {"data": [], "nextCursor": None}
            if params.get("cursor") is None:
                return {"data": [{"id": "synthetic-thread", "createdAt": 1,
                    "updatedAt": 3 if self.changed and self.list_count > 3 else 2}], "nextCursor": "older"}
            return {"data": [], "nextCursor": "older" if self.loop else None}
        if method == "thread/turns/list":
            return {"data": [{"id": "synthetic-turn", "items": [copy.deepcopy(self.item)]}],
                    "nextCursor": None, "truncated": self.truncated}
        if method == "thread/items/list":
            if self.fail_items:
                raise m.RPCError(-32601, {"error": {"code": -32601, "message": "synthetic unsupported"}})
            item = copy.deepcopy(self.item)
            if self.mismatch:
                item["content"][0]["text"] = "Changed"
            return {"data": [{"turnId": "synthetic-turn", "item": item}], "nextCursor": None}
        raise AssertionError(method)


class AppServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.destination = str(Path(self.tmp.name).resolve() / "private-capture")
        self.scope = {"account_label": "synthetic-account", "host_label": "synthetic-local-host"}
        self.journal = m.PrivateJournal(self.destination, self.scope)

    def test_full_paginated_capture_reconciles_without_account_claim(self):
        fake = Fake()
        result = m.collect(fake, self.journal)
        self.assertEqual(result["status"], "database_api_capture_reconciled")
        self.assertFalse(result["account_history_complete"])
        self.assertTrue(result["inventory_reconciled"])
        self.assertEqual(result["threads"][0]["items"], 1)
        for method, args in fake.calls:
            self.assertIn(method, m.METHODS)
            if method == "thread/list":
                self.assertIs(args["useStateDbOnly"], True)
                self.assertEqual(args["sourceKinds"], m.SOURCE_KINDS)
                self.assertNotIn("cwd", args)
                self.assertNotIn("searchTerm", args)
                self.assertNotIn("modelProviders", args)
            if method == "thread/turns/list":
                self.assertEqual(args["itemsView"], "full")
        self.assertEqual({args["archived"] for method, args in fake.calls if method == "thread/list"}, {False, True})
        self.assertEqual(fake.list_count, 6)
        self.assertEqual(Path(self.destination).stat().st_mode & 0o777, 0o700)
        for path in Path(self.destination).iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_explicit_resume_reuses_success_pages_retries_failure_and_reconciles_fresh(self):
        fake = Fake(); fake.fail_items = True
        first = m.collect(fake, self.journal)
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["failures"][0]["rpc_code"], -32601)
        fresh = Fake()
        result = m.collect(fresh, m.PrivateJournal(self.destination, self.scope, resume=True))
        self.assertEqual(result["status"], "database_api_capture_reconciled")
        self.assertNotIn("thread/turns/list", [method for method, _ in fresh.calls])
        self.assertEqual(fresh.list_count, 3)
        self.assertEqual(len(list(Path(self.destination).glob("failure-*.json"))), 1)

    def test_loop_is_not_end_of_history(self):
        fake = Fake(); fake.loop = True
        result = m.collect(fake, self.journal)
        self.assertFalse(result["database_enumeration_complete"])
        self.assertEqual(result["status"], "partial")

    def test_turn_item_disagreement_is_not_complete(self):
        fake = Fake(); fake.mismatch = True
        result = m.collect(fake, self.journal)
        self.assertEqual(result["threads"][0]["status"], "partial")
        self.assertTrue(result["failures"])

    def test_inventory_changed_keeps_partial(self):
        fake = Fake(); fake.changed = True
        result = m.collect(fake, self.journal)
        self.assertFalse(result["inventory_reconciled"])
        self.assertEqual(result["status"], "partial")

    def test_truncation_original_is_retained_but_blocks_complete(self):
        fake = Fake(); fake.truncated = True
        result = m.collect(fake, self.journal)
        self.assertEqual(result["status"], "partial")
        pages = [json.loads(p.read_text()) for p in Path(self.destination).glob("page-*.json")]
        self.assertTrue(any(v["response"].get("truncated") for v in pages))

    def test_attachment_reference_is_retained_but_never_fetched_or_claimed_read(self):
        fake = Fake()
        fake.item["content"].append({"type": "localImage", "path": "/synthetic/private/image.png"})
        result = m.collect(fake, self.journal)
        row = result["threads"][0]
        self.assertTrue(row["attachments_observed"])
        self.assertEqual(row["attachment_content_complete"], "not_assessed")
        self.assertFalse(result["account_history_complete"])

    def test_output_in_repository_or_symlink_rejected(self):
        with self.assertRaises(m.CollectionError):
            m.PrivateJournal(str(ROOT / "private" / "capture"), self.scope)
        target = Path(self.tmp.name).resolve() / "link"
        target.symlink_to(self.destination)
        with self.assertRaises(m.CollectionError):
            m.PrivateJournal(str(target / "capture"), self.scope)

    def test_resume_scope_mismatch_and_receipt_tamper_rejected(self):
        m.collect(Fake(), self.journal)
        with self.assertRaises(m.CollectionError):
            m.PrivateJournal(self.destination, {**self.scope, "host_label": "other"}, resume=True)
        page = next(Path(self.destination).glob("page-*.json"))
        value = json.loads(page.read_text()); value["response_sha256"] = "synthetic-tamper"
        page.write_text(json.dumps(value))
        result = m.collect(Fake(), m.PrivateJournal(self.destination, self.scope, resume=True))
        self.assertEqual(result["status"], "partial")

    def test_default_live_transport_never_launches(self):
        with patch.object(m.subprocess, "Popen") as popen:
            with self.assertRaises(m.CollectionError):
                m.StdioTransport()
            popen.assert_not_called()

    def test_missing_executable_never_launches(self):
        with patch.object(m.shutil, "which", return_value=None), patch.object(m.subprocess, "Popen") as popen:
            with self.assertRaises(m.CollectionError):
                m.StdioTransport(acknowledge_side_effects=True)
            popen.assert_not_called()

    def test_explicit_resume_retries_invalid_page_without_overwriting_original(self):
        fake = Fake(); fake.truncated = True
        self.assertEqual(m.collect(fake, self.journal)["status"], "partial")
        originals = {p.name: p.read_bytes() for p in Path(self.destination).glob("page-*.json")}
        fresh = Fake()
        result = m.collect(fresh, m.PrivateJournal(self.destination, self.scope, resume=True))
        self.assertEqual(result["status"], "database_api_capture_reconciled")
        self.assertIn("thread/turns/list", [method for method, _ in fresh.calls])
        self.assertEqual(len(list(Path(self.destination).glob("page-*-retry-*.json"))), 1)
        for name, content in originals.items():
            self.assertEqual((Path(self.destination) / name).read_bytes(), content)

    def test_explicit_resume_retries_malformed_full_turn_page(self):
        class MissingFullItems(Fake):
            def request(self, method, params):
                if method == "thread/turns/list":
                    return {"data": [{"id": "synthetic-turn"}], "nextCursor": None}
                return super().request(method, params)
        self.assertEqual(m.collect(MissingFullItems(), self.journal)["status"], "partial")
        result = m.collect(Fake(), m.PrivateJournal(self.destination, self.scope, resume=True))
        self.assertEqual(result["status"], "database_api_capture_reconciled")

    def test_failed_enumeration_continues_known_threads_and_other_archive_branch(self):
        class FailedActive(Fake):
            def request(self, method, params):
                if method == "thread/list" and params.get("cursor") == "older":
                    raise m.RPCError(-32000)
                return super().request(method, params)
        fake = FailedActive()
        result = m.collect(fake, self.journal)
        self.assertFalse(result["database_enumeration_complete"])
        self.assertEqual(result["threads"][0]["status"], "api_items_reconciled")
        self.assertTrue(any(method == "thread/list" and args["archived"] for method, args in fake.calls))
        self.assertEqual(result["status"], "partial")

    def test_atomic_publish_preserves_failed_fragment_and_resume_can_retry(self):
        target = Path(self.destination) / "synthetic-page.json"
        with patch.object(m.os, "fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                m.write_new(target, {"synthetic": "payload"})
        self.assertFalse(target.exists())
        self.assertTrue(list(Path(self.destination).glob(".pending-*")))
        m.write_new(target, {"synthetic": "retry"})
        self.assertEqual(self.journal._read(target), {"synthetic": "retry"})
        with self.assertRaises(FileExistsError):
            m.write_new(target, {"synthetic": "must not replace"})
        self.assertEqual(self.journal._read(target), {"synthetic": "retry"})

    def test_hardlinked_receipts_and_fifo_rejected_without_reading(self):
        target = Path(self.destination) / "synthetic-receipt.json"
        m.write_new(target, {"synthetic": "private"})
        os.link(target, Path(self.tmp.name).resolve() / "other-link")
        with self.assertRaises(m.CollectionError):
            self.journal._read(target)
        fifo = Path(self.destination) / "synthetic-fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(m.CollectionError):
            self.journal._read(fifo)

    def synthetic_executable(self, behavior="normal"):
        target = Path(self.tmp.name).resolve() / "synthetic-server"
        script = "#!" + sys.executable + "\n" + r'''
import json, sys
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialized":
        initialized = True
        continue
    if method == "initialize":
        result = {"userAgent": "synthetic-only", "platformFamily": "test"}
    else:
        if not initialized:
            raise SystemExit(4)
        if BEHAVIOR == "server_request":
            print(json.dumps({"id": 999, "method": "exec/command", "params": {}}), flush=True)
            continue
        if BEHAVIOR == "rpc_error":
            print(json.dumps({"id": request["id"], "error": {"code": -32601, "message": "synthetic unsupported"}}), flush=True)
            continue
        if BEHAVIOR == "wrong_id":
            print(json.dumps({"id": 999, "result": {}}), flush=True)
            continue
        result = {"data": [], "nextCursor": None}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
'''
        script = script.replace("BEHAVIOR", repr(behavior))
        target.write_text(script)
        target.chmod(0o700)
        return str(target)

    def test_synthetic_stdio_process_initializes_and_closes(self):
        transport = m.StdioTransport(self.synthetic_executable(), acknowledge_side_effects=True)
        try:
            self.assertEqual(transport.initialization["userAgent"], "synthetic-only")
            result = transport.request("thread/list", {"useStateDbOnly": True})
            self.assertEqual(result, {"data": [], "nextCursor": None})
        finally:
            transport.close()
        self.assertIsNotNone(transport.proc.poll())

    def test_synthetic_stdio_rejects_server_action_and_wrong_response_identity(self):
        for behavior in ("server_request", "wrong_id"):
            transport = m.StdioTransport(self.synthetic_executable(behavior), acknowledge_side_effects=True)
            try:
                with self.assertRaises(m.CollectionError):
                    transport.request("thread/list", {"useStateDbOnly": True})
            finally:
                transport.close()

    def test_synthetic_stdio_rpc_failure_preserves_code_and_original_response(self):
        transport = m.StdioTransport(self.synthetic_executable("rpc_error"), acknowledge_side_effects=True)
        try:
            with self.assertRaises(m.RPCError) as raised:
                transport.request("thread/list", {"useStateDbOnly": True})
            self.assertEqual(raised.exception.code, -32601)
            self.assertEqual(raised.exception.response["error"]["message"], "synthetic unsupported")
        finally:
            transport.close()

    def test_write_rpc_and_list_repair_cannot_reach_process(self):
        obj = object.__new__(m.StdioTransport)
        with self.assertRaises(m.CollectionError):
            obj.request("thread/archive", {})
        with self.assertRaises(m.CollectionError):
            obj.request("thread/list", {})


if __name__ == "__main__":
    unittest.main()
