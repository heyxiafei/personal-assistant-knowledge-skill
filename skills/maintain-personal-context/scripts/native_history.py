#!/usr/bin/env python3
"""Normalize authorized native history receipts. No network, discovery or API calls."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


class HistoryError(ValueError):
    pass


def payload(value):
    """Accept the actual native JSON or its MCP text/structured wrapper."""
    if not isinstance(value, dict) or value.get("isError"):
        raise HistoryError("history tool failed or did not return an object")
    if "thread" in value or "threads" in value:
        return value
    structured = value.get("structuredContent")
    if isinstance(structured, dict):
        return payload(structured)
    blocks = value.get("content", [])
    if isinstance(blocks, list) and len(blocks) == 1 and isinstance(blocks[0], dict) \
            and blocks[0].get("type") == "text":
        try:
            return payload(json.loads(blocks[0]["text"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise HistoryError("history text wrapper is not native JSON") from exc
    raise HistoryError("unsupported history receipt; inspect the active schema")


def discovery(value, tool="list_threads", requested_cursor=None, caller_host=None):
    """Deduplicate visible source identities without claiming an account total."""
    data = payload(value)
    if tool not in ("list_threads", "list_archived_threads"):
        raise HistoryError("unsupported discovery tool")
    rows = data.get("threads")
    pinned = data.get("pinnedThreads", []) if tool == "list_threads" else []
    if not isinstance(rows, list) or not isinstance(pinned, list):
        raise HistoryError("thread list is missing")
    found, other = {}, {}
    for row in pinned + rows:
        if not isinstance(row, dict):
            raise HistoryError("invalid discovery row")
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise HistoryError("discovery row lacks a thread id")
        kind = row.get("kind", "codex")
        if not isinstance(kind, str) or not kind:
            raise HistoryError("invalid returned kind")
        host = row.get("hostId")
        if host is None and tool == "list_archived_threads" and kind == "codex":
            host = caller_host  # Proven caller scope only; never assume 'local'.
        if host is not None and (not isinstance(host, str) or not host):
            raise HistoryError("invalid returned host")
        key = (kind, host, row["id"])
        target = found if kind == "codex" else other
        target[key] = {"kind": kind, "host_id": host, "thread_id": row["id"],
                       "title": row.get("title"), "status": "discovered_unread"}
    next_cursor = data.get("nextCursor")
    # list_threads currently has no pagination contract. Archive EOF applies only
    # to this caller/tool scope, and only this page was inspected by this helper.
    if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
        raise HistoryError("invalid archive cursor")
    terminal = tool == "list_archived_threads" and "nextCursor" in data \
        and next_cursor is None and data.get("partial_results") is not True \
        and data.get("hasMore") is not True
    return {"sources": list(found.values()), "returned_unique": len(found),
            "other_sources": list(other.values()), "other_returned_unique": len(other),
            "all_returned_unique": len(found) + len(other),
            "account_enumeration_complete": "unknown", "account_total": "unknown",
            "page_terminal": terminal, "requested_cursor": requested_cursor,
            "next_cursor": next_cursor,
            "scope": "bounded_recent_and_pinned" if tool == "list_threads" else "caller_host_archive_page"}


def _limited(value):
    return isinstance(value, dict) and any(value.get(k) is True for k in
        ("truncated", "isTruncated", "partial", "partial_results", "incomplete", "content_omitted"))


def _turn_date(turn):
    stamp = turn.get("startedAt")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        try:
            return datetime.fromtimestamp(stamp, timezone.utc).isoformat(), "turn.startedAt"
        except (OverflowError, OSError, ValueError):
            pass
    return None, "unknown"


def normalize_reads(reads, thread_id, host_id):
    """Validate an exact cursor chain and return user/assistant text as source data.

    EOF describes this native tool's returned text-turn range. It is never an
    account enumeration or proof that images, compactions and tool output were read.
    """
    if not all(isinstance(v, str) and v for v in (thread_id, host_id)):
        raise HistoryError("actual thread and host identities are required")
    if not isinstance(reads, list) or not reads:
        raise HistoryError("at least one captured read is required")
    messages, identities, turns, seen_cursors = [], {}, set(), set()
    reasons, next_cursor, terminal, paging_known = set(), None, False, True
    duplicates = 0
    for index, read in enumerate(reads):
        if not isinstance(read, dict) or "request_cursor" not in read:
            raise HistoryError("record the actual requested cursor, including initial null")
        requested = read["request_cursor"]
        if index == 0 and requested is not None:
            reasons.add("read_started_mid_thread")
        if index and (terminal or next_cursor is None or requested != next_cursor):
            raise HistoryError("cursor chain skipped, repeated or continued after EOF")
        if requested is not None:
            if not isinstance(requested, str) or not requested or requested in seen_cursors:
                raise HistoryError("invalid or repeated requested cursor")
            seen_cursors.add(requested)
        data = payload(read.get("payload"))
        thread = data.get("thread", {})
        if not isinstance(thread, dict):
            raise HistoryError("thread identity metadata is missing")
        if thread.get("id") != thread_id or thread.get("hostId") != host_id \
                or thread.get("kind", "codex") != "codex":
            raise HistoryError("read returned a different or unknown thread/host")
        page = data.get("page", {})
        if not isinstance(page, dict):
            raise HistoryError("invalid page metadata")
        more = page.get("hasMore")
        next_cursor = page.get("nextCursor")
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
            raise HistoryError("invalid returned cursor")
        if more is False and next_cursor is not None or more is True and next_cursor is None:
            raise HistoryError("contradictory pagination metadata")
        if next_cursor in seen_cursors:
            raise HistoryError("returned continuation loops to an earlier cursor")
        if type(more) is not bool or "nextCursor" not in page:
            paging_known = False
            reasons.add("pagination_unknown")
        terminal = more is False and "nextCursor" in page and next_cursor is None
        if _limited(data) or _limited(page):
            reasons.add("text_truncated_or_partial")
        rows = data.get("turns")
        if not isinstance(rows, list):
            raise HistoryError("turn list is missing")
        for turn in rows:
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not turn["id"]:
                raise HistoryError("turn identity missing")
            turns.add(turn["id"])
            recorded, date_basis = _turn_date(turn)
            if _limited(turn):
                reasons.add("text_truncated_or_partial")
            items = turn.get("items")
            if not isinstance(items, list):
                reasons.add("summary_only_turn")
                continue
            for item in items:
                if not isinstance(item, dict):
                    raise HistoryError("invalid native item")
                if item.get("type") == "contextCompaction":
                    reasons.add("compaction_observed")
                if item.get("type") not in ("userMessage", "agentMessage"):
                    continue  # Historical tools, commands and reasoning are never executed.
                if _limited(item):
                    reasons.add("text_truncated_or_partial")
                message_limited = _limited(item) or _limited(turn) or _limited(data) or _limited(page)
                attachments = False
                role = "user" if item["type"] == "userMessage" else "assistant"
                if role == "user":
                    content = item.get("content")
                    if not isinstance(content, list):
                        reasons.add("message_text_unknown")
                        message_limited = True
                        content = []
                    text_parts = []
                    for block in content:
                        if not isinstance(block, dict):
                            raise HistoryError("invalid user content block")
                        if _limited(block):
                            reasons.add("text_truncated_or_partial")
                            message_limited = True
                        if block.get("type") == "text" and isinstance(block.get("text"), str):
                            text_parts.append(block["text"])
                        elif block.get("type") == "text":
                            reasons.add("message_text_unknown")
                            message_limited = True
                        else:
                            reasons.add("attachments_not_read")
                            attachments = True
                    text = "\n".join(text_parts)
                else:
                    text = item.get("text")
                    if not isinstance(text, str):
                        reasons.add("message_text_unknown")
                        message_limited = True
                        text = ""
                if not isinstance(item.get("id"), str) or not item["id"]:
                    raise HistoryError("message identity missing; do not invent it")
                message = {"kind": "codex", "host_id": host_id, "thread_id": thread_id,
                    "turn_id": turn["id"], "message_id": item["id"], "author_role": role,
                    "text": text, "phase": item.get("phase"), "recorded_at_utc": recorded,
                    "date_basis": date_basis, "exact_message_timestamp": None,
                    "confirmation_date": None, "event_date": None}
                # Keep per-record limits through later filtering/counting. An
                # empty truncated body is unread, never evidence of no content.
                message["read_status"] = ("unread" if not text.strip() and message_limited
                                          else "partial" if message_limited else "text_returned")
                message["truncation_observed"] = message_limited
                message["attachments_not_read"] = attachments
                old = identities.get(item["id"])
                if old is not None:
                    # Different page-level limits on an overlap do not change
                    # its identity/content. Conservatively retain either limit.
                    limits = {"read_status", "truncation_observed", "attachments_not_read"}
                    if {k: v for k, v in old.items() if k not in limits} != \
                            {k: v for k, v in message.items() if k not in limits}:
                        raise HistoryError("same message id has conflicting content; retain both receipts for review")
                    old["truncation_observed"] |= message["truncation_observed"]
                    old["attachments_not_read"] |= message["attachments_not_read"]
                    if old["truncation_observed"]:
                        old["read_status"] = "partial" if text.strip() else "unread"
                    duplicates += 1
                    continue
                identities[item["id"]] = message
                messages.append(message)
    blocking = {"text_truncated_or_partial", "summary_only_turn", "message_text_unknown", "read_started_mid_thread"}
    complete = False if reasons & blocking else "unknown" if not paging_known else terminal
    return {"source": {"kind": "codex", "host_id": host_id, "thread_id": thread_id},
            "messages": messages, "unique_turns": len(turns), "duplicate_messages": duplicates,
            "coverage": {"thread_text_range_complete": complete, "next_cursor": next_cursor,
                "account_enumeration_complete": "unknown", "source_status": "partial",
                "granularity": "native_text_messages", "source_content_complete": "not_assessed",
                "limitations": sorted(reasons | {"text_only_scope"})}}


def reconcile_claims(claims, required_keys=()):
    """Group Agent-annotated canonical claims; no NLP, source reads or page writes.

    A key/value is a reviewed semantic annotation, not a raw-text similarity guess.
    Memory sources remain summaries. Corrections require direct user evidence and
    known dates; later or undated contradictory saved facts remain conflicts.
    """
    def value_key(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)

    def stamp(row):
        text = row.get("recorded_at")
        if not isinstance(text, str):
            return None
        try:
            date = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return date.astimezone(timezone.utc) if date.tzinfo else None
        except ValueError:
            return None

    def authority(row):
        return row["author_role"] == "user" and row["source_kind"] != "memory" \
            and row["evidence_tier"] in ("direct", "saved")

    def unverified_material(row):
        return row["source_kind"] == "saved_material" and row["author_role"] == "unknown"

    grouped, identities = {}, {}
    for original in claims:
        if not isinstance(original, dict):
            raise HistoryError("claim must be an annotated object")
        row = json.loads(json.dumps(original, ensure_ascii=False, allow_nan=False))
        if not all(isinstance(row.get(k), str) and row[k] for k in ("key", "source_id")) \
                or "value" not in row or row.get("source_kind") not in ("conversation", "memory", "saved_material") \
                or row.get("author_role") not in ("user", "assistant", "third_party", "unknown") \
                or row.get("evidence_tier") not in ("direct", "saved", "summary"):
            raise HistoryError("claim identity, source kind, role and evidence tier required")
        if row["source_kind"] == "memory" and row["evidence_tier"] != "summary":
            raise HistoryError("memory must remain derived summary evidence")
        if "correction_of" in row and not (authority(row) and row["evidence_tier"] == "direct"):
            raise HistoryError("only direct user evidence can assert a correction")
        identity = (row["source_kind"], row["source_id"], row["key"])
        if identity in identities:
            if identities[identity] != row:
                raise HistoryError("same claim source identity changed; review both receipts")
            continue
        identities[identity] = row
        buckets = grouped.setdefault(row["key"], {})
        buckets.setdefault(value_key(row["value"]), {"value": row["value"], "sources": []})["sources"].append(row)
    facts, conflicts = [], []
    for key, buckets in grouped.items():
        active = {v for v, b in buckets.items() if any(authority(s) for s in b["sources"])}
        if not active:
            active = {v for v, b in buckets.items() if any(s["source_kind"] == "memory" \
                or s["source_kind"] == "saved_material" and s["author_role"] == "unknown" \
                for s in b["sources"])}
        corrections = [s for b in buckets.values() for s in b["sources"] if "correction_of" in s]
        corrections.sort(key=lambda s: stamp(s) or datetime.min.replace(tzinfo=timezone.utc))
        for correction in corrections:
            old = value_key(correction["correction_of"])
            new = value_key(correction["value"])
            when = stamp(correction)
            if old == new or old not in buckets or when is None:
                continue
            competitors = [s for s in buckets[old]["sources"] if authority(s) or unverified_material(s)]
            if any(stamp(s) is None or stamp(s) >= when for s in competitors):
                continue
            buckets[old]["superseded_by"] = correction["source_id"]
            active.discard(old)
            active.add(new)
        # Unknown authors never gain authority. Their later/undated conflicting
        # material still needs review; it cannot silently disappear behind a fact.
        dates = [stamp(s) for v in active for s in buckets[v]["sources"] if authority(s)]
        if dates:
            latest = max(dates) if all(d is not None for d in dates) else None
            for v, bucket in buckets.items():
                if v in active or "superseded_by" in bucket:
                    continue
                if any(unverified_material(s) and (latest is None or stamp(s) is None
                       or stamp(s) >= latest) for s in bucket["sources"]):
                    active.add(v)
        chosen = next(iter(active)) if len(active) == 1 else None
        has_authority = chosen is not None and any(authority(s) for s in buckets[chosen]["sources"])
        status = "unconfirmed" if not active else "conflict" if chosen is None else "known" if has_authority else "summary_only"
        if status == "conflict":
            conflicts.append(key)
        facts.append({"key": key, "status": status,
            "current_value": buckets[chosen]["value"] if has_authority else None,
            "summary_candidate": buckets[chosen]["value"] if chosen is not None and not has_authority else None,
            "values": list(buckets.values()), "independent_evidence_by_source_count": False,
            "save_confirmation_date": None})
    unconfirmed = {f["key"] for f in facts if f["status"] == "unconfirmed"}
    missing = list(dict.fromkeys(k for k in required_keys if k not in grouped or k in unconfirmed))
    return {"facts": facts, "missing_keys": missing, "conflict_keys": conflicts,
            "question_keys": list(dict.fromkeys(missing + conflicts))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="private JSON bundle of actual captured reads")
    parser.add_argument("--output", required=True, help="new private file; existing files are preserved")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text())
    result = normalize_reads(data["reads"], data["thread_id"], data["host_id"])
    out = Path(args.output).expanduser().absolute()
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise HistoryError("symlink output or ancestor is not supported")
    if out.is_relative_to(Path(__file__).resolve().parents[1]):
        raise HistoryError("output must be outside the installed Skill")
    for ancestor in (out.parent, *out.parent.parents):
        if (ancestor / "skills/maintain-personal-context/SKILL.md").is_file() \
                or ancestor.name == "maintain-personal-context" and (ancestor / "SKILL.md").is_file():
            raise HistoryError("private output must be outside public source copies and installed Skills")
    encoded = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode()
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded)
    print(json.dumps({"messages": len(result["messages"]), "sha256": hashlib.sha256(encoded).hexdigest(),
                      "coverage": result["coverage"]}))


if __name__ == "__main__":
    try:
        main()
    except (HistoryError, ValueError, KeyError, OSError) as exc:
        raise SystemExit("native_history: " + str(exc))
