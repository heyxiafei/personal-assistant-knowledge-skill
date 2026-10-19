#!/usr/bin/env python3
"""Private receipt journal for an Agent using real Space tools. No network/API calls."""
from __future__ import annotations

import argparse
import copy
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

ROLES = {
    "root": ("个人助手知识库", None),
    "identity": ("我是谁", "root"),
    "experience": ("经历技能", "root"),
    "resources": ("资源协作", "root"),
    "goals": ("目标", "root"),
    "projects": ("日常项目", "root"),
    "results": ("成果复盘", "root"),
    "sources": ("来源纠错", "root"),
    "workbench": ("工作台", "root"),
    "profile": ("稳定档案", "identity"),
    "daily": ("日常日志", "projects"),
    "long_term": ("精选长期结论", "results"),
    "setup": ("建库与覆盖记录", "sources"),
}
READ_STATUSES = {"discovered_unread", "full", "partial", "failed", "inaccessible"}


class StateError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def new_state(name, as_of, authorized_scope):
    if not all(isinstance(x, str) and x.strip() for x in (name, as_of, authorized_scope)):
        raise StateError("name, explicit as_of and authorized_scope are required")
    try:
        if date.fromisoformat(as_of).isoformat() != as_of:
            raise ValueError()
    except ValueError:
        raise StateError("as_of must be an explicit ISO calendar date")
    return {"schema_version": 1, "run_id": str(uuid.uuid4()), "name": name,
            "as_of": as_of, "authorized_scope": authorized_scope, "revision": 0,
            "space": None, "pages": {}, "operations": {}, "sources": {},
            "coverage": {}, "privacy": {}, "events": []}


def marker(state, role):
    if role not in ROLES:
        raise StateError("unknown role")
    return "个人助手建库标记：" + state["run_id"] + ":" + role


def event(state, action, detail):
    state["revision"] += 1
    state["events"].append({"revision": state["revision"], "action": action, "detail": detail})


def begin_create(state, role):
    if role != "space" and role not in ROLES:
        raise StateError("unknown role")
    if role == "root":
        raise StateError("root is returned by create_space; never create a second root")
    if (role == "space" and state["space"]) or role in state["pages"]:
        raise StateError("already mapped; read and verify, do not create")
    if any(o["role"] == role and o["status"] in ("pending", "unknown")
           for o in state["operations"].values()):
        raise StateError("unresolved outcome; reconcile before any new creation")
    if role != "space":
        parent = ROLES[role][1]
        if parent not in state["pages"] or not state["pages"][parent].get("verified"):
            raise StateError("parent must be mapped and verified")
        if not state["privacy"].get("root", {}).get("owner_only"):
            raise StateError("privacy unverified; stop before writing personal data")
    op_id = str(uuid.uuid4())
    state["operations"][op_id] = {"role": role, "status": "pending"}
    event(state, "begin_create", {"operation": op_id, "role": role})
    return op_id


def unknown(state, op_id):
    op = state["operations"].get(op_id)
    if not op or op["status"] == "verified":
        raise StateError("operation is missing or already verified")
    op["status"] = "unknown"
    event(state, "unknown", {"operation": op_id})


def accept_created(state, op_id, receipt):
    """Accept a real creation receipt, not a success flag invented by a caller."""
    op = state["operations"].get(op_id)
    if not op or op["status"] not in ("pending", "unknown"):
        raise StateError("no pending creation")
    if receipt.get("error"):
        raise StateError("creation returned an error; record outcome and reconcile")
    role = op["role"]
    if role == "space":
        if receipt.get("name") != state["name"] or not receipt.get("id") or not receipt.get("root_page_id"):
            raise StateError("Space identity does not match the requested creation")
        if state["space"]:
            raise StateError("Space already mapped")
        state["space"] = {k: receipt[k] for k in ("id", "root_page_id", "name")}
        state["pages"]["root"] = {"page_id": receipt["root_page_id"], "verified": False}
    else:
        page_id = receipt.get("page_id") or receipt.get("id") \
            or receipt.get("content", {}).get("page_id") \
            or receipt.get("metadata", {}).get("page_id")
        if not page_id or role in state["pages"]:
            raise StateError("missing Page identity or already mapped")
        if page_id in [p["page_id"] for p in state["pages"].values()]:
            raise StateError("one Page cannot silently fulfill two roles")
        state["pages"][role] = {"page_id": page_id, "verified": False,
                                "parent_role": ROLES[role][1]}
    op["status"] = "created"
    op["receipt_digest"] = digest(receipt)
    event(state, "created", {"operation": op_id, "role": role})


