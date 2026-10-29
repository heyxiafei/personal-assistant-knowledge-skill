#!/usr/bin/env python3
"""Offline import of an explicitly supplied ChatGPT export; stdlib, no discovery.

Official help documents a ZIP and possibly numbered conversation JSON files,
but no stable naming/count contract. This adapter streams JSON arrays with
``mapping`` nodes from an explicit JSON, ZIP or export directory. All identified
files are enumerated; an optional explicit manifest checks declared members,
ordinals and hashes. Neither contiguous names nor a declared manifest prove
the service supplied the final shard. Every branch and original file is retained.
Unknown record shapes fail closed. No account login,
export request, network request, memory API or semantic attachment reading occurs.

File reconciliation proves this supplied file was imported, never that the
service exported all retained chats or all ChatGPT personal memory. Real export
acceptance remains pending until a user explicitly supplies and accepts one.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import re
import sys
import tempfile
import zipfile

SCHEMA_VERSION = 2
MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100000


class ExportError(ValueError):
    """Sanitized error code: do not include private text in exceptions/logs."""

    def __init__(self, code, details=None):
        super().__init__(code)
        self.details = details  # Source names stay private; CLI prints only code.


def encode(value):
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ExportError("unserializable_json_value") from exc


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExportError("duplicate_json_key")
        result[key] = value
    return result


def decode(data):
    def finite_float(raw):
        number = float(raw)
        if not math.isfinite(number):
            raise ExportError("nonfinite_json")
        return number
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs,
                          parse_float=finite_float,
                          parse_constant=lambda _: (_ for _ in ()).throw(ExportError("nonfinite_json")))
    except ExportError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ExportError("invalid_json") from exc


def _absolute_no_symlink(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ExportError("absolute_path_without_parent_traversal_required")
    # Do not resolve first: doing so would hide the symlink we must reject.
    for part in (path,) + tuple(path.parents):
        if part.is_symlink():
            raise ExportError("symlink_path_refused")
    return path


def _outside_code(path):
    own_skill = Path(__file__).resolve().parents[1]
    if path == own_skill or own_skill in path.parents:
        raise ExportError("private_output_inside_skill_refused")
    for parent in (path,) + tuple(path.parents):
        if (parent / ".git").exists() or (parent / ".hg").exists():
            raise ExportError("private_output_inside_repository_refused")
        if (parent / "SKILL.md").exists():
            raise ExportError("private_output_inside_skill_refused")
        if (parent / "skills/maintain-personal-context/SKILL.md").exists():
            raise ExportError("private_output_inside_source_package_refused")
    parts = path.parts
    if any(parts[i:i + 2] in ((".agents", "skills"), (".codex", "skills"))
           for i in range(len(parts) - 1)):
        raise ExportError("private_output_inside_skill_directory_refused")


def _private_output(value, resume):
    path = _absolute_no_symlink(value)
    _outside_code(path)
    if path.exists():
        if not resume:
            raise ExportError("output_already_exists_use_explicit_resume")
        if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ExportError("resume_requires_owner_only_directory")
    else:
        if resume:
            raise ExportError("resume_output_missing")
        if not path.parent.is_dir():
            raise ExportError("private_parent_directory_must_exist")
    return path


def _write(path, data):
    """Atomic private write; no symlink following and no permissive temp files."""
    _absolute_no_symlink(path)
    if path.exists() and not path.is_file():
        raise ExportError("output_file_type_changed")
    if path.exists() and path.stat().st_nlink != 1:
        raise ExportError("hardlinked_private_file_refused")
    fd, temporary = tempfile.mkstemp(prefix=".import-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_private(path):
    _absolute_no_symlink(path)
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ExportError("private_checkpoint_permissions_or_type_changed")
    if path.stat().st_nlink != 1:
        raise ExportError("hardlinked_private_file_refused")
    return path.read_bytes()


def _file_sha(path):
    _absolute_no_symlink(path)
    if not path.is_file():
        raise ExportError("source_must_be_regular_file")
    if path.stat().st_nlink != 1:
        raise ExportError("hardlinked_source_file_refused")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def iter_json_array(stream, *, max_record_bytes=MAX_RECORD_BYTES, chunk_size=64 * 1024, max_depth=256):
    """Yield objects from a binary top-level array with bounded reads and RAM.

    Strings/structural spans are scanned by the regex engine, not a Python loop
    over every payload byte. A complete record is decoded exactly once. Chunk
    boundaries may occur inside UTF-8, escapes, BOMs or nested structures.
    """
    for value, code in ((max_record_bytes, "positive_record_limit_required"),
                        (chunk_size, "positive_chunk_size_required"), (max_depth, "positive_depth_limit_required")):
        if type(value) is not int or value < 1:
            raise ExportError(code)
    state, depth, quoted, escaped = "start", 0, False, False
    record, prefix = bytearray(), bytearray()
    prefix_done = False
    string_token = re.compile(rb'["\\]')
    structure_token = re.compile(rb'["{}\[\]]')
    whitespace = re.compile(rb'[ \t\r\n]*')

    def append(segment):
        if len(record) + len(segment) > max_record_bytes:
            raise ExportError("record_size_limit_exceeded")
        record.extend(segment)

    while True:
        chunk = stream.read(chunk_size)
        if not isinstance(chunk, bytes):
            raise ExportError("binary_stream_required")
        end_of_stream = not chunk
        if not prefix_done:
            prefix.extend(chunk)
            if len(prefix) < 3 and chunk:
                continue
            chunk = bytes(prefix)
            if chunk.startswith(b"\xef\xbb\xbf"):
                chunk = chunk[3:]
            prefix_done = True
        if not chunk:
            if end_of_stream:
                break
            continue
        position = 0
        while position < len(chunk):
            if state == "record":
                if escaped:
                    append(chunk[position:position + 1])
                    position += 1
                    escaped = False
                    continue
                token = (string_token if quoted else structure_token).search(chunk, position)
                if token is None:
                    append(chunk[position:])
                    break
                endpoint = token.end()
                append(chunk[position:endpoint])
                byte = chunk[endpoint - 1]
                position = endpoint
                if quoted:
                    if byte == 92:
                        escaped = True
                    else:
                        quoted = False
                elif byte == 34:
                    quoted = True
                elif byte in (123, 91):
                    depth += 1
                    if depth > max_depth:
                        raise ExportError("record_nesting_limit_exceeded")
                else:
                    depth -= 1
                    if depth == 0:
                        value = decode(bytes(record))
                        if not isinstance(value, dict):
                            raise ExportError("unsupported_conversation_shape")
                        record.clear()
                        state = "separator"
                        yield value
                continue
            position = whitespace.match(chunk, position).end()
            if position == len(chunk):
                break
            byte = chunk[position]
            position += 1
            if state == "start":
                if byte != 91:
                    raise ExportError("unsupported_export_top_level")
                state = "first"
            elif state in ("first", "next"):
                if byte == 93 and state == "first":
                    state = "end"
                elif byte == 123:
                    append(b"{")
                    depth, quoted, escaped = 1, False, False
                    state = "record"
                else:
                    raise ExportError("invalid_array_record_or_trailing_comma")
            elif state == "separator":
                if byte == 44:
                    state = "next"
                elif byte == 93:
                    state = "end"
                else:
                    raise ExportError("invalid_array_separator")
            else:
                raise ExportError("data_after_json_array")
    if state != "end":
        raise ExportError("incomplete_json_array")

def _valid_member(name):
    if (not isinstance(name, str) or not name or "\\" in name or "\x00" in name
            or PurePosixPath(name).is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ExportError("unsafe_source_member")
    return name


def _conversation_filename(name):
    path = PurePosixPath(name)
    return path.suffix.lower() == ".json" and "conversation" in path.stem.lower()


def _stream_sha(stream, max_bytes=None):
    result = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(1024 * 1024 if max_bytes is None else min(1024 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        result.update(chunk)
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise ExportError("total_source_bytes_limit_exceeded")
    return result.hexdigest(), total


class SourceCatalog:
    """Enumerate only the explicit container; no account or external discovery."""

    def __init__(self, source, *, source_manifest=None, max_total_bytes=None):
        self.path = _absolute_no_symlink(source)
        _outside_code(self.path)
        if max_total_bytes is not None and (type(max_total_bytes) is not int or max_total_bytes < 1):
            raise ExportError("positive_total_limit_required")
        self.kind = "directory" if self.path.is_dir() else "zip" if zipfile.is_zipfile(self.path) else "json"
        self.entries = {}
        if self.kind == "directory":
            for root, directories, filenames in os.walk(self.path, followlinks=False):
                directories.sort()
                for name in directories + sorted(filenames):
                    target = _absolute_no_symlink(Path(root) / name)
                    relative = _valid_member(target.relative_to(self.path).as_posix())
                    if target.is_dir():
                        _outside_code(target)
                        continue
                    if not target.is_file() or target.stat().st_nlink != 1:
                        raise ExportError("nonregular_or_hardlinked_source_member")
                    self.entries[relative] = {"member": relative, "bytes": target.stat().st_size}
                    if len(self.entries) > MAX_ARCHIVE_ENTRIES:
                        raise ExportError("source_entry_limit_exceeded")
        elif self.kind == "zip":
            _file_sha(self.path)  # Refuse symlinks/hardlinks before opening.
            try:
                with zipfile.ZipFile(self.path) as archive:
                    if len(archive.infolist()) > MAX_ARCHIVE_ENTRIES:
                        raise ExportError("archive_entry_limit_exceeded")
                    seen = set()
                    for entry in archive.infolist():
                        name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
                        try:
                            name = _valid_member(name)
                        except ExportError as exc:
                            raise ExportError("unsafe_or_duplicate_archive_member") from exc
                        mode = stat.S_IFMT(entry.external_attr >> 16)
                        if name in seen or mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                            raise ExportError("unsafe_or_duplicate_archive_member")
                        seen.add(name)
                        if not entry.is_dir():
                            self.entries[name] = {"member": name, "bytes": entry.file_size}
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise ExportError("unreadable_zip") from exc
        else:
            _file_sha(self.path)
            self.entries[self.path.name] = {"member": self.path.name, "bytes": self.path.stat().st_size}
        discovered = sorted(name for name in self.entries if _conversation_filename(name))
        self.selection = {"basis": "container_filename_discovery", "declared_manifest_matched": False,
                          "shard_set_completeness": "unknown", "final_part_known": False,
                          "missing_members": [], "unexpected_conversation_members": [],
                          "ordinal_gaps": [], "visible_number_sequence_gaps": []}
        expected_hashes = {}
        if source_manifest is not None:
            if (not isinstance(source_manifest, dict) or set(source_manifest) != {"schema_version", "expected_parts", "members"}
                    or type(source_manifest.get("schema_version")) is not int or source_manifest.get("schema_version") != 1
                    or type(source_manifest.get("expected_parts")) is not int
                    or not 1 <= source_manifest["expected_parts"] <= MAX_ARCHIVE_ENTRIES
                    or not isinstance(source_manifest.get("members"), list)
                    or len(source_manifest["members"]) > MAX_ARCHIVE_ENTRIES):
                raise ExportError("invalid_source_manifest")
            rows = source_manifest["members"]
            selected, ordinals = [], []
            for row in rows:
                if not isinstance(row, dict) or not {"path", "ordinal"} <= set(row) or set(row) - {"path", "ordinal", "sha256"}:
                    raise ExportError("invalid_source_manifest_member")
                name = _valid_member(row["path"])
                if type(row["ordinal"]) is not int or row["ordinal"] < 1 or name in selected:
                    raise ExportError("invalid_source_manifest_sequence")
                selected.append(name)
                ordinals.append(row["ordinal"])
                if "sha256" in row:
                    if not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
                        raise ExportError("invalid_source_manifest_hash")
                    expected_hashes[name] = row["sha256"]
            self.selection.update(basis="explicit_user_declared_manifest", declared_expected_parts=source_manifest["expected_parts"],
                                  declared_members=rows,
                                  missing_members=sorted(set(selected) - set(self.entries)),
                                  missing_file_ordinals=sorted(row["ordinal"] for row in rows if row["path"] not in self.entries),
                                  unexpected_conversation_members=sorted(set(discovered) - set(selected)),
                                  ordinal_gaps=sorted(set(range(1, source_manifest["expected_parts"] + 1)) - set(ordinals)))
            if len(rows) != source_manifest["expected_parts"] or sorted(ordinals) != list(range(1, len(rows) + 1)):
                raise ExportError("source_manifest_sequence_mismatch", self.selection)
            if self.selection["missing_members"]:
                raise ExportError("source_manifest_missing_members", self.selection)
            if self.selection["unexpected_conversation_members"]:
                raise ExportError("source_manifest_unexpected_conversation_members", self.selection)
            selected = [name for _, name in sorted(zip(ordinals, selected))]
            self.selection["declared_manifest_matched"] = True
        else:
            selected = [self.path.name] if self.kind == "json" else discovered
            if not selected:
                raise ExportError("conversation_files_not_identified_supply_manifest")
        families = {}
        for name in selected:
            match = re.match(r"^(.*?)([0-9]+)(\.json)$", name, re.I)
            if match:
                if len(match.group(2)) > 18:
                    self.selection["visible_number_sequence_gaps"].append({
                        "family": match.group(1) + "<number>" + match.group(3),
                        "missing_numbers": None, "status": "index_too_large_to_parse"})
                    continue
                family = match.group(1) + "<number>" + match.group(3)
                families.setdefault(family, []).append(int(match.group(2)))
        for family, numbers in sorted(families.items()):
            # Visible interior gaps are clues only; no assumption about index
            # origin or final shard is promoted into an official schema claim.
            unique = sorted(set(numbers))
            if unique and unique[-1] - unique[0] <= MAX_ARCHIVE_ENTRIES:
                gaps = sorted(set(range(unique[0], unique[-1] + 1)) - set(unique))
                if gaps:
                    self.selection["visible_number_sequence_gaps"].append({"family": family, "missing_numbers": gaps})
            elif unique:
                self.selection["visible_number_sequence_gaps"].append({"family": family,
                    "missing_numbers": None, "range_start": unique[0], "range_end": unique[-1],
                    "status": "range_too_large_to_enumerate"})
        if max_total_bytes is not None and sum(self.entries[name]["bytes"] for name in selected) > max_total_bytes:
            raise ExportError("total_source_bytes_limit_exceeded")
        self.shards = []
        observed_total = 0
        for index, name in enumerate(selected):
            with self.open_member(name) as stream:
                fingerprint, size = _stream_sha(stream, None if max_total_bytes is None else max_total_bytes - observed_total)
            observed_total += size
            if size != self.entries[name]["bytes"]:
                raise ExportError("source_member_size_changed")
            if name in expected_hashes and fingerprint != expected_hashes[name]:
                raise ExportError("source_manifest_hash_mismatch")
            self.shards.append({"member": name, "sha256": fingerprint, "bytes": size,
                                "ordinal": index + 1,
                                "preserved_path": "original-conversations.json" if len(selected) == 1 else "original-files/" + str(index) + ".json"})
        self.inventory = [dict(self.entries[name], status="present_in_archive_unread" if self.kind == "zip" else "present_in_source_unread")
                          for name in sorted(self.entries) if name not in selected]
        fingerprint = _file_sha(self.path) if self.kind != "directory" else digest(encode(self.shards))
        self.info = {"sha256": fingerprint, "json_sha256": self.shards[0]["sha256"] if len(self.shards) == 1 else None,
                     "bytes": self.path.stat().st_size if self.kind != "directory" else sum(s["bytes"] for s in self.shards),
                     "format": ("zip_mapping_json" if self.kind == "zip" else "mapping_json") if len(self.shards) == 1 and self.kind != "directory" else self.kind + "_mapping_json_shards",
                     "member": self.shards[0]["member"] if self.kind == "zip" and len(self.shards) == 1 else None,
                     "files": self.shards}

    @contextmanager
    def open_member(self, name):
        _valid_member(name)
        if name not in self.entries:
            raise ExportError("member_not_in_explicit_container")
        if self.kind == "zip":
            try:
                with zipfile.ZipFile(self.path) as archive:
                    with archive.open(name) as stream:
                        yield stream
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise ExportError("unreadable_zip") from exc
        else:
            path = self.path / name if self.kind == "directory" else self.path
            _absolute_no_symlink(path)
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ExportError("nonregular_or_hardlinked_source_member")
            with path.open("rb") as stream:
                yield stream

    def records(self, max_record_bytes):
        for source_index, shard in enumerate(self.shards):
            with self.open_member(shard["member"]) as stream:
                for record_index, value in enumerate(iter_json_array(stream, max_record_bytes=max_record_bytes)):
                    yield source_index, record_index, value

    def unchanged(self):
        if self.kind != "directory" and _file_sha(self.path) != self.info["sha256"]:
            raise ExportError("source_changed_during_import")
        for shard in self.shards:
            with self.open_member(shard["member"]) as stream:
                fingerprint, size = _stream_sha(stream)
            if fingerprint != shard["sha256"] or size != shard["bytes"]:
                raise ExportError("source_changed_during_import")

def _identifier(value, code):
    if not isinstance(value, str) or not value.strip():
        raise ExportError(code)
    return value


def _conversation_id(conversation):
    if not isinstance(conversation, dict):
        raise ExportError("unsupported_conversation_shape")
    identity = conversation.get("id", conversation.get("conversation_id"))
    _identifier(identity, "conversation_id_missing")
    if "conversation_id" in conversation and conversation["conversation_id"] != identity:
        raise ExportError("conflicting_conversation_ids")
    return identity


def _timestamp(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ExportError("unsupported_timestamp")
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError) as exc:
        raise ExportError("unsupported_timestamp") from exc


def _limited(value):
    if isinstance(value, dict):
        if any(value.get(key) is True for key in
               ("truncated", "isTruncated", "partial", "partial_results", "incomplete", "content_omitted")):
            return True
        return any(_limited(child) for child in value.values())
    if isinstance(value, list):
        return any(_limited(child) for child in value)
    return False


def normalize_conversation(conversation):
    identity = _conversation_id(conversation)
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        raise ExportError("unsupported_conversation_mapping")
    source_limited = _limited(conversation)
    messages = []
    seen_messages = {}
    for node_id, node in mapping.items():
        _identifier(node_id, "node_id_missing")
        if not isinstance(node, dict) or node.get("id") != node_id or "message" not in node:
            raise ExportError("unsupported_mapping_node")
        parent, children = node.get("parent"), node.get("children")
        if parent is not None and (not isinstance(parent, str) or parent not in mapping):
            raise ExportError("mapping_parent_missing")
        if not isinstance(children, list) or any(not isinstance(c, str) or c not in mapping for c in children):
            raise ExportError("mapping_children_missing")
        if len(set(children)) != len(children):
            raise ExportError("duplicate_mapping_child")
        message = node["message"]
        if message is None:
            continue
        if not isinstance(message, dict):
            raise ExportError("unsupported_message_shape")
        message_id = _identifier(message.get("id"), "message_id_missing")
        if message_id in seen_messages:
            previous, original_hash = seen_messages[message_id]
            if digest(encode(message)) != original_hash:
                raise ExportError("conflicting_duplicate_message_id")
            previous.setdefault("node_occurrences", [{"node_id": previous["node_id"],
                "parent_node_id": previous["parent_node_id"], "child_node_ids": previous["child_node_ids"]}]).append(
                    {"node_id": node_id, "parent_node_id": parent, "child_node_ids": children})
            continue
        author = message.get("author")
        if not isinstance(author, dict):
            raise ExportError("message_author_missing")
        role = _identifier(author.get("role"), "message_role_missing")
        content = message.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("content_type"), str):
            raise ExportError("unsupported_message_content")
        parts = content.get("parts")
        text_parts = []
        nontext_parts = 0
        content_supported = content["content_type"] in ("text", "multimodal_text") and isinstance(parts, list)
        if content_supported:
            for part in parts:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    nontext_parts += 1
                else:
                    raise ExportError("unsupported_content_part")
        metadata = message.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ExportError("unsupported_message_metadata")
        attachments = metadata.get("attachments", [])
        if not isinstance(attachments, list):
            raise ExportError("unsupported_attachment_metadata")
        # A limiting flag anywhere in this source remains a source-level limit,
        # including scaffolding nodes, metadata and multimodal parts.
        limited = source_limited
        messages.append({"source_kind": "chatgpt_export", "conversation_id": identity,
                         "node_id": node_id, "message_id": message_id,
                         "parent_node_id": parent, "child_node_ids": children,
                         "author_role": role, "text_parts": text_parts,
                         "text": "\n".join(text_parts), "content_type": content["content_type"],
                         "source_created_at_utc": _timestamp(message.get("create_time")),
                         "source_updated_at_utc": _timestamp(message.get("update_time")),
                         "event_date": None, "confirmation_date": None,
                         "read_status": "partial" if limited else "text_extracted" if content_supported else "raw_only_unsupported_content",
                         "nontext_parts_unread": nontext_parts,
                         "attachment_references_unread": len(attachments),
                         "raw_message_sha256": digest(encode(message))})
        seen_messages[message_id] = (messages[-1], digest(encode(message)))
    current = conversation.get("current_node")
    if current is not None and (not isinstance(current, str) or current not in mapping):
        raise ExportError("current_node_missing")
    # A mapping can contain every branch; do not walk only current_node ancestry.
    return messages


def _message_bytes(messages):
    return b"".join(encode(row) for row in messages)


def _receipt(conversation, messages):
    return {"conversation_id": _conversation_id(conversation),
            "raw_sha256": digest(encode(conversation)),
            "messages_sha256": digest(_message_bytes(messages)),
            "mapping_nodes": len(conversation["mapping"]), "messages": len(messages),
            "raw_message_occurrences": sum(node["message"] is not None for node in conversation["mapping"].values()),
            "duplicate_message_occurrences": sum(node["message"] is not None for node in conversation["mapping"].values()) - len(messages),
            "source_truncation_observed": _limited(conversation),
            "raw_only_messages": sum(row["read_status"] == "raw_only_unsupported_content" for row in messages),
            "partial_messages": sum(row["read_status"] == "partial" for row in messages),
            "nontext_parts_unread": sum(row["nontext_parts_unread"] for row in messages),
            "attachment_references_unread": sum(row["attachment_references_unread"] for row in messages)}


def _validate_cutoff(value):
    if value is None:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ExportError("cutoff_requires_timestamp_with_timezone") from exc


def _raw_summary(conversation):
    """Count original nodes/message identities without using normalized output."""
    identity = _conversation_id(conversation)
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        raise ExportError("unsupported_conversation_mapping")
    messages = {}
    occurrences = 0
    for node in mapping.values():
        if not isinstance(node, dict) or "message" not in node:
            raise ExportError("unsupported_mapping_node")
        message = node["message"]
        if message is None:
            continue
        if not isinstance(message, dict):
            raise ExportError("unsupported_message_shape")
        message_id = _identifier(message.get("id"), "message_id_missing")
        fingerprint = digest(encode(message))
        if message_id in messages and messages[message_id] != fingerprint:
            raise ExportError("conflicting_duplicate_message_id")
        messages[message_id] = fingerprint
        occurrences += 1
    return identity, {"mapping_nodes": len(mapping), "messages": len(messages),
                      "message_occurrences": occurrences}


def _scan_catalog(catalog, max_record_bytes):
    """Keep only identities, hashes and counters in RAM, never record bodies."""
    seen, plan = {}, []
    counts = {"conversations": 0, "mapping_nodes": 0, "messages": 0}
    raw_counts = {"conversation_records": 0, "mapping_nodes": 0, "message_occurrences": 0}
    per_file = [{"ordinal": shard["ordinal"], "conversation_records": 0} for shard in catalog.shards]
    for source_index, record_index, conversation in catalog.records(max_record_bytes):
        identity, summary = _raw_summary(conversation)
        fingerprint = digest(encode(conversation))
        duplicate = identity in seen
        # A repeated conversation can be deduplicated only if its entire
        # canonical original is identical; partial/split revisions are conflicts.
        if duplicate:
            old_index, old_hash = seen[identity]
            if old_hash != fingerprint:
                raise ExportError("conflicting_duplicate_conversation", {"source_index": source_index, "record_index": record_index})
            unique_index = old_index
        else:
            unique_index = len(seen)
            seen[identity] = (unique_index, fingerprint)
            counts["conversations"] += 1
            counts["mapping_nodes"] += summary["mapping_nodes"]
            counts["messages"] += summary["messages"]
        raw_counts["conversation_records"] += 1
        raw_counts["mapping_nodes"] += summary["mapping_nodes"]
        raw_counts["message_occurrences"] += summary["message_occurrences"]
        per_file[source_index]["conversation_records"] += 1
        plan.append({"source_index": source_index, "record_index": record_index,
                     "conversation_index": unique_index, "conversation_id": identity,
                     "raw_sha256": fingerprint, "duplicate": duplicate})
    catalog.unchanged()
    return plan, counts, raw_counts, per_file


def _private_sha(path):
    _absolute_no_symlink(path)
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ExportError("private_checkpoint_permissions_or_type_changed")
    if path.stat().st_nlink != 1:
        raise ExportError("hardlinked_private_file_refused")
    return _file_sha(path)


def _copy_member(catalog, shard, target):
    _absolute_no_symlink(target)
    if target.exists():
        if _private_sha(target) != shard["sha256"]:
            raise ExportError("preserved_source_changed")
        return
    fd, temporary = tempfile.mkstemp(prefix=".source-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as out, catalog.open_member(shard["member"]) as incoming:
            fingerprint = hashlib.sha256()
            total = 0
            while True:
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > shard["bytes"]:
                    raise ExportError("source_member_size_changed")
                fingerprint.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if fingerprint.hexdigest() != shard["sha256"] or total != shard["bytes"]:
            raise ExportError("source_changed_during_copy")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _manifest_base(catalog, account_scope, cutoff, source_manifest, max_record_bytes, max_total_bytes):
    return {"schema_version": SCHEMA_VERSION, "adapter": "chatgpt_official_export_mapping_v2",
            "source": catalog.info, "source_selection": catalog.selection,
            "declared_source_manifest": source_manifest,
            "account_scope": account_scope, "account_scope_basis": "user_declared_not_authenticated_by_adapter",
            "declared_export_cutoff_utc": cutoff, "cutoff_basis": "user_declared" if cutoff else "unknown",
            "import_started_at_utc": datetime.now(timezone.utc).isoformat(),
            "enumeration_scope": "all_records_and_branches_in_selected_source_files",
            "pagination": "not_applicable_offline_files", "expected_file_counts": None,
            "raw_source_counts": None, "per_file_counts": [], "conversations": [], "occurrences": [],
            "next_index": 0, "next_record": 0, "status": "preparing", "last_error": None,
            "capacity_policy": {"max_record_bytes": max_record_bytes, "max_total_bytes": max_total_bytes,
                "max_source_entries": MAX_ARCHIVE_ENTRIES, "max_record_nesting": 256,
                "memory": "one_conversation_plus_identity_hash_and_checkpoint_metadata",
                "total_limit_basis": "selected_uncompressed_JSON_bytes"},
            "coverage": {"supplied_file_import_complete": False, "account_enumeration_complete": "unknown",
                         "shard_set_completeness": "unknown", "chatgpt_memory_complete": "not_assessed",
                         "attachment_content_complete": "not_assessed", "deleted_or_temporary_chats": "not_claimed",
                         "server_export_completeness": "not_verified"}}


def _check_manifest(manifest, catalog):
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("adapter") != "chatgpt_official_export_mapping_v2"):
        raise ExportError("unsupported_resume_manifest")
    if manifest.get("source") != catalog.info or manifest.get("source_selection") != catalog.selection:
        raise ExportError("resume_source_or_scope_changed")
    receipts, occurrences = manifest.get("conversations"), manifest.get("occurrences")
    if (not isinstance(receipts, list) or any(not isinstance(r, dict) for r in receipts)
            or not isinstance(occurrences, list) or any(not isinstance(r, dict) for r in occurrences)
            or type(manifest.get("next_index")) is not int or manifest["next_index"] != len(receipts)
            or type(manifest.get("next_record")) is not int or manifest["next_record"] != len(occurrences)):
        raise ExportError("invalid_resume_checkpoint")


def verify_import(source, output, *, accepted=False, source_manifest=None,
                  max_record_bytes=MAX_RECORD_BYTES, max_total_bytes=None):
    """Read-only independent streaming reconciliation of every selected source.

    Source files and their preserved originals are freshly hashed. Unique counts
    are derived independently from original mapping identities. Every committed
    normalized body and receipt is rederived; saved success flags are ignored.
    """
    if accepted is not True:
        raise ExportError("explicit_user_acceptance_required")
    destination = _private_output(output, True)
    catalog = SourceCatalog(source, source_manifest=source_manifest, max_total_bytes=max_total_bytes)
    manifest = decode(_read_private(destination / "manifest.json"))
    _check_manifest(manifest, catalog)
    for shard in catalog.shards:
        if _private_sha(destination / shard["preserved_path"]) != shard["sha256"]:
            raise ExportError("verify_preserved_source_mismatch")
    if decode(_read_private(destination / "archive-inventory.json")) != catalog.inventory:
        raise ExportError("verify_preserved_source_mismatch")
    plan, expected, raw_counts, per_file = _scan_catalog(catalog, max_record_bytes)
    if (manifest["next_record"] > len(plan) or manifest["occurrences"] != plan[:manifest["next_record"]]
            or manifest.get("expected_file_counts") != expected or manifest.get("raw_source_counts") != raw_counts
            or manifest.get("per_file_counts") != per_file):
        raise ExportError("verify_checkpoint_inventory_mismatch")
    receipts = manifest["conversations"]
    observed = {"conversations": 0, "mapping_nodes": 0, "messages": 0}
    seen_records = 0
    for ordinal, (source_index, record_index, original) in enumerate(catalog.records(max_record_bytes)):
        if ordinal >= len(plan):
            raise ExportError("source_changed_during_verification")
        seen_records = ordinal + 1
        entry = plan[ordinal]
        if entry["source_index"] != source_index or entry["record_index"] != record_index or entry["raw_sha256"] != digest(encode(original)):
            raise ExportError("source_changed_during_verification")
        if ordinal >= manifest["next_record"] or entry["duplicate"]:
            continue
        index = entry["conversation_index"]
        if index != observed["conversations"] or index >= len(receipts):
            raise ExportError("verify_checkpoint_inventory_mismatch")
        raw_output = _read_private(destination / "conversations" / (str(index) + ".json"))
        message_output = _read_private(destination / "messages" / (str(index) + ".jsonl"))
        normalized = normalize_conversation(original)
        if raw_output != encode(original) or message_output != _message_bytes(normalized):
            raise ExportError("verify_output_content_mismatch")
        if receipts[index] != _receipt(original, normalized):
            raise ExportError("verify_receipt_coverage_mismatch")
        persisted = decode(raw_output)
        persisted_messages = [decode(line) for line in message_output.splitlines()]
        _, independently_counted = _raw_summary(persisted)
        observed["conversations"] += 1
        observed["mapping_nodes"] += independently_counted["mapping_nodes"]
        observed["messages"] += len(persisted_messages)
        if len(persisted_messages) != independently_counted["messages"]:
            raise ExportError("verify_message_identity_mismatch")
    if observed["conversations"] != len(receipts):
        raise ExportError("verify_checkpoint_inventory_mismatch")
    if seen_records != len(plan):
        raise ExportError("source_changed_during_verification")
    # Re-enumerate too: a newly added/removed shard or unread sibling must not be
    # missed just because the previously selected files kept their hashes.
    final_catalog = SourceCatalog(source, source_manifest=source_manifest, max_total_bytes=max_total_bytes)
    if final_catalog.info != catalog.info or final_catalog.inventory != catalog.inventory or final_catalog.selection != catalog.selection:
        raise ExportError("source_changed_during_verification")
    complete = manifest["next_record"] == len(plan) and observed == expected
    return {"status": "matched", "raw_source_counts": expected, "raw_occurrence_counts": raw_counts,
            "persisted_output_counts": observed, "next_index": len(receipts), "next_record": manifest["next_record"],
            "duplicate_conversation_records": sum(entry["duplicate"] for entry in plan[:manifest["next_record"]]),
            "supplied_file_import_complete": complete, "account_enumeration_complete": "unknown",
            "shard_set_completeness": "unknown", "chatgpt_memory_complete": "not_assessed",
            "scope": "selected_source_files_only_not_service_inventory"}


def import_export(source, output, *, accepted=False, account_scope,
                  export_cutoff_utc=None, resume=False, max_conversations=None,
                  source_manifest=None, max_record_bytes=MAX_RECORD_BYTES, max_total_bytes=None):
    """Import the explicit files, with bounded-memory record parsing.

    ``source_manifest`` is an optional user-declared list, not a provider proof.
    ``max_conversations`` bounds source records per foreground batch, including
    duplicates. Original files are copied privately before parsing so conflicts
    and malformed records leave their source evidence intact. Checkpoints write
    every 100 records and at pause/failure/finish; replay adopts identical files.
    """
    if accepted is not True:
        raise ExportError("explicit_user_acceptance_required")
    _identifier(account_scope, "explicit_account_scope_required")
    cutoff = _validate_cutoff(export_cutoff_utc)
    if max_conversations is not None and (type(max_conversations) is not int or max_conversations < 1):
        raise ExportError("positive_batch_size_required")
    if type(max_record_bytes) is not int or max_record_bytes < 1:
        raise ExportError("positive_record_limit_required")
    destination = _private_output(output, resume)
    source = _absolute_no_symlink(source)
    if source == destination or destination in source.parents or source in destination.parents:
        raise ExportError("source_and_output_must_be_separate")
    catalog = SourceCatalog(source, source_manifest=source_manifest, max_total_bytes=max_total_bytes)
    if resume:
        manifest = decode(_read_private(destination / "manifest.json"))
        _check_manifest(manifest, catalog)
        if manifest.get("account_scope") != account_scope or manifest.get("declared_export_cutoff_utc") != cutoff:
            raise ExportError("resume_source_or_scope_changed")
        if manifest.get("expected_file_counts") is not None:
            try:
                verify_import(source, destination, accepted=True, source_manifest=source_manifest,
                              max_record_bytes=max_record_bytes, max_total_bytes=max_total_bytes)
            except ExportError as exc:
                if str(exc) in ("verify_output_content_mismatch", "verify_receipt_coverage_mismatch"):
                    raise ExportError("resume_output_integrity_failure") from exc
                raise
        else:
            # Failed parsing before inventory completion can resume only with
            # the same preserved source and an empty committed record ledger.
            if manifest["conversations"] or manifest["occurrences"]:
                raise ExportError("invalid_resume_checkpoint")
            for shard in catalog.shards:
                preserved = destination / shard["preserved_path"]
                if preserved.exists() and _private_sha(preserved) != shard["sha256"]:
                    raise ExportError("preserved_source_changed")
            inventory_path = destination / "archive-inventory.json"
            if inventory_path.exists() and decode(_read_private(inventory_path)) != catalog.inventory:
                raise ExportError("verify_preserved_source_mismatch")
    else:
        destination.mkdir(mode=0o700)
        for directory in ("conversations", "messages", "original-files"):
            (destination / directory).mkdir(mode=0o700)
        manifest = _manifest_base(catalog, account_scope, cutoff, source_manifest, max_record_bytes, max_total_bytes)
        _write(destination / "manifest.json", encode(manifest))
        _write(destination / "archive-inventory.json", encode(catalog.inventory))
    try:
        if not (destination / "archive-inventory.json").exists():
            _write(destination / "archive-inventory.json", encode(catalog.inventory))
        for shard in catalog.shards:
            _copy_member(catalog, shard, destination / shard["preserved_path"])
        plan, expected, raw_counts, per_file = _scan_catalog(catalog, max_record_bytes)
        manifest.update(status="importing", last_error=None, reconciliation=None,
                        expected_file_counts=expected, raw_source_counts=raw_counts, per_file_counts=per_file)
        manifest["coverage"]["supplied_file_import_complete"] = False
        manifest["capacity_policy"].update(max_record_bytes=max_record_bytes, max_total_bytes=max_total_bytes)
        _write(destination / "manifest.json", encode(manifest))
        processed = 0
        seen_records = 0
        for ordinal, (source_index, record_index, original) in enumerate(catalog.records(max_record_bytes)):
            if ordinal >= len(plan):
                raise ExportError("source_changed_during_import")
            seen_records = ordinal + 1
            if ordinal < manifest["next_record"]:
                continue
            if max_conversations is not None and processed >= max_conversations:
                manifest["status"] = "paused"
                break
            entry = plan[ordinal]
            raw_bytes = encode(original)
            if entry["source_index"] != source_index or entry["record_index"] != record_index or entry["raw_sha256"] != digest(raw_bytes):
                raise ExportError("source_changed_during_import")
            index = entry["conversation_index"]
            if not entry["duplicate"]:
                if index != manifest["next_index"]:
                    raise ExportError("checkpoint_conversation_sequence_mismatch")
                messages = normalize_conversation(original)
                message_bytes = _message_bytes(messages)
                for path, content in ((destination / "conversations" / (str(index) + ".json"), raw_bytes),
                                      (destination / "messages" / (str(index) + ".jsonl"), message_bytes)):
                    if path.exists() and _read_private(path) != content:
                        raise ExportError("uncommitted_output_conflict")
                    _write(path, content)
                    if _read_private(path) != content:
                        raise ExportError("output_readback_failed")
                manifest["conversations"].append(_receipt(original, messages))
                manifest["next_index"] += 1
            elif index >= manifest["next_index"]:
                raise ExportError("checkpoint_duplicate_precedes_original")
            manifest["occurrences"].append(entry)
            manifest["next_record"] = ordinal + 1
            processed += 1
            if processed % 100 == 0:
                _write(destination / "manifest.json", encode(manifest))
        if manifest["status"] != "paused" and seen_records != len(plan):
            raise ExportError("source_changed_during_import")
        catalog.unchanged()
        _write(destination / "manifest.json", encode(manifest))
        if manifest["next_record"] == len(plan):
            verification = verify_import(source, destination, accepted=True, source_manifest=source_manifest,
                                         max_record_bytes=max_record_bytes, max_total_bytes=max_total_bytes)
            if not verification["supplied_file_import_complete"]:
                raise ExportError("independent_verification_incomplete")
            manifest["reconciliation"] = verification
            manifest["coverage"]["supplied_file_import_complete"] = True
            manifest["coverage"]["source_truncation_observed"] = any(r["source_truncation_observed"] for r in manifest["conversations"])
            manifest["coverage"]["text_extraction_complete_for_supported_file"] = not any(
                r["raw_only_messages"] or r["partial_messages"] or r["source_truncation_observed"] for r in manifest["conversations"])
            manifest["status"] = "file_import_complete"
        manifest["import_updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write(destination / "manifest.json", encode(manifest))
        return manifest
    except (ExportError, OSError) as exc:
        manifest["status"] = "failed"
        manifest["last_error"] = str(exc) if isinstance(exc, ExportError) else "filesystem_operation_failed"
        manifest["coverage"]["supplied_file_import_complete"] = False
        if isinstance(exc, ExportError) and exc.details:
            manifest["failure_details"] = exc.details
        _write(destination / "manifest.json", encode(manifest))
        raise ExportError(manifest["last_error"], getattr(exc, "details", None)) from exc


def _public_selection(selection):
    return {"basis": selection.get("basis"), "declared_manifest_matched": selection.get("declared_manifest_matched", False),
            "shard_set_completeness": "unknown", "trailing_completeness": "unknown",
            "missing_member_count": len(selection.get("missing_members", [])),
            "unexpected_conversation_member_count": len(selection.get("unexpected_conversation_members", [])),
            "missing_ordinals": sorted(set(selection.get("ordinal_gaps", []) + selection.get("missing_file_ordinals", []))),
            "observed_internal_gaps": [{key: value for key, value in gap.items() if key != "family"}
                                       for gap in selection.get("visible_number_sequence_gaps", [])]}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Explicit user-provided absolute ZIP, JSON, or export directory")
    parser.add_argument("--output", required=True, help="New absolute private directory outside source/Skill repositories")
    parser.add_argument("--accept-user-provided-export", action="store_true")
    parser.add_argument("--account-scope", required=True, help="User-declared account/workspace label, stored privately")
    parser.add_argument("--export-cutoff-utc", help="Optional user-declared export cutoff; never inferred")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-conversations", type=int)
    parser.add_argument("--source-manifest", help="Explicit absolute JSON manifest: schema_version=1, expected_parts, members(path/ordinal/optional sha256)")
    parser.add_argument("--max-record-bytes", type=int, default=MAX_RECORD_BYTES,
                        help="Single conversation byte limit; default 64 MiB, configurable")
    parser.add_argument("--max-total-bytes", type=int,
                        help="Optional selected uncompressed JSON byte budget; default no hard total cap")
    parser.add_argument("--verify", action="store_true", help="Read-only source/output reconciliation; performs no writes")
    args = parser.parse_args(argv)
    try:
        if not args.accept_user_provided_export:
            raise ExportError("explicit_user_acceptance_required")
        source_manifest = None
        if args.source_manifest:
            manifest_path = _absolute_no_symlink(args.source_manifest)
            _outside_code(manifest_path)
            _file_sha(manifest_path)
            if manifest_path.stat().st_size > MAX_RECORD_BYTES:
                raise ExportError("source_manifest_size_limit_exceeded")
            source_manifest = decode(manifest_path.read_bytes())
        if args.verify:
            result = verify_import(args.source, args.output, accepted=args.accept_user_provided_export,
                                   source_manifest=source_manifest, max_record_bytes=args.max_record_bytes,
                                   max_total_bytes=args.max_total_bytes)
            print(json.dumps(result))
            return 0 if result["supplied_file_import_complete"] else 2
        result = import_export(args.source, args.output, accepted=args.accept_user_provided_export,
                               account_scope=args.account_scope, export_cutoff_utc=args.export_cutoff_utc,
                               resume=args.resume, max_conversations=args.max_conversations,
                               source_manifest=source_manifest, max_record_bytes=args.max_record_bytes,
                               max_total_bytes=args.max_total_bytes)
        # Never print titles, bodies, account labels, archive names or locations.
        print(json.dumps({"status": result["status"], "imported_conversations": result["next_index"],
                          "file_counts": result["expected_file_counts"], "coverage": result["coverage"],
                          "source_inventory": _public_selection(result["source_selection"])}))
        return 0 if result["status"] == "file_import_complete" else 2
    except (ExportError, OSError) as exc:
        result = {"status": "blocked", "reason": str(exc) if isinstance(exc, ExportError) else "filesystem_operation_failed"}
        if isinstance(exc, ExportError) and exc.details and "basis" in exc.details:
            result["source_inventory"] = _public_selection(exc.details)
        print(json.dumps(result), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
