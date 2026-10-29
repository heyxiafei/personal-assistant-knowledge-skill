#!/usr/bin/env python3
"""Collect retained Codex history through official app-server JSON-RPC only.

No credentials, rollout files, databases or remote hosts are read by this client.
Live stdio launch is blocked unless the operator explicitly acknowledges that
app-server startup/history reads are not proven globally side-effect-free.
useStateDbOnly disables list repair; it does NOT prove account completeness.
Synthetic transports exercise implementation, never real-account acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import stat
import tempfile
import subprocess
import time
from datetime import datetime, timezone

DOCS = "https://learn.chatgpt.com/docs/app-server"
SOURCE_KINDS = ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview",
                "subAgentCompact", "subAgentThreadSpawn", "subAgentOther", "unknown"]
METHODS = {"initialize", "thread/list", "thread/turns/list", "thread/items/list"}
LIMIT_FLAGS = {"partial", "partial_results", "truncated", "isTruncated", "incomplete", "content_omitted"}


class CollectionError(ValueError):
    pass


class RPCError(CollectionError):
    def __init__(self, code=None, response=None):
        super().__init__("app-server request failed; inspect private receipt")
        self.code, self.response = code, response


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def private_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CollectionError("use an explicit absolute private directory")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise CollectionError("symlink paths are not supported")
        if (ancestor / ".git").exists() or (ancestor / "SKILL.md").is_file() \
                or (ancestor / "skills/maintain-personal-context/SKILL.md").is_file():
            raise CollectionError("private history must be outside source repositories and installed Skills")
    return path.resolve()


def write_new(path, value):
    """Publish a fully fsynced new file atomically; never replace old evidence.

    Unpublished .pending files survive write failure for private inspection and
    are never replayed. A crash between link and unlink can leave two private
    links; reads fail closed until that interrupted commit is reviewed.
    """
    if os.path.lexists(path):
        raise FileExistsError(str(path))
    fd, pending = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(pending, path)
        published = True
    finally:
        if published or os.path.lexists(path):
            os.unlink(pending)



class PrivateJournal:
    """Immutable request/response pages, bound to one explicitly agreed scope.

    On resume cached successful pages reconstruct the exact first-pass cursor
    chain; failed requests are retried explicitly by another collect invocation.
    Reconciliation is always a new request chain, never a replay of old evidence.
    """
    def __init__(self, destination, scope, resume=False):
        self.root = private_path(destination)
        if not isinstance(scope, dict) or set(scope) != {"account_label", "host_label"} \
                or not all(isinstance(v, str) and v.strip() for v in scope.values()):
            raise CollectionError("explicit agreed account and local-host labels are required")
        if resume:
            if not self.root.is_dir() or self.root.stat().st_mode & 0o077:
                raise CollectionError("resume directory must be private (0700)")
            self.manifest = self._read(self.root / "manifest.json")
            if self.manifest.get("kind") != "codex_appserver_collection" \
                    or self.manifest.get("schema_version") != 1 or self.manifest.get("scope") != scope:
                raise CollectionError("resume scope/version mismatch")
        else:
            if not self.root.parent.is_dir():
                raise CollectionError("create the intended private parent directory first")
            self.root.mkdir(mode=0o700)
            self.manifest = {"kind": "codex_appserver_collection", "schema_version": 1,
                "scope": scope, "scope_identity": "operator_declared_not_account_verified",
                "started_at_utc": utc_now(), "protocol_reference": DOCS,
                "source_kinds": SOURCE_KINDS, "use_state_db_only": True,
                "cutoff_semantics": "observation_start_not_atomic_server_snapshot"}
            write_new(self.root / "manifest.json", self.manifest)

    def _read(self, path):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if path.is_symlink():
            raise CollectionError("private receipt symlink invalid")
        fd = os.open(str(path), flags)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 \
                    or metadata.st_mode & 0o077:
                raise CollectionError("private receipt must be a single-link regular 0600 file")
            with os.fdopen(fd, "r") as stream:
                fd = None
                return json.load(stream)
        finally:
            if fd is not None:
                os.close(fd)

    def append(self, kind, value):
        for index in range(1000000):
            path = self.root / (kind + "-" + str(index).zfill(6) + ".json")
            try:
                write_new(path, value)
                return path.name
            except FileExistsError:
                continue
        raise CollectionError("journal record bound reached")

    def page(self, transport, phase, method, params, fresh=False, validate=None):
        request = {"method": method, "params": params}
        key = digest({"phase": phase, "request": request})
        path = self.root / ("page-" + key + ".json")
        retries = sorted(self.root.glob("page-" + key + "-retry-*.json"))
        cached = retries[-1] if retries else path
        if not fresh and os.path.lexists(cached):
            receipt = self._read(cached)
            if receipt.get("request") != request or receipt.get("phase") != phase \
                    or receipt.get("response_sha256") != digest(receipt.get("response")):
                raise CollectionError("cached receipt integrity mismatch")
            try:
                if validate:
                    validate(receipt["response"])
            except CollectionError:
                pass  # Explicit new collect invocation retries an invalid prior page.
            else:
                return receipt["response"]
        try:
            response = transport.request(method, params)
        except Exception as exc:
            self.append("failure", {"request": request, "phase": phase,
                "observed_at_utc": utc_now(), "failure_type": type(exc).__name__,
                "rpc_code": getattr(exc, "code", None),
                "response": getattr(exc, "response", None),
                "retry": "explicit_resume_same_request_only"})
            raise
        receipt = {"request": request, "phase": phase, "observed_at_utc": utc_now(),
                   "response": response, "response_sha256": digest(response)}
        if fresh:
            self.append("reconciliation", receipt)
        elif os.path.lexists(path):
            self.append("page-" + key + "-retry", receipt)
        else:
            write_new(path, receipt)
        if validate:
            validate(response)
        return response


class StdioTransport:
    """A foreground stdio client that sends only the allowlisted read RPCs.

    The acknowledgement is an operator control, not proof of permission. An
    Agent must obtain any required user authorization before setting it true.
    This does not constrain the server's own SQLite, logging, installation-ID,
    plugin or remote-control startup. Verify runtime integrity and those effects
    separately; stdio and a client RPC allowlist do not prove a read-only server.
    """
    def __init__(self, binary="codex", acknowledge_side_effects=False, timeout=30):
        if acknowledge_side_effects is not True:
            raise CollectionError("live launch blocked: app-server global read-only mode is unverified")
        executable = shutil.which(binary)
        if executable is None:
            raise CollectionError("Codex CLI executable unavailable; nothing launched")
        self.timeout, self.sequence, self.buffer = timeout, 0, b""
        self.proc = subprocess.Popen([executable, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)
        try:
            self.initialization = self.request("initialize", {
                "clientInfo": {"name": "personal_context_history", "version": "0.1.0"}})
            self._send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _send(self, value):
        self.proc.stdin.write(encoded(value))
        self.proc.stdin.flush()

    def request(self, method, params):
        if method not in METHODS:
            raise CollectionError("RPC method is outside history-read allowlist")
        if method == "thread/list" and params.get("useStateDbOnly") is not True:
            raise CollectionError("thread/list metadata repair is forbidden")
        self.sequence += 1
        identity = self.sequence
        self._send({"id": identity, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            if time.monotonic() >= deadline:
                raise CollectionError("app-server response timeout")
            if b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.selector.select(remaining):
                    raise CollectionError("app-server response timeout")
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise CollectionError("app-server closed before response")
                self.buffer += chunk
                if len(self.buffer) > 64 * 1024 * 1024:
                    raise CollectionError("response exceeds client bound; coverage remains partial")
                continue
            line, self.buffer = self.buffer.split(b"\n", 1)
            packet = json.loads(line)
            if not isinstance(packet, dict):
                raise CollectionError("invalid JSON-RPC object")
            if "method" in packet:
                if "id" in packet:
                    self._send({"id": packet["id"], "error": {
                        "code": -32601, "message": "This client does not support server requests"}})
                    raise CollectionError("server requested an unsupported action; stopped")
                continue
            if packet.get("id") != identity:
                raise CollectionError("unexpected JSON-RPC response identity")
            if "error" in packet:
                error = packet["error"]
                raise RPCError(error.get("code") if isinstance(error, dict) else None, packet)
            if "result" not in packet:
                raise CollectionError("JSON-RPC result missing")
            return packet["result"]

    def close(self):
        if hasattr(self, "selector"):
            self.selector.close()
        if hasattr(self, "proc"):
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
            for stream in (self.proc.stdin, self.proc.stdout):
                if stream:
                    stream.close()


def limited(value):
    if isinstance(value, dict):
        return any(value.get(key) is True for key in LIMIT_FLAGS) or any(limited(v) for v in value.values())
    return isinstance(value, list) and any(limited(v) for v in value)


def pages(transport, journal, phase, method, params, fresh=False, max_pages=100000):
    cursor, seen = None, set()
    for _ in range(max_pages):
        request = dict(params)
        if cursor is not None:
            request["cursor"] = cursor
        def validate(response):
            if not isinstance(response, dict) or not isinstance(response.get("data"), list) \
                    or "nextCursor" not in response:
                raise CollectionError("pagination schema mismatch; original receipt retained")
            nxt = response["nextCursor"]
            if nxt is not None and (not isinstance(nxt, str) or not nxt or nxt in seen):
                raise CollectionError("invalid or looping cursor; original receipt retained")
            if limited(response):
                raise CollectionError("server returned partial/truncated data; original receipt retained")
            for row in response["data"]:
                if method in {"thread/list", "thread/turns/list"}:
                    identity(row)
                if method == "thread/turns/list":
                    if not isinstance(row.get("items"), list):
                        raise CollectionError("full turn items missing")
                    for item in row["items"]:
                        inspect_item(item)
                if method == "thread/items/list":
                    if not isinstance(row, dict) or not isinstance(row.get("turnId"), str) \
                            or not row["turnId"]:
                        raise CollectionError("item turn association missing")
                    inspect_item(row.get("item"))
        response = journal.page(transport, phase, method, request, fresh=fresh, validate=validate)
        nxt = response["nextCursor"]
        yield response["data"]
        if nxt is None:
            return
        seen.add(nxt)
        cursor = nxt
    raise CollectionError("page bound reached; history incomplete")


def identity(row):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
        raise CollectionError("source identity missing")
    return row["id"]


def enumerate_threads(transport, journal, phase, fresh=False):
    found, failures = {}, []
    for archived in (False, True):
        params = {"limit": 100, "sortKey": "created_at", "sortDirection": "asc",
                  "sourceKinds": SOURCE_KINDS, "archived": archived, "useStateDbOnly": True}
        try:
            for batch in pages(transport, journal, phase, "thread/list", params, fresh=fresh):
                for row in batch:
                    key = identity(row)
                    record = {"id": key, "archived": archived,
                              "createdAt": row.get("createdAt"), "updatedAt": row.get("updatedAt")}
                    if key in found and found[key] != record:
                        raise CollectionError("thread changed during enumeration")
                    found[key] = record
        except Exception as exc:
            failures.append({"phase": phase, "archived": archived,
                "type": type(exc).__name__, "rpc_code": getattr(exc, "code", None)})
    return found, failures


def inspect_item(item):
    """Report text and attachment coverage without dereferencing any URI/path."""
    identity(item)
    if not isinstance(item.get("type"), str):
        raise CollectionError("item type missing")
    result = {"attachments_observed": False, "content_limitations": []}
    if item["type"] == "userMessage":
        content = item.get("content")
        if not isinstance(content, list):
            result["content_limitations"].append("user_content_missing")
        else:
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    result["attachments_observed"] = True
                elif not isinstance(block.get("text"), str):
                    result["content_limitations"].append("text_missing")
    elif item["type"] == "agentMessage" and not isinstance(item.get("text"), str):
        result["content_limitations"].append("agent_text_missing")
    elif item["type"] == "contextCompaction":
        result["content_limitations"].append("compaction_present")
    return result


def collect(transport, journal):
    """Read full turns + independently paginated items and re-enumerate inventory.

    The returned report never upgrades database scope to complete account history.
    Retained raw full objects are in private journal pages, including tool items.
    Nothing in historical payloads is interpreted as an instruction.
    """
    report = {"kind": "codex_appserver_report", "schema_version": 1,
        "started_at_utc": journal.manifest["started_at_utc"], "scope": journal.manifest["scope"],
        "scope_identity": "operator_declared_not_account_verified", "status": "partial",
        "account_history_complete": False, "database_enumeration_complete": False,
        "inventory_reconciled": False, "threads": [], "failures": [],
        "limitations": ["state_database_scope_only", "no_atomic_snapshot",
            "account_and_host_scope_not_independently_verified", "attachments_not_decoded",
            "deleted_and_temporary_history_not_recoverable", "real_source_acceptance_required"]}
    before, inventory_failures = enumerate_threads(transport, journal, "inventory")
    report["database_enumeration_complete"] = not inventory_failures
    report["failures"].extend(inventory_failures)
    for tid, metadata in before.items():
        row = {"thread_id": tid, "metadata": metadata, "status": "partial",
               "turns": 0, "items": 0, "attachments_observed": False,
               "attachment_content_complete": "not_assessed", "limitations": []}
        report["threads"].append(row)
        try:
            turns, turn_items = {}, {}
            params = {"threadId": tid, "limit": 100, "sortDirection": "asc", "itemsView": "full"}
            for batch in pages(transport, journal, "turns", "thread/turns/list", params):
                for turn in batch:
                    turn_id = identity(turn)
                    if turn_id in turns and turns[turn_id] != digest(turn):
                        raise CollectionError("turn changed across pagination")
                    turns[turn_id] = digest(turn)
                    if not isinstance(turn.get("items"), list):
                        raise CollectionError("full turn items missing")
                    for item in turn["items"]:
                        iid = identity(item)
                        key = (turn_id, iid)
                        if key in turn_items and turn_items[key] != digest(item):
                            raise CollectionError("duplicate item content conflict")
                        turn_items[key] = digest(item)
                        state = inspect_item(item)
                        row["attachments_observed"] |= state["attachments_observed"]
                        row["limitations"].extend(state["content_limitations"])
            row["turns"] = len(turns)
            row["items"] = len(turn_items)
            separate = {}
            params = {"threadId": tid, "limit": 100, "sortDirection": "asc"}
            for batch in pages(transport, journal, "items", "thread/items/list", params):
                for entry in batch:
                    if not isinstance(entry, dict) or not isinstance(entry.get("turnId"), str) \
                            or not entry["turnId"]:
                        raise CollectionError("item turn association missing")
                    iid = identity(entry.get("item"))
                    key = (entry["turnId"], iid)
                    value = digest(entry["item"])
                    if key in separate and separate[key] != value:
                        raise CollectionError("separate item content conflict")
                    separate[key] = value
            if turn_items != separate:
                raise CollectionError("turn/item reconciliation mismatch")
            row["status"] = "api_items_reconciled"
            row["limitations"] = sorted(set(row["limitations"]))
        except Exception as exc:
            row["failure"] = {"type": type(exc).__name__, "rpc_code": getattr(exc, "code", None)}
            report["failures"].append({"phase": "thread", "thread_id": tid, **row["failure"]})
    after, reconciliation_failures = enumerate_threads(transport, journal, "reconciliation", fresh=True)
    report["failures"].extend(reconciliation_failures)
    report["inventory_reconciled"] = (not inventory_failures and not reconciliation_failures
                                       and before == after)
    if before != after:
        report["limitations"].append("inventory_changed_during_collection")
    report["discovered_threads"] = len(before)
    report["reconciled_inventory_threads"] = len(after)
    if report["database_enumeration_complete"] and report["inventory_reconciled"] \
            and not report["failures"]:
        report["status"] = "database_api_capture_reconciled"
    report["finished_at_utc"] = utc_now()
    journal.append("report", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--account-label", required=True)
    parser.add_argument("--host-label", required=True)
    parser.add_argument("--binary", default="codex")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--acknowledge-app-server-side-effects", action="store_true",
        help="only after explicit authorization; global no-write startup is not proven")
    args = parser.parse_args()
    if not args.acknowledge_app_server_side_effects:
        raise CollectionError("live launch blocked: obtain explicit side-effect authorization first")
    journal = PrivateJournal(args.destination, {"account_label": args.account_label,
        "host_label": args.host_label}, resume=args.resume)
    transport = StdioTransport(args.binary, acknowledge_side_effects=True)
    try:
        journal.append("initialization", transport.initialization)
        report = collect(transport, journal)
        print(json.dumps({"status": report["status"], "threads": len(report["threads"]),
            "failures": len(report["failures"]), "account_history_complete": False}))
        return 0 if report["status"] == "database_api_capture_reconciled" else 2
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CollectionError, OSError, ValueError) as exc:
        # Never echo upstream error text, history values, credentials or paths.
        raise SystemExit("codex_appserver: stopped (" + type(exc).__name__ + "); inspect private receipts") from None