def reconcile_found(state, op_id, candidates, enumeration_complete, same_run_evidence=False):
    """Candidates are read-back objects, not name-only search hits."""
    op = state["operations"].get(op_id)
    if not op or op["status"] not in ("pending", "unknown"):
        raise StateError("no unresolved creation")
    if enumeration_complete is not True:
        raise StateError("incomplete search cannot establish absence or unique ownership")
    role = op["role"]
    if role == "space":
        matching = [c for c in candidates if c.get("name") == state["name"]]
        # An exact title alone does not prove ownership. Before root marking, the
        # caller needs a captured original receipt or uniquely corroborated run evidence.
        if not same_run_evidence:
            raise StateError("name alone does not identify an owned Space; stop for a targeted choice")
    else:
        matching = [c for c in candidates if marker(state, role) in c.get("markdown", "")]
    if len(matching) != 1:
        raise StateError("missing or ambiguous creation; no blind retry")
    accept_created(state, op_id, matching[0])


def verify_page(state, role, read):
    mapping = state["pages"].get(role)
    content, metadata = read.get("content"), read.get("metadata", {})
    if not mapping or not content or content.get("page_id") != mapping["page_id"]:
        raise StateError("read-back Page identity mismatch or unavailable content")
    if read.get("selection") and not read["selection"].get("whole_page_complete"):
        raise StateError("partial selection cannot verify the entire managed content")
    if read.get("view") == "excerpts":
        raise StateError("excerpts cannot verify the complete managed content")
    blocks = content.get("blocks", [])
    text = "\n\n".join(b.get("markdown", "") for b in blocks)
    if text.count(marker(state, role)) != 1:
        raise StateError("missing or duplicate ownership marker")
    expected_title = state["name"] if role == "root" else ROLES[role][0]
    if content.get("title") != expected_title:
        raise StateError("title changed; review before claiming the mapped role")
    if metadata.get("drive_space_id") != state["space"]["id"]:
        raise StateError("wrong or unknown Space placement")
    if role != "root":
        parent = metadata.get("parent")
        if not isinstance(parent, dict) or parent.get("page_id") != state["pages"][ROLES[role][1]]["page_id"]:
            raise StateError("wrong or unknown parent; links do not establish hierarchy")
    if metadata.get("access", {}).get("can_write") is not True:
        raise StateError("write permission unavailable")
    if any(not b.get("id") or not b.get("hash") for b in blocks):
        raise StateError("missing canonical block guards")
    mapping.update({"verified": True, "url": metadata.get("url"), "title": content["title"],
                    "stream_kind": metadata.get("stream_kind", "content"),
                    "block_hashes": {b["id"]: b["hash"] for b in blocks},
                    "read_digest": digest(read)})
    for op in state["operations"].values():
        if op["role"] == role or (role == "root" and op["role"] == "space"):
            if op["status"] == "created":
                op["status"] = "verified"
    event(state, "verified", {"role": role})


def check_preservation(state, role, read):
    """Detect changed/deleted known blocks; added user blocks remain untouched."""
    mapping = state["pages"].get(role)
    content = read.get("content")
    if not mapping or not mapping.get("verified") or not content:
        raise StateError("full current read and verified mapping required")
    if content.get("page_id") != mapping["page_id"] or read.get("view") == "excerpts":
        raise StateError("wrong or partial read")
    if read.get("selection") and not read["selection"].get("whole_page_complete"):
        raise StateError("partial read")
    observed = {b["id"]: b["hash"] for b in content.get("blocks", [])}
    changed = [bid for bid, old in mapping["block_hashes"].items() if observed.get(bid) != old]
    return {"changed_known_blocks": changed,
            "added_blocks": [bid for bid in observed if bid not in mapping["block_hashes"]],
            "safe_to_reuse_old_plan": not changed}


def record_privacy(state, role, sharing, requested_page_id=None):
    evidence_page_id = sharing.get("page_id", requested_page_id)
    if role not in state["pages"] or evidence_page_id != state["pages"][role]["page_id"]:
        raise StateError("sharing evidence must identify the mapped Page")
    limited_owner_check = sharing.get("scope") == "page_owner" and sharing.get("owner_only") is True
    if role == "root" and not limited_owner_check and sharing.get("space_id") != state["space"]["id"]:
        raise StateError("root sharing evidence must identify the mapped Space")
    inherited = sharing.get("inherited_access", {})
    owner_only = limited_owner_check
    if sharing.get("sharing_available") is True:
        # Require actual owner and complete inherited/roster evidence. Empty/missing
        # arrays or an owning workspace alone are never treated as private.
        owner_only = bool(sharing.get("owner")) and sharing.get("shares") == [] \
            and inherited.get("complete") is True and inherited.get("sources") == [] \
            and sharing.get("next_cursor") is None
    state["privacy"][role] = {"owner_only": owner_only,
                              "evidence_digest": digest(sharing)}
    event(state, "privacy", {"role": role, "owner_only": owner_only})
    return owner_only


