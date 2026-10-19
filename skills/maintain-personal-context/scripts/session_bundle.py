#!/usr/bin/env python3
"""Portable, offline sessions from Agent-reviewed annotations; stdlib only.

export(data, destination) creates a NEW private directory containing session.json,
result.md and session.html. read_bundle verifies digests before fresh-session use.
validate_responses checks downloaded replies without updating facts or a library.
This is not extraction, history access, source authentication or a save authorizer.
The caller supplies already-read source excerpts and actual coverage boundaries.
"""
from __future__ import annotations
import argparse
import base64
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys

CATEGORIES = {"identity": "你是谁", "capability": "你能做什么", "ongoing": "你在做什么", "next": "下一步"}
STATUSES = {"current": "当前", "planned": "计划", "paused": "已暂停", "cancelled": "已取消",
            "uncertain": "待核", "historical": "过往"}
SOURCE_STATES = {"read", "partial", "unread", "blocked"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
ASSET = Path(__file__).resolve().parents[1] / "assets/session.html"


class BundleError(ValueError):
    pass


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fields(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise BundleError(label + " has missing or unexpected fields")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise BundleError(label + " must be nonempty text")
    return value


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise BundleError("invalid identifier")
    return value


def rows(value, label):
    if not isinstance(value, list):
        raise BundleError(label + " must be a list")
    return value


def strings(value, label):
    return [text(v, label) for v in rows(value, label)]


def indexed(value, label):
    result = {}
    for row in rows(value, label):
        if not isinstance(row, dict):
            raise BundleError(label + " row must be an object")
        key = identifier(row.get("id"))
        if key in result:
            raise BundleError("duplicate " + label + " identifier")
        result[key] = row
    return result


def refs(value, known, label, required=True):
    values = rows(value, label)
    if (required and not values) or any(not isinstance(v, str) or v not in known for v in values) \
            or len(set(values)) != len(values):
        raise BundleError(label + " must reference distinct existing identifiers")


def validate(data, sealed=False):
    """Validate structure and cross-references, not meaning or evidence truth."""
    fields(data, "schema_version title as_of sources coverage facts advice questions", "session")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise BundleError("unsupported session schema")
    text(data["title"], "title")
    try:
        stamp = datetime.fromisoformat(text(data["as_of"], "as_of").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise BundleError("as_of needs an ISO timestamp with timezone") from None
    coverage = data["coverage"]
    fields(coverage, "scope limits uncovered", "coverage")
    text(coverage["scope"], "checked scope")
    strings(coverage["limits"], "coverage limits")
    strings(coverage["uncovered"], "uncovered sources")
    sources = indexed(data["sources"], "source")
    for source in sources.values():
        keys = "id title locator kind read_status time_range limitations snapshot"
        fields(source, keys + (" snapshot_sha256" if sealed else ""), "source")
        for key in ("title", "locator", "kind", "time_range"):
            text(source[key], "source " + key)
        if source["read_status"] not in SOURCE_STATES:
            raise BundleError("invalid source read status")
        limitations = strings(source["limitations"], "source limitations")
        if source["read_status"] != "read" and not limitations:
            raise BundleError("partial, unread or blocked sources need a limitation")
        snapshot = source["snapshot"]
        if source["read_status"] in {"read", "partial"}:
            text(snapshot, "actual source excerpt")
        elif snapshot is not None:
            raise BundleError("unread or blocked source snapshot must be null")
        if sealed and source["snapshot_sha256"] != (digest(snapshot.encode("utf-8")) if snapshot is not None else None):
            raise BundleError("source snapshot digest mismatch")
    facts = indexed(data["facts"], "fact")
    readable = {s for s in sources if sources[s]["read_status"] in {"read", "partial"}}
    for fact in facts.values():
        fields(fact, "id text status category source_ids sensitivity", "fact")
        text(fact["text"], "fact text")
        if fact["status"] not in STATUSES or fact["category"] not in CATEGORIES \
                or fact["sensitivity"] not in {"normal", "sensitive"}:
            raise BundleError("invalid fact status, category or sensitivity")
        refs(fact["source_ids"], readable, "fact sources")
    advice = indexed(data["advice"], "advice")
    for item in advice.values():
        fields(item, "id text fact_ids status condition", "advice")
        text(item["text"], "advice text")
        text(item["condition"], "adoption condition")
        if item["status"] != "conditional":
            raise BundleError("advice must remain conditional")
        refs(item["fact_ids"], facts, "advice facts")
        if any(facts[f]["status"] in {"paused", "cancelled", "uncertain"} for f in item["fact_ids"]):
            raise BundleError("advice cannot advance paused, cancelled or unresolved facts")
    if len(advice) + sum(f["category"] == "next" for f in facts.values()) > 3:
        raise BundleError("next items and advice together have at most three entries")
    questions = indexed(data["questions"], "question")
    if len(questions) > 4:
        raise BundleError("keep the core interview to at most four questions")
    for question in questions.values():
        fields(question, "id text reason fact_ids options answer", "question")
        text(question["text"], "question text")
        text(question["reason"], "question reason")
        refs(question["fact_ids"], facts, "question facts", required=False)
        options = indexed(question["options"], "option")
        if not 2 <= len(options) <= 6:
            raise BundleError("each question needs two to six actual choices")
        for option in options.values():
            fields(option, "id label", "option")
            text(option["label"], "option label")
        if question["answer"] is not None:
            raise BundleError("pending interview answers must stay null")
    return data


def seal(data):
    payload = copy.deepcopy(validate(data))
    for source in payload["sources"]:
        source["snapshot_sha256"] = digest(source["snapshot"].encode("utf-8")) if source["snapshot"] is not None else None
    return payload


def literal(value):
    # Markdown output is inert text even when a source contains HTML or links.
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", str(value).replace("&", "&amp;").replace("<", "&lt;"))


def interview_state(payload):
    return {"stage": "unanswered" if payload["questions"] else "no_pending_questions",
            "unanswered_ids": [q["id"] for q in payload["questions"]]}


def render_markdown(payload, bundle_digest):
    lines = ["# " + literal(payload["title"]), "", "会话快照已导出；知识库写入状态：未据此更新。",
             "", "版本：`" + bundle_digest + "`", "快照时间：" + literal(payload["as_of"]),
             "", "## 来源与覆盖边界", "", literal(payload["coverage"]["scope"])]
    for s in payload["sources"]:
        lines.append("- " + literal(s["id"] + " / " + s["title"] + " / " + s["read_status"] + " / " + s["time_range"]))
        lines.append("  定位：" + literal(s["locator"]))
        lines.extend("  限制：" + literal(v) for v in s["limitations"])
    lines.extend("- 限制：" + literal(v) for v in payload["coverage"]["limits"])
    lines.extend("- 未覆盖：" + literal(v) for v in payload["coverage"]["uncovered"])
    for category, label in CATEGORIES.items():
        lines += ["", "## " + label, ""]
        selected = [f for f in payload["facts"] if f["category"] == category]
        lines += ["- " + literal(f["text"]) + "（" + STATUSES[f["status"]] + "；来源："
                  + literal("、".join(f["source_ids"])) + "；敏感性：" + f["sensitivity"] + "）" for f in selected] or ["暂无已读依据，保留空白。"]
    lines += ["", "## 建议", ""]
    lines += ["- " + literal(a["text"]) + "（仅条件建议；依据事实：" + literal("、".join(a["fact_ids"]))
              + "；采用条件：" + literal(a["condition"]) + "）" for a in payload["advice"]] or ["暂无建议。"]
    state = interview_state(payload)
    lines += ["", "## 访谈进度", "", "阶段：" + state["stage"] + "；回答仍为空。"]
    lines.extend("- " + literal(q["id"] + "：" + q["text"]) + "（未回答）" for q in payload["questions"])
    lines += ["", "新会话先校验 session.json 和来源快照，再核对当前状态；旧快照不证明来源仍是最新，导出不授权执行建议。", ""]
    return "\n".join(lines)


def render_html(payload, bundle_digest):
    template = ASSET.read_text(encoding="utf-8")
    value = json.dumps({"bundle_digest": bundle_digest, "payload": payload}, ensure_ascii=False)
    for raw, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        value = value.replace(raw, escaped)
    script = template.split('<script id="app-code">', 1)[1].split("</script>", 1)[0]
    csp_hash = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    return template.replace("__SCRIPT_HASH__", csp_hash).replace("__SESSION_DATA__", value)


def private_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise BundleError("destination must be an explicit absolute private path")
    for ancestor in [path, *path.parents]:
        if ancestor.is_symlink():
            raise BundleError("symlink paths are not supported")
        if (ancestor / "skills/maintain-personal-context/SKILL.md").exists() \
                or (ancestor / "SKILL.md").exists():
            raise BundleError("session data must be outside public source and installed Skills")
    return path.resolve()


def export(data, destination):
    payload = seal(data)
    bundle_digest = digest(encode(payload))
    outputs = {"result.md": render_markdown(payload, bundle_digest).encode("utf-8"),
               "session.html": render_html(payload, bundle_digest).encode("utf-8")}
    envelope = {"kind": "personal_context_session", "schema_version": 1,
                "bundle_digest": bundle_digest, "payload": payload,
                "interview": interview_state(payload),
                "files": {name: digest(value) for name, value in outputs.items()}}
    outputs["session.json"] = encode(envelope)
    target = private_path(destination)
    if not target.parent.is_dir():
        raise BundleError("create the intended private parent directory first")
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise BundleError("destination exists; preserving it without overwrite") from None
    try:
        for name, value in outputs.items():
            fd = os.open(str(target / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
    except OSError:
        raise BundleError("partial bundle retained; inspect it before choosing a new destination") from None
    return {"status": "exported", "bundle_digest": bundle_digest, "directory": str(target),
            "files": list(outputs), "knowledge_base_updated": False}


def json_load(value):
    def unique_pairs(pairs):
        result = {}
        for key, val in pairs:
            if key in result:
                raise BundleError("duplicate JSON key")
            result[key] = val
        return result
    return json.loads(value, object_pairs_hook=unique_pairs)


def read_bundle(directory, expected_digest=None):
    target = Path(directory).expanduser()
    for path in [target, *target.parents, *(target / n for n in ("session.json", "result.md", "session.html"))]:
        if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
            raise BundleError("linked bundle paths are not supported")
    envelope = json_load((target / "session.json").read_bytes())
    fields(envelope, "kind schema_version bundle_digest payload interview files", "bundle")
    if envelope["kind"] != "personal_context_session" or envelope["schema_version"] != 1:
        raise BundleError("unsupported bundle")
    payload = validate(envelope["payload"], sealed=True)
    actual = digest(encode(payload))
    if envelope["bundle_digest"] != actual or (expected_digest is not None and expected_digest != actual):
        raise BundleError("bundle digest mismatch")
    if envelope["interview"] != interview_state(payload):
        raise BundleError("interview state mismatch")
    fields(envelope["files"], "result.md session.html", "bundle files")
    for name, wanted in envelope["files"].items():
        if digest((target / name).read_bytes()) != wanted:
            raise BundleError("bundle file changed; preserve edits and review before reuse")
    return envelope


def validate_responses(bundle, value):
    """Return untrusted replies for Agent review; never turn them into facts."""
    fields(value, "kind schema_version bundle_digest responses", "responses")
    if value["kind"] != "session_responses" or value["schema_version"] != 1 \
            or value["bundle_digest"] != bundle["bundle_digest"]:
        raise BundleError("responses do not match this bundle")
    questions = {q["id"]: q for q in bundle["payload"]["questions"]}
    normalized, seen = [], set()
    for response in rows(value["responses"], "responses"):
        fields(response, "question_id action answer", "response")
        qid = response["question_id"]
        if not isinstance(qid, str) or qid not in questions or qid in seen:
            raise BundleError("response has unknown or duplicate question")
        seen.add(qid)
        action, answer = response["action"], response["answer"]
        if action not in {"answer", "unanswered", "skip", "uncertain", "do_not_save"}:
            raise BundleError("invalid response action")
        if action == "answer":
            fields(answer, "option_id text", "answer")
            if answer["option_id"] is not None:
                if answer["option_id"] not in {o["id"] for o in questions[qid]["options"]} or answer["text"] is not None:
                    raise BundleError("answer must select one offered choice or free text")
            else:
                text(answer["text"], "free answer")
        else:
            # Explicit no-save and other non-answer actions cannot carry text into reuse.
            answer = None
        normalized.append({"question_id": qid, "action": action, "answer": copy.deepcopy(answer)})
    if seen != set(questions):
        raise BundleError("responses must leave an explicit unanswered entry for every pending question")
    return {"kind": "session_responses", "schema_version": 1, "bundle_digest": bundle["bundle_digest"],
            "responses": normalized, "needs_agent_review": True, "knowledge_base_updated": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("export")
    create.add_argument("--input", required=True)
    create.add_argument("--dest", required=True)
    for command in ("read", "read-responses"):
        read = sub.add_parser(command)
        read.add_argument("--bundle", required=True)
        read.add_argument("--expect-digest")
        if command == "read-responses":
            read.add_argument("--input", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export(json_load(Path(args.input).read_bytes()), args.dest)
        else:
            bundle = read_bundle(args.bundle, args.expect_digest)
            result = bundle if args.command == "read" else validate_responses(bundle, json_load(Path(args.input).read_bytes()))
        print(encode(result).decode("utf-8"), end="")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("session_bundle: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
