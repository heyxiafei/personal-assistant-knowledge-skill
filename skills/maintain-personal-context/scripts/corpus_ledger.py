#!/usr/bin/env python3
"""Validate an already collected, Agent-annotated corpus; never access accounts.

API: build_ledger(bundle) -> a new dict. CLI: --input private.json --output new.json.
Input schema_version is 1. Required arrays (even when empty):

channels: {channel_id, status, reason, access}
sources:  {channel_id, host_id, kind, source_id, status, reason, access}
records:  {channel_id, host_id, kind, source_id, message_id, text, truncated,
           content_omitted, author_role, author_basis, author_reference,
           disposition, reason}

Every access object requires {tool, environment, method, query, scope, pagination,
truncated, attachments, limitations}. query is captured JSON or null. tool may
be null only for a channel/source not read. pagination requires {pages_read,
next_cursor, exhausted}; exhausted/truncated may be null when unknown. attachments
requires {status, reason}, with status read/partial/unread/none/unknown. limitations
is an array of explicit boundary descriptions. scope is the actual bounded scope,
not a claim about an account. Metadata may be retained in optional metadata objects
on channels, sources and records; it is not interpreted or executed.

Optional attachments inventory_complete (true/false/null) and items must occur
together. Each item requires {attachment_id, kind, status, reason, reader, reference,
extent: {unit, total}, read_ranges: [{start, end}], sampled, limitations}. Kinds
image/screenshot/pdf use page units (1-based, inclusive); audio/video use second
units (0-based, end exclusive); file/unknown use item units (a single whole item).
Unknown extent is null. Status is read/partial/unread/blocked/unknown. Read/partial
require actual reader and receipt reference; partial/unread/blocked/unknown require
limitations. Read requires known total, continuous full coverage and no sampling.
An unresolved limitation is partial even when its temporal/page range is complete.
The inventory summary must agree with the unique item receipts. Identical item
duplicates count once; conflicting identities fail. An incomplete inventory cannot
claim read/none. Legacy summaries remain accepted, with attachment_coverage.basis
set to legacy_summary; they do not establish per-attachment coverage. This script
validates annotations only: it never opens, decodes or interprets media.

Access statuses: read, partial, user_excluded, experiment_excluded, not_attempted,
blocked, unavailable. Non-read statuses need a reason. Source identity is
(host_id, kind, source_id), message identity adds message_id. host_id is explicitly
null when unknown; do not invent a host. Each source has one canonical channel;
conflicting duplicate identities must be reconciled upstream, never double-counted.

Record dispositions: retained/excluded/quarantined/unread (exactly one). Record
truncated/content_omitted accept true/false/null; null records missing evidence.
author_role
is user/assistant/third_party/unknown. Known roles require author_basis of
source_metadata or user_confirmation and a nonempty author_reference. Unknown role
requires unknown/null. Formatting is never author evidence. Unknown authors proposed
for retention are quarantined; an explicit exclusion retains its reason and unknown
author. Empty omitted/truncated text is always unread. Nonempty text with true/null
completeness flags may be retained but downgrades its source/channel to partial.
Readable child sources make an unavailable/blocked/not_attempted channel partial;
they cannot override a user_excluded/experiment_excluded channel boundary.

Optional reported_counts has the exact counts shape {total, retained, excluded,
quarantined, unread}; it is compared with, never used to generate, final counts.
All counts derive from final unique records, including per-source/channel counts.
The result preserves original access status and disposition plus adjustment codes.
It validates annotations, not their truth, completeness of discovery, semantic
extraction, permissions or authorship. Account coverage always remains unknown.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path


DISPOSITIONS = ("retained", "excluded", "quarantined", "unread")
ACCESS_STATUSES = ("read", "partial", "user_excluded", "experiment_excluded",
                   "not_attempted", "blocked", "unavailable")
ACTIVE_STATUSES = ("read", "partial")
SOURCE_KEY = ("host_id", "kind", "source_id")
RECORD_KEY = SOURCE_KEY + ("message_id",)
ATTACHMENT_KINDS = {"image": "page", "screenshot": "page", "pdf": "page",
                    "audio": "second", "video": "second", "file": "item", "unknown": "item"}
ATTACHMENT_STATUSES = ("read", "partial", "unread", "blocked", "unknown")


class LedgerError(ValueError):
    """An invalid or conflicting annotation; errors never include source contents."""


def _object(value, required, where, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys():
        raise LedgerError(f"{where}: missing required object fields")
    if value.keys() - set(required) - set(optional):
        raise LedgerError(f"{where}: unsupported fields; use metadata for extra annotations")


def _text(value, where, empty=False, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise LedgerError(f"{where}: expected {'nullable ' if nullable else ''}text")


def _choice(value, choices, where):
    if not isinstance(value, str) or value not in choices:
        raise LedgerError(f"{where}: invalid choice")


def _flag(value, where, nullable=False):
    if type(value) is not bool and not (nullable and value is None):
        raise LedgerError(f"{where}: expected boolean{' or null' if nullable else ''}")


def _number(value, where):
    if type(value) is not int or value < 0:
        raise LedgerError(f"{where}: expected nonnegative integer")


def _identity(row, fields):
    return tuple(row[k] for k in fields)


def _source_identity(row, where):
    _text(row["host_id"], where + ".host_id", nullable=True)
    for field in ("channel_id", "kind", "source_id"):
        _text(row[field], where + "." + field)


def _metadata(row, where):
    if "metadata" in row and not isinstance(row["metadata"], dict):
        raise LedgerError(f"{where}.metadata: expected object")


def _attachment(row, where):
    _object(row, ("attachment_id", "kind", "status", "reason", "reader", "reference",
                  "extent", "read_ranges", "sampled", "limitations"), where)
    _text(row["attachment_id"], where + ".attachment_id")
    _choice(row["kind"], ATTACHMENT_KINDS, where + ".kind")
    _choice(row["status"], ATTACHMENT_STATUSES, where + ".status")
    _text(row["reason"], where + ".reason")
    active = row["status"] in ACTIVE_STATUSES
    for field in ("reader", "reference"):
        _text(row[field], where + "." + field, nullable=not active)
    _flag(row["sampled"], where + ".sampled")
    if not isinstance(row["limitations"], list):
        raise LedgerError(f"{where}.limitations: expected array")
    for value in row["limitations"]:
        _text(value, where + ".limitations[]")
    if row["status"] != "read" and not row["limitations"]:
        raise LedgerError(f"{where}: incomplete attachment requires limitations")
    if row["status"] == "read" and row["limitations"]:
        raise LedgerError(f"{where}: read cannot retain unresolved limitations")
    extent = row["extent"]
    _object(extent, ("unit", "total"), where + ".extent")
    if extent["unit"] != ATTACHMENT_KINDS[row["kind"]]:
        raise LedgerError(f"{where}: extent unit does not match attachment kind")
    unit, total = extent["unit"], extent["total"]

    def bound(value, label, start=False):
        if unit == "second":
            if type(value) not in (int, float) or value < 0 or (not start and value == 0):
                raise LedgerError(f"{label}: expected {'nonnegative' if start else 'positive'} seconds")
        elif type(value) is not int or value < 1:
            raise LedgerError(f"{label}: expected positive integer")

    if total is not None:
        bound(total, where + ".extent.total")
        if unit == "item" and total != 1:
            raise LedgerError(f"{where}: whole-item extent must be one or unknown")
    if not isinstance(row["read_ranges"], list):
        raise LedgerError(f"{where}.read_ranges: expected array")
    for part in row["read_ranges"]:
        _object(part, ("start", "end"), where + ".read_ranges[]")
        bound(part["start"], where + ".read_ranges[].start", start=True)
        bound(part["end"], where + ".read_ranges[].end")
        if part["end"] < part["start"] or unit == "second" and part["end"] == part["start"]:
            raise LedgerError(f"{where}: empty or reversed read range")
        if total is not None and part["end"] > total:
            raise LedgerError(f"{where}: read range exceeds known extent")
        if unit == "item" and (part["start"], part["end"]) != (1, 1):
            raise LedgerError(f"{where}: whole-item read range must be one item")
    ranges = sorted(row["read_ranges"], key=lambda part: part["start"])
    for previous, current in zip(ranges, ranges[1:]):
        if current["start"] < previous["end"] or unit != "second" and current["start"] == previous["end"]:
            raise LedgerError(f"{where}: overlapping read ranges")
    if active != bool(ranges):
        raise LedgerError(f"{where}: read/partial requires ranges; unread/blocked/unknown cannot claim ranges")
    if not active and row["sampled"]:
        raise LedgerError(f"{where}: sampling requires a read range")
    if row["kind"] == "unknown" and active:
        raise LedgerError(f"{where}: unknown attachment kind cannot claim a reading")
    if row["status"] == "read":
        origin, step = (0, 0) if unit == "second" else (1, 1)
        complete = total is not None and ranges[0]["start"] == origin and ranges[-1]["end"] == total
        complete = complete and all(current["start"] == previous["end"] + step
                                    for previous, current in zip(ranges, ranges[1:]))
        if row["sampled"] or not complete:
            raise LedgerError(f"{where}: read requires known full coverage without sampling")


def _attachment_receipt(attachment, where):
    _object(attachment, ("status", "reason"), where, ("inventory_complete", "items"))
    _choice(attachment["status"], ("read", "partial", "unread", "none", "unknown"), where + ".status")
    _text(attachment["reason"], where + ".reason")
    if ("items" in attachment) != ("inventory_complete" in attachment):
        raise LedgerError(f"{where}: items and inventory_complete must occur together")
    if "items" not in attachment:
        return None, {"basis": "legacy_summary", "inventory_complete": None,
                      "unique_items": None, "duplicates_removed": 0, "status": attachment["status"]}
    _flag(attachment["inventory_complete"], where + ".inventory_complete", nullable=True)
    if not isinstance(attachment["items"], list):
        raise LedgerError(f"{where}.items: expected array")
    items, duplicates = _unique(attachment["items"], ("attachment_id",), _attachment, where + ".items")
    rows = list(items.values())
    if any(row["status"] in ACTIVE_STATUSES for row in rows):
        status = "read" if attachment["inventory_complete"] is True and all(row["status"] == "read" for row in rows) else "partial"
    elif attachment["inventory_complete"] is not True or any(row["status"] == "unknown" for row in rows):
        status = "unknown"
    else:
        status = "unread" if rows else "none"
    if attachment["status"] != status:
        raise LedgerError(f"{where}: summary conflicts with attachment receipts")
    return rows, {"basis": "per_attachment", "inventory_complete": attachment["inventory_complete"],
                  "unique_items": len(rows), "duplicates_removed": duplicates, "status": status}


def _access(row, where):
    _choice(row["status"], ACCESS_STATUSES, where + ".status")
    _text(row["reason"], where + ".reason", empty=row["status"] == "read")
    access = row["access"]
    _object(access, ("tool", "environment", "method", "query", "scope", "pagination",
                     "truncated", "attachments", "limitations"), where + ".access")
    _text(access["tool"], where + ".tool", nullable=row["status"] not in ACTIVE_STATUSES)
    for field in ("environment", "method", "scope"):
        _text(access[field], where + "." + field)
    _flag(access["truncated"], where + ".truncated", nullable=True)
    if not isinstance(access["limitations"], list):
        raise LedgerError(f"{where}.limitations: expected array")
    for value in access["limitations"]:
        _text(value, where + ".limitations[]")
    page = access["pagination"]
    _object(page, ("pages_read", "next_cursor", "exhausted"), where + ".pagination")
    _number(page["pages_read"], where + ".pages_read")
    _text(page["next_cursor"], where + ".next_cursor", nullable=True)
    _flag(page["exhausted"], where + ".exhausted", nullable=True)
    if page["exhausted"] is True and (page["next_cursor"] is not None or page["pages_read"] == 0):
        raise LedgerError(f"{where}: contradictory pagination metadata")
    if row["status"] in ACTIVE_STATUSES and page["pages_read"] == 0:
        raise LedgerError(f"{where}: read/partial requires a captured page")
    _attachment_receipt(access["attachments"], where + ".attachments")
    _metadata(row, where)


def _counts(records):
    counts = dict.fromkeys(DISPOSITIONS, 0)
    for record in records:
        counts[record["disposition"]] += 1
    return {"total": sum(counts.values()), **counts}


def _unique(rows, fields, validator, label):
    unique, duplicate_count = {}, 0
    for index, row in enumerate(rows):
        where = f"{label}[{index}]"
        validator(row, where)
        key = _identity(row, fields)
        if key in unique:
            # Compare before normalization: differing supplied annotations remain
            # conflicts even when a safety adjustment would produce the same state.
            if json.dumps(unique[key], sort_keys=True) != json.dumps(row, sort_keys=True):
                raise LedgerError(f"{where}: conflicting duplicate identity")
            duplicate_count += 1
        else:
            unique[key] = row
    return unique, duplicate_count


def _channel(row, where):
    _object(row, ("channel_id", "status", "reason", "access"), where, ("metadata",))
    _text(row["channel_id"], where + ".channel_id")
    _access(row, where)


def _source(row, where):
    _object(row, ("channel_id", *SOURCE_KEY, "status", "reason", "access"), where, ("metadata",))
    _source_identity(row, where)
    _access(row, where)


def _record(row, where):
    _object(row, ("channel_id", *RECORD_KEY, "text", "truncated", "content_omitted",
                  "author_role", "author_basis", "author_reference", "disposition", "reason"),
            where, ("metadata",))
    _source_identity(row, where)
    _text(row["message_id"], where + ".message_id")
    _text(row["text"], where + ".text", empty=True)
    for flag in ("truncated", "content_omitted"):
        _flag(row[flag], where + "." + flag, nullable=True)
    _choice(row["disposition"], DISPOSITIONS, where + ".disposition")
    _text(row["reason"], where + ".reason")
    _choice(row["author_role"], ("user", "assistant", "third_party", "unknown"), where + ".author_role")
    _choice(row["author_basis"], ("source_metadata", "user_confirmation", "unknown"), where + ".author_basis")
    if row["author_role"] == "unknown":
        if row["author_basis"] != "unknown" or row["author_reference"] is not None:
            raise LedgerError(f"{where}: unknown author must retain unknown/null evidence")
    elif row["author_basis"] == "unknown":
        raise LedgerError(f"{where}: known author requires evidence")
    else:
        _text(row["author_reference"], where + ".author_reference")
    _metadata(row, where)


def _normalize_record(row):
    row["reported_disposition"] = row["disposition"]
    row["adjustments"] = []
    if not row["text"].strip() and (row["truncated"] is True or row["content_omitted"] is True):
        row["disposition"] = "unread"
        row["adjustments"].append("empty_incomplete_text_is_unread")
    elif row["author_role"] == "unknown" and row["disposition"] == "retained":
        row["disposition"] = "quarantined"
        row["adjustments"].append("unknown_author_requires_review")
    if row["disposition"] == "retained" and not row["text"].strip():
        raise LedgerError("record: retained text cannot be empty")
    return row


def _access_result(row, records, child_statuses=()):
    row["reported_status"] = row["status"]
    gaps = []
    access = row["access"]
    items, row["attachment_coverage"] = _attachment_receipt(access["attachments"], "access.attachments")
    if items is not None:
        access["attachments"]["items"] = items
    if access["pagination"]["exhausted"] is not True:
        gaps.append("pagination_not_proven_exhausted")
    if access["truncated"] is not False:
        gaps.append("access_truncation_or_unknown")
    if access["attachments"]["status"] not in ("read", "none"):
        gaps.append("attachments_not_fully_read")
    if any(r["truncated"] is not False or r["content_omitted"] is not False for r in records):
        gaps.append("record_text_incomplete_or_unknown")
    if any(r["disposition"] == "unread" for r in records):
        gaps.append("record_unread")
    if any(status != "read" for status in child_statuses):
        gaps.append("source_not_read")
    if row["status"] in ("unavailable", "blocked", "not_attempted") \
            and any(status in ACTIVE_STATUSES for status in child_statuses):
        gaps.append("readable_source_despite_channel_status")
        row["status"] = "partial"
    row["coverage_gaps"] = gaps
    if row["status"] == "read" and gaps:
        row["status"] = "partial"
    row["counts"] = _counts(records)
    return row


def build_ledger(bundle):
    """Return final unique records and independently recomputed count partitions."""
    # Reject non-JSON API inputs and copy before any adjustments; no input mutation.
    try:
        json.dumps(bundle, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise LedgerError("bundle must contain finite JSON values") from exc
    data = copy.deepcopy(bundle)
    _object(data, ("schema_version", "channels", "sources", "records"), "bundle", ("reported_counts",))
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise LedgerError("unsupported schema_version")
    for field in ("channels", "sources", "records"):
        if not isinstance(data[field], list):
            raise LedgerError(f"{field}: expected array")
    channels, channel_dupes = _unique(data["channels"], ("channel_id",), _channel, "channels")
    sources, source_dupes = _unique(data["sources"], SOURCE_KEY, _source, "sources")
    records, record_dupes = _unique(data["records"], RECORD_KEY, _record, "records")
    for source in sources.values():
        if (source["channel_id"],) not in channels:
            raise LedgerError("source: channel reference does not exist")
    for row in records.values():
        source = sources.get(_identity(row, SOURCE_KEY))
        if source is None or source["channel_id"] != row["channel_id"]:
            raise LedgerError("record: source/channel reference does not match")
        _normalize_record(row)
        channel = channels[(row["channel_id"],)]
        if (source["status"] not in ACTIVE_STATUSES
                or channel["status"] in ("user_excluded", "experiment_excluded")) \
                and row["disposition"] in ("retained", "quarantined"):
            raise LedgerError("record: readable content conflicts with source status or explicit channel exclusion")
    final_records = list(records.values())
    for key, source in sources.items():
        _access_result(source, [r for r in final_records if _identity(r, SOURCE_KEY) == key])
    for (channel_id,), channel in channels.items():
        _access_result(channel, [r for r in final_records if r["channel_id"] == channel_id],
                       [s["status"] for s in sources.values() if s["channel_id"] == channel_id])
    counts = _counts(final_records)
    reconciliation = None
    if "reported_counts" in data:
        reported = data["reported_counts"]
        _object(reported, ("total", *DISPOSITIONS), "reported_counts")
        for field, value in reported.items():
            _number(value, "reported_counts." + field)
        reconciliation = {"reported": reported, "matches_final": reported == counts,
                          "delta_final_minus_reported": {k: counts[k] - reported[k] for k in counts}}
    return {"schema_version": 1, "records": final_records,
            "sources": list(sources.values()), "channels": list(channels.values()),
            "counts": counts, "reported_counts_comparison": reconciliation,
            "duplicates_removed": {"channels": channel_dupes, "sources": source_dupes, "records": record_dupes},
            "coverage": {"account_total": "unknown", "account_enumeration_complete": "unknown",
                         "basis": "supplied_agent_annotations_only",
                         "channels_by_status": {s: sum(c["status"] == s for c in channels.values()) for s in ACCESS_STATUSES}}}


def _private_output(path):
    out = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise LedgerError("symlink output or ancestor is not supported")
    if out.is_relative_to(Path(__file__).resolve().parents[1]):
        raise LedgerError("output must be outside the installed Skill")
    for ancestor in (out.parent, *out.parent.parents):
        if (ancestor / "skills/maintain-personal-context/SKILL.md").is_file() \
                or ancestor.name == "maintain-personal-context" and (ancestor / "SKILL.md").is_file():
            raise LedgerError("private output must be outside public source copies and installed Skills")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="already collected private JSON annotations")
    parser.add_argument("--output", required=True, help="new private ledger file; no overwrite")
    args = parser.parse_args()
    result = build_ledger(json.loads(Path(args.input).read_text(encoding="utf-8")))
    out = _private_output(args.output)
    encoded = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded)
    print(json.dumps({"counts": result["counts"], "sha256": hashlib.sha256(encoded).hexdigest(),
                      "reported_counts_match": None if result["reported_counts_comparison"] is None
                      else result["reported_counts_comparison"]["matches_final"]}))


if __name__ == "__main__":
    try:
        main()
    except (LedgerError, ValueError, OSError) as exc:
        raise SystemExit("corpus_ledger: " + str(exc))