def record_source(state, source):
    required = ("kind", "host", "id", "status", "read_at", "granularity")
    if any(k not in source for k in required) or source["status"] not in READ_STATUSES:
        raise StateError("source identity, status, date and granularity required")
    if source["status"] == "full" and source["granularity"] != "full_messages":
        raise StateError("a summary/search hit is not a full source")
    if source["status"] == "full" and source.get("next_cursor"):
        raise StateError("remaining pagination cursor: use partial and preserve the cursor; conversation is not completely read")
    key = digest([source["kind"], source["host"], source["id"]])
    allowed = set(required) | {"url", "record_date", "next_cursor", "reason", "content_digest", "source_date"}
    if set(source) - allowed:
        raise StateError("source data cannot add authorization or executable instructions")
    previous = state["sources"].get(key)
    if previous == source:
        return key, False
    state["sources"][key] = copy.deepcopy(source)
    event(state, "source", {"key": key, "status": source["status"]})
    return key, True


def record_coverage(state, channel, coverage):
    if channel not in ("codex", "chatgpt", "projects", "memory", "materials"):
        raise StateError("unknown history channel")
    required = {"tool", "scope", "checked_at", "enumeration_complete", "total", "next_cursor", "limitations"}
    if set(coverage) != required or coverage["enumeration_complete"] not in (True, False, "unknown"):
        raise StateError("explicit coverage and continuation fields required")
    if coverage["total"] != "unknown" and (not isinstance(coverage["total"], int) or coverage["total"] < 0):
        raise StateError("total must be a known nonnegative number or unknown")
    if coverage["next_cursor"] and coverage["enumeration_complete"] is True:
        raise StateError("remaining cursor contradicts complete enumeration")
    state["coverage"][channel] = copy.deepcopy(coverage)
    event(state, "coverage", {"channel": channel})


def edit_disposition(receipt):
    """Inspect real edit receipts without treating mixed/unknown saves as success."""
    error = receipt.get("error", {})
    operations = receipt.get("operation_results", error.get("details", {}).get("operation_results", []))
    unknown_commit = error.get("commit_status") == "unknown"
    statuses = []
    for op in operations:
        if op.get("ignored_reason"):
            statuses.append("ignored")
        elif op.get("status") == "rejected":
            statuses.append("rejected")
        elif op.get("status") in (None, "applied"):
            statuses.append("applied")
        else:
            statuses.append("unknown")
    if unknown_commit or "unknown" in statuses:
        action = "read_and_reconcile_before_any_write"
    elif "rejected" in statuses or "ignored" in statuses:
        action = "reread_preserve_applied_rebuild_only_unapplied"
    elif error or not operations:
        action = "stop_and_report_no_confirmed_operations"
    else:
        action = "verify_readback"
    return {"operation_statuses": statuses, "action": action,
            "unknown_commit": unknown_commit, "receipt_digest": digest(receipt)}


def summary(state):
    counts = {x: 0 for x in READ_STATUSES}
    for source in state["sources"].values():
        # Conservative display for a legacy journal containing the old invalid
        # full+cursor combination. Loading it must not claim complete history.
        status = "partial" if source["status"] == "full" and source.get("next_cursor") else source["status"]
        counts[status] += 1
    return {"space_mapped": bool(state["space"]),
            "verified_roles": sorted(k for k, p in state["pages"].items() if p.get("verified")),
            "pending_roles": [k for k in ROLES if not state["pages"].get(k, {}).get("verified")],
            "unresolved_operations": [k for k, o in state["operations"].items() if o["status"] in ("pending", "unknown")],
            "privacy_verified": all(state["privacy"].get(k, {}).get("owner_only") for k in ROLES),
            "discovered": len(state["sources"]), "read_counts": counts,
            "coverage_channels": sorted(state["coverage"]),
            "pending_change_sets": sorted(k for k, v in state.get("change_sets", {}).items() if v["status"] != "verified"),
            "overall_total": "unknown", "revision": state["revision"]}


def begin_change_set(state, change_id, targets, confirmation):
    """Track all affected Page projections; this does not judge or grant consent."""
    if not change_id or change_id in state.get("change_sets", {}):
        raise StateError("new change identity required; reconcile an existing change")
    if not isinstance(confirmation, dict) or set(confirmation) != {"source", "date", "basis"}:
        raise StateError("explicit confirmation source, date and basis required")
    if not confirmation["source"] or confirmation["basis"] not in ("current_user_authorization", "explicit_save_message"):
        raise StateError("message/event date alone is not consent to save")
    try:
        if date.fromisoformat(confirmation["date"]).isoformat() != confirmation["date"]:
            raise ValueError()
    except (ValueError, TypeError):
        raise StateError("confirmation requires an explicit ISO date")
    if not targets or not isinstance(targets, dict):
        raise StateError("affected roles with exact read-back checks required")
    for role, checks in targets.items():
        if role not in ROLES or not state["pages"].get(role, {}).get("verified"):
            raise StateError("affected Page must be mapped and verified")
        if not state["privacy"].get(role, {}).get("owner_only"):
            raise StateError("affected Page privacy unverified")
        if not isinstance(checks, list) or not checks or any(not isinstance(x, str) or not x for x in checks):
            raise StateError("nonempty exact checks required")
    state.setdefault("change_sets", {})[change_id] = {
        "targets": copy.deepcopy(targets), "confirmation": copy.deepcopy(confirmation),
        "verified_roles": {}, "status": "pending"}
    event(state, "begin_change_set", {"change_id": change_id, "roles": sorted(targets)})


def verify_change_role(state, change_id, role, read):
    change = state.get("change_sets", {}).get(change_id)
    if not change or role not in change["targets"]:
        raise StateError("role is not part of the declared change")
    try:
        content = read.get("content")
        if not content:
            raise StateError("read-back missing; change remains unconfirmed")
        text = "\n\n".join(b.get("markdown", "") for b in content.get("blocks", []))
        if any(check not in text for check in change["targets"][role]):
            raise StateError("affected projection is stale or incomplete; do not claim whole update complete")
        verify_page(state, role, read)
    except StateError:
        change["verified_roles"].pop(role, None)
        change["status"] = "pending"
        event(state, "invalidate_change_role", {"change_id": change_id, "role": role})
        raise
    change["verified_roles"][role] = digest(read)
    change["status"] = "verified" if set(change["targets"]) == set(change["verified_roles"]) else "pending"
    event(state, "verify_change_role", {"change_id": change_id, "role": role, "status": change["status"]})


def change_summary(state, change_id):
    change = state.get("change_sets", {}).get(change_id)
    if not change:
        raise StateError("unknown change")
    return {"status": change["status"], "verified_roles": sorted(change["verified_roles"]),
            "pending_roles": sorted(set(change["targets"]) - set(change["verified_roles"])),
            "confirmation": copy.deepcopy(change["confirmation"])}


def load_state(path, with_hash=False):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise StateError("state must be a regular non-linked file")
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != 1:
        raise StateError("unsupported state schema")
    return (data, hashlib.sha256(raw).hexdigest()) if with_hash else data


def save_state(path, state, expected_revision=None, expected_file_hash=None):
    """Atomic local save with a lock and optimistic revision guard; no blind overwrite."""
    path = Path(path)
    if not path.is_absolute():
        raise StateError("absolute private state path required")
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise StateError("state must be a regular non-linked file")
    if any(p.is_symlink() for p in path.parents):
        raise StateError("state directory must not traverse symbolic links")
    path = path.resolve()
    # Locate actual source/Skill markers below. A fixed parents[3] is only
    # correct in the repository layout; installed copies have a different depth
    # and must allow a private checkpoint beside their installation directory.
    for ancestor in [path.parent, *path.parent.parents]:
        if (ancestor / "skills" / "maintain-personal-context" / "SKILL.md").is_file():
            raise StateError("private journal must be outside every public source package copy")
        if ancestor.name == "maintain-personal-context" and (ancestor / "SKILL.md").is_file():
            raise StateError("private journal must be outside installed Skill")
    if not path.parent.exists() or path.parent.is_symlink():
        raise StateError("create an explicit private state directory first")
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise StateError("journal locked; reconcile interrupted writer before retry")
    tmp = None
    try:
        os.close(fd)
        if expected_file_hash is not None and (not path.is_file() or
                hashlib.sha256(path.read_bytes()).hexdigest() != expected_file_hash):
            raise StateError("journal bytes changed; preserve edits and reread before saving")
        if expected_revision is None:
            if path.exists():
                raise StateError("existing journal: load and preserve it")
        elif not path.exists() or load_state(path)["revision"] != expected_revision:
            raise StateError("journal changed; reread before saving")
        with tempfile.NamedTemporaryFile("w", dir=str(path.parent), encoding="utf-8", delete=False) as out:
            tmp = Path(out.name)
            os.chmod(str(tmp), 0o600)
            json.dump(state, out, ensure_ascii=False, indent=2)
            out.write("\n");out.flush();os.fsync(out.fileno())
        os.replace(str(tmp), str(path));tmp = None
    finally:
        if tmp is not None:
            tmp.unlink()
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, help="absolute file in a private directory outside public source")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    for flag in ("name", "as-of", "authorized-scope"):
        init.add_argument("--" + flag, required=True)
    sub.add_parser("show")
    args = parser.parse_args()
    try:
        if args.command == "init":
            state = new_state(args.name, args.as_of, args.authorized_scope)
            save_state(args.state, state)
        else:
            state = load_state(args.state)
        print(json.dumps(summary(state), ensure_ascii=False, indent=2))
        return 0
    except (StateError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
