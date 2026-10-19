#!/usr/bin/env python3
"""Local Markdown context helper. Python 3.9+, standard library, no network."""
from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
import uuid

START = "<!-- personal-context:start -->"
END = "<!-- personal-context:end -->"
DATA = "<!-- personal-context:data "
CATEGORIES = {
    "identity": "我是谁",
    "experience-skills": "经历技能",
    "resources-collaboration": "资源协作",
    "goals": "目标",
    "daily-projects": "日常项目",
    "outcomes-reviews": "成果复盘",
    "sources-corrections": "来源纠错",
}
STATUS = {"confirmed": "已确认", "pending": "待确认", "unknown": "未知",
          "superseded": "已被替代"}
KINDS = {"fact": "来源所述事实", "inference": "推断", "recommendation": "建议"}
FIXED = ("INDEX.md", "PERSON_PROFILE.md", "LONG_TERM.md", "SOURCES.md")
TEMPLATES = Path(__file__).resolve().parents[1] / "assets" / "templates"
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
# Conservative refusal cues, not a general natural-language truth detector.
NONCURRENT = re.compile(
    r"预计|预期|打算|计划|准备|将要|到时|届时|以后|之后再|稍后|明天|后天|下周|下个月|"
    r"如果|假如|要是|倘若|一旦|只要|等到|建议|不妨|也许|可能|或许|尚不清楚|仍未知|不知道|"
    r"\b(?:will|would|may|might|plan(?:ned)?|expect(?:ed)?|if|should|suggest(?:ed)?)\b", re.I)
# A reporting predicate before a clause is independent of the speaker's name.
ATTRIBUTED = re.compile(r"(?:说(?:过|道)?|表示|声称|认为|提到)"
                        r"(?:[：:，,\s\"“‘]|(?=已经|已|尚未|没|目前|现在|完成|通过))|"
                        r"建议|提议|转述|据说|听说|"
                        r"\b(?:said|says|say|reportedly|suggested|claims?|claimed)\b", re.I)
DEPENDENT = re.compile(r"如果|假如|要是|倘若|一旦|只要|等到|建议|提议|不妨|"
                       r"也许|可能|或许|尚不清楚|仍未知|不知道|"
                       r"\b(?:if|would|may|might|should|suggest(?:ed)?)\b", re.I)
CONDITIONAL = re.compile(r"如果|假如|要是|倘若|一旦|只要|等到|\bif\b", re.I)
CURRENT_ASPECT = re.compile(r"尚未|还没|没有|仍")
CURRENT_TIME = re.compile(r"^(?:现在|目前|当前|现时|眼下|此刻)")
INTENTION = re.compile(r"打算|计划|准备")


def noncurrent(value):
    # An ongoing-state predicate makes 计划 its subject/noun, not an action
    # modality. Keep every other unquoted use conservative, including 我也计划.
    value = re.sub(r"计划(?=还在|仍在|正在|尚在|仍然在)", "  ", value)
    return NONCURRENT.search(value)


def independent_current_prefix(prefix):
    """End independent intentions, never erase an enclosing condition/time."""
    if CONDITIONAL.search(prefix):
        return False
    clauses = [c.strip() for c in re.split(r"[，,；;]", prefix) if c.strip()]
    # A genuine present-time bridge may end an earlier hypothetical move.
    for index, clause in enumerate(clauses):
        if CURRENT_TIME.match(clause) and not DEPENDENT.search(clause):
            clauses = clauses[index:]
            break
    for clause in clauses:
        if DEPENDENT.search(clause):
            return False
        if noncurrent(clause):
            intent = INTENTION.search(clause)
            if not intent or noncurrent(clause[:intent.start()]):
                return False
    return True


class ContextError(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def safe_path(root, rel):
    p = root / rel
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ContextError("unsafe relative path")
    for part in [p, *p.parents]:
        if part.is_symlink():
            raise ContextError("symlink paths are not supported")
    if not p.resolve().is_relative_to(root.resolve()):
        raise ContextError("resolved path escapes private root")
    if p.exists() and p.is_file() and p.stat().st_nlink > 1:
        raise ContextError("hard-linked files are not supported")
    return p


def check_root(value):
    p = Path(value).expanduser()
    if not p.is_absolute():
        raise ContextError("--root must be an explicit absolute private directory")
    safe_path(p, ".context")
    p = p.resolve()
    # Check canonical ancestors, including package copies and installed skills.
    for parent in [p, *p.parents]:
        if (parent / "skills" / "maintain-personal-context" / "SKILL.md").exists():
            raise ContextError("private context must be outside the public source package")
        if parent.name == "maintain-personal-context" and (parent / "SKILL.md").exists():
            raise ContextError("private context must be outside installed skill")
    return p


def create_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        os.chmod(path, 0o600)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".context-write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def lock(root, initializing=False):
    if not initializing and not safe_path(root, ".context/format.json").is_file():
        raise ContextError("initialize an empty private context directory first")
    path = safe_path(root, ".context/lock")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ContextError("another operation or an interrupted lock exists; inspect .context/lock")
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def initialize(root):
    if root.exists() and any(root.iterdir()):
        if (root / ".context/format.json").is_file():
            inventory(root)
            return {"status": "already_initialized"}
        raise ContextError("destination is not empty; refusing to overwrite")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    with lock(root, initializing=True):
        for name in FIXED:
            create_new(safe_path(root, name), (TEMPLATES / name).read_bytes())
        create_new(safe_path(root, ".context/format.json"), encode({"version": 1}))
    return {"status": "initialized", "files": list(FIXED)}


def day(value, field, nullable=True):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ContextError(field + " must be YYYY-MM-DD or null")
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ContextError(field + " is not a valid date")


def validate(record, incoming=True):
    fields = {"id", "category", "layer", "kind", "status", "text", "sources",
              "event_date", "record_date", "valid_from", "confirmed_on",
              "confirmation", "supersedes"}
    if isinstance(record, dict) and "assertion" in record:
        fields.add("assertion")
    if not incoming:
        fields.add("superseded_by")
        if isinstance(record, dict) and "original_status" in record:
            fields.add("original_status")
    if not isinstance(record, dict) or set(record) != fields:
        raise ContextError("record keys must match the documented schema")
    if not isinstance(record["id"], str) or not ID.fullmatch(record["id"]):
        raise ContextError("invalid record id")
    if record["category"] not in CATEGORIES or record["layer"] not in {"profile", "daily", "longterm"}:
        raise ContextError("invalid category or layer")
    if record["kind"] not in KINDS:
        raise ContextError("invalid kind")
    allowed = {"confirmed", "pending", "unknown"} if incoming else set(STATUS)
    if record["status"] not in allowed:
        raise ContextError("invalid status")
    original_status = record.get("original_status")
    if "original_status" in record and (record["status"] != "superseded"
            or original_status not in {"confirmed", "pending", "unknown"}):
        raise ContextError("original_status belongs only to replaced history")
    evidence_status = original_status or record["status"]
    if not isinstance(record["text"], str) or not record["text"].strip() or len(record["text"]) > 4000:
        raise ContextError("text must contain 1-4000 characters")
    sources = record["sources"]
    if not isinstance(sources, list) or len(sources) > 20:
        raise ContextError("sources must be a list of at most 20 references")
    if not sources and evidence_status != "unknown":
        raise ContextError("non-unknown records need a source")
    for source in sources:
        if (not isinstance(source, dict) or set(source) != {"label", "locator"}
                or not isinstance(source["label"], str) or not source["label"].strip()
                or len(source["label"]) > 300
                or not isinstance(source["locator"], str) or not source["locator"].strip()
                or len(source["locator"]) > 1000):
            raise ContextError("source needs an exact supplied label and locator")
    for field in ("event_date", "record_date", "valid_from", "confirmed_on"):
        day(record[field], field, field != "record_date")
    if record["confirmation"] is not None and (
            not isinstance(record["confirmation"], str) or not record["confirmation"].strip()
            or len(record["confirmation"]) > 2000):
        raise ContextError("confirmation must be a specific instruction reference or null")
    if evidence_status == "confirmed":
        if not record["confirmed_on"] or not record["confirmation"]:
            raise ContextError("confirmed records require confirmation date and instruction reference")
    elif evidence_status != "superseded" and (record["confirmed_on"] or record["confirmation"]):
        raise ContextError("pending/unknown records cannot carry a confirmation")
    if record["layer"] in {"profile", "longterm"} and evidence_status == "pending":
        raise ContextError("pending candidates belong in daily records, outside long-term context")
    if (not isinstance(record["supersedes"], list)
            or any(not isinstance(x, str) or not ID.fullmatch(x) for x in record["supersedes"])
            or len(set(record["supersedes"])) != len(record["supersedes"])
            or record["id"] in record["supersedes"]):
        raise ContextError("invalid supersedes references")
    if record["supersedes"]:
        historical = not incoming and record["status"] == "superseded"
        if (record["status"] != "confirmed" and not historical
                or not record["confirmed_on"] or not record["confirmation"]):
            raise ContextError("replacement needs explicit confirmation")
    if "assertion" in record:
        validate_assertion(record)
    if not incoming:
        target = record["superseded_by"]
        if target is not None and (not isinstance(target, str) or not ID.fullmatch(target)):
            raise ContextError("invalid superseded_by")
        if (record["status"] == "superseded") != bool(target):
            raise ContextError("superseded status and replacement must agree")


def source_view(value, reported_boundaries=False):
    """Mask quoted literals without changing offsets; never invent missing quotes."""
    opening = {'“': '”', '‘': '’', '「': '」', '『': '』', '《': '》', '〈': '〉',
               '"': '"', "'": "'"}
    closing = set(opening.values())
    result, stack = list(value), []
    for i, char in enumerate(value):
        # Decimal points, versions and filenames do not end a source clause or
        # erase a condition before the selected statement. Keep offsets stable.
        if char == '.' and 0 < i < len(value) - 1 and value[i-1].isalnum() and value[i+1].isalnum():
            result[i] = ' '
            continue
        # An apostrophe inside an ASCII word is not a quoted literal.
        if (char in {"'", '’'} and 0 < i < len(value) - 1
                and all(c.isascii() and c.isalnum() for c in (value[i-1], value[i+1]))):
            if stack:
                result[i] = ' '
            continue
        if stack:
            result[i] = ' '
            if char == stack[-1]:
                stack.pop()
                # A sentence-final mark inside closed direct speech ends that
                # speaker's report. Use this boundary only for attribution;
                # enclosing conditions/time cues must retain their full scope.
                if (reported_boundaries and not stack and char not in {'》', '〉'}
                        and value[:i].rstrip(' \t\r\n”’」』\"\'').endswith(('。', '.', '!', '?', '？'))):
                    result[i] = '。'
            elif char in opening:
                stack.append(opening[char])
            elif char in closing:
                return '', False
        elif char in opening:
            result[i] = ' '
            stack.append(opening[char])
        elif char in closing:
            return '', False
    return ''.join(result), not stack


def validate_assertion(record):
    """Bind an event-state claim to an unshortened source clause, fail closed."""
    a = record["assertion"]
    if (not isinstance(a, dict) or set(a) != {
            "state", "author_role", "source", "source_text", "statement"}
            or not isinstance(a["state"], str) or not isinstance(a["author_role"], str)
            or a["state"] not in {"observed", "planned", "uncertain"}
            or a["author_role"] not in {"user", "third_party", "assistant", "unknown"}):
        raise ContextError("assertion needs state, author_role, source, source_text and statement")
    if a["source"] not in record["sources"]:
        raise ContextError("assertion source must match a supplied source reference")
    if (not isinstance(a["source_text"], str) or not a["source_text"].strip()
            or len(a["source_text"]) > 6000
            or not isinstance(a["statement"], str) or not a["statement"].strip()
            or len(a["statement"]) > 4000):
        raise ContextError("assertion needs bounded raw source text and a statement")
    # Match a complete punctuation-delimited clause; dropping a negation or condition
    # from the front of a clause must not produce independent event evidence.
    statement = a["statement"].strip()
    view, balanced = source_view(a["source_text"])
    if not balanced:
        raise ContextError("assertion source has unbalanced quotes; preserve raw text and confirm")
    matches = [m for m in re.finditer(r"[^。；;，,\n.!]+", view)
               if a["source_text"][m.start():m.end()].strip() == statement]
    if len(matches) != 1 or record["text"].strip().rstrip("。.!；;") != statement:
        raise ContextError("assertion text must preserve one complete verbatim source clause")
    if a["state"] != "observed":
        if record["layer"] != "daily" or record["supersedes"]:
            raise ContextError("planned/uncertain assertions stay daily without supersedes")
        return
    # Earlier clauses in the same sentence can condition or attribute this claim.
    # Later independent plans do not undo an explicit observation/correction.
    prefix = re.split(r"[。\n.!?？]", view[:matches[0].start()])[-1]
    reported_view, _ = source_view(a["source_text"], reported_boundaries=True)
    report_prefix = re.split(r"[。\n.!?？]", reported_view[:matches[0].start()])[-1]
    statement_view, balanced = source_view(statement)
    scope = prefix + statement_view
    # An explicit current-time clause can follow an independent future intention.
    # It cannot escape a preceding condition, uncertainty or reported speaker.
    temporal_prefix = prefix
    independent_aspect = (CURRENT_ASPECT.search(statement_view)
                          and independent_current_prefix(prefix))
    if ((re.match(r"^(?:现在|目前|当前|现时)", statement_view.strip())
         and not DEPENDENT.search(prefix)) or independent_aspect):
        temporal_prefix = ""
    if (a["author_role"] != "user" or not balanced
            or not any(c.isalnum() for c in statement_view)
            or noncurrent(temporal_prefix + statement_view)
            or ATTRIBUTED.search(report_prefix + statement_view) or re.search(r"[?？]", scope)
            or a["source_text"][matches[0].end():].lstrip().startswith(("?", "？"))):
        raise ContextError("observed assertion is not explicit user event/state evidence; retain unknown and confirm")


def guard_current_fact(record, records):
    if record["kind"] != "fact" or record["status"] == "unknown":
        return
    current = record["layer"] in {"profile", "longterm"} or record["supersedes"]
    text = record["text"]
    if record.get("assertion", {}).get("state") == "observed":
        text, _ = source_view(text)  # validate_assertion already checked the full evidence.
    if current and noncurrent(text):
        raise ContextError("future/uncertain text cannot replace current facts; retain unknown and use daily")
    replaces_unknown = any(records.get(rid, {}).get("status") == "unknown"
                           for rid in record["supersedes"])
    if replaces_unknown and ("assertion" not in record
            or record["assertion"]["state"] != "observed"):
        raise ContextError("replacing an unknown fact needs observed source assertion; save confirmation is not event evidence")


def validate_state(records):
    """Validate the complete historical graph before rendering or writing."""
    for rid, r in records.items():
        validate(r, incoming=False)
        if r["id"] != rid:
            raise ContextError("record key and id disagree")
        target = r["superseded_by"]
        if target:
            newer = records.get(target)
            if not newer or rid not in newer["supersedes"]:
                raise ContextError("replacement target or reverse reference is missing")
            if r["category"] != newer["category"] or r["kind"] != newer["kind"]:
                raise ContextError("replacement changed category or type")
        for oldid in r["supersedes"]:
            old = records.get(oldid)
            if not old or old["superseded_by"] != rid:
                raise ContextError("supersedes reference disagrees with history")
        seen = {rid}
        while target:
            if target in seen:
                raise ContextError("replacement history contains a cycle")
            seen.add(target)
            target = records[target]["superseded_by"]


def render(records):
    lines = [START, "", "## 已整理条目", ""]
    for r in records:
        lines += ["### " + r["id"] + " · " + CATEGORIES[r["category"]],
                  "", html.escape(r["text"]), "",
                  "- 类型：" + KINDS[r["kind"]] + "；状态：" + STATUS[r["status"]],
                  "- 事件日期：" + (r["event_date"] or "未知")
                  + "；记录日期：" + r["record_date"]
                  + "；生效日期：" + (r["valid_from"] or "未知"),
                  "- 确认日期：" + (r["confirmed_on"] or "未确认")
                  + "；依据：" + html.escape(r["confirmation"] or "无")]
        lines += ["- 来源：" + html.escape(s["label"]) + " · " + html.escape(s["locator"])
                  for s in r["sources"]]
        if "assertion" in r:
            a = r["assertion"]
            lines += ["- 来源事件状态：" + a["state"] + "；作者角色：" + a["author_role"],
                      "- 事件依据原文：" + html.escape(a["source_text"])]
        if "original_status" in r:
            lines += ["- 替代前状态：" + STATUS[r["original_status"]]]
        if r["superseded_by"]:
            lines += ["- 当前使用替代条目：" + r["superseded_by"]]
        if r["supersedes"]:
            lines += ["- 替代旧条目：" + ", ".join(r["supersedes"])]
        lines += [""]
    raw = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = raw.replace("<", r"\u003c").replace(">", r"\u003e")
    lines += [DATA + raw + " -->", END]
    return "\n".join(lines)


def parse(text):
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        raise ContextError("managed block missing or duplicated; preserve the file and reconcile manually")
    a, b = text.index(START), text.index(END) + len(END)
    block = text[a:b]
    match = re.search(re.escape(DATA) + r"(.*?) -->", block)
    if not match:
        raise ContextError("record metadata missing")
    try:
        records = json.loads(match.group(1))
        if not isinstance(records, list):
            raise ValueError()
        for r in records:
            validate(r, incoming=False)
    except (ValueError, TypeError) as e:
        raise ContextError("invalid managed metadata: " + str(e))
    if render(records) != block:
        raise ContextError("user changed managed block; refusing to overwrite; reconcile it first")
    return records, text[:a], text[b:]


def rel_for(record):
    return {"profile": "PERSON_PROFILE.md", "longterm": "LONG_TERM.md"}.get(
        record["layer"], "daily/" + record["record_date"] + ".md")


def inventory(root):
    marker = safe_path(root, ".context/format.json")
    if not marker.is_file() or json.loads(marker.read_text()) != {"version": 1}:
        raise ContextError("initialize an empty private context directory first")
    txbase = safe_path(root, ".context/transactions")
    if txbase.exists():
        for journal in txbase.glob("*/journal.json"):
            safe_path(root, str(journal.relative_to(root)))
            if json.loads(journal.read_text())["status"] == "pending":
                raise ContextError("unfinished transaction; use recover before continuing")
    files = {}
    for name in FIXED:
        p = safe_path(root, name)
        if not p.is_file():
            raise ContextError("missing initialized file: " + name)
        files[name] = p.read_bytes()
    daily = safe_path(root, "daily")
    if daily.exists():
        for p in sorted(daily.iterdir()):
            safe_path(root, str(p.relative_to(root)))
            if p.is_file() and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", p.name):
                files[str(p.relative_to(root))] = p.read_bytes()
    return files


def semantic(record):
    norm = " ".join(unicodedata.normalize("NFKC", record["text"]).split()).casefold()
    # Separate dated events and statements of different certainty.
    key = (record["category"], record["layer"], record["kind"], record["status"], norm,
           record["event_date"], record["valid_from"], tuple(record["supersedes"]))
    a = record.get("assertion")
    # Source references are corroboration, not a second occurrence of the event.
    key += ((a["state"], a["author_role"]) if a else (None, None),)
    return key + ((record["record_date"],) if record["layer"] == "daily" else ())


def historical_replay(old, new):
    """An old confirmation stays historical; new evidence alone needs review."""
    if old["status"] != "superseded":
        return False
    original_status = old.get("original_status")
    if original_status is None:
        # Legacy history omitted this field; only positive confirmation is unambiguous.
        if not old["confirmed_on"] or not old["confirmation"]:
            return False
        original_status = "confirmed"
    previous = dict(old, status=original_status)
    # History may have merged sources. semantic handles record_date for daily only.
    if (semantic(previous) != semantic(new)
            or any(previous[field] != new[field] for field in ("confirmed_on", "confirmation"))):
        return False
    if any(source not in previous["sources"] for source in new["sources"]):
        raise ContextError("historical statement has new sources; review " + old["id"]
                           + " before treating it as current; new evidence is not a new confirmation")
    return True


def make_plan(root, bundle):
    if (not isinstance(bundle, dict)
            or set(bundle) not in ({"version", "records"}, {"version", "records", "as_of"})
            or bundle["version"] != 1):
        raise ContextError("bundle must contain version=1, records and optional as_of")
    as_of = bundle.get("as_of")
    if "as_of" in bundle:
        day(as_of, "as_of", nullable=False)
    if not isinstance(bundle["records"], list) or len(bundle["records"]) > 100:
        raise ContextError("records must be a list of at most 100 items")
    files = inventory(root)
    contexts, records = {}, {}
    for rel, content in files.items():
        if rel in {"INDEX.md", "SOURCES.md"}:
            continue
        parsed, prefix, suffix = parse(content.decode("utf-8"))
        contexts[rel] = [parsed, prefix, suffix]
        for r in parsed:
            if r["id"] in records or rel_for(r) != rel:
                raise ContextError("duplicate id or a record stored in the wrong layer")
            records[r["id"]] = r
    validate_state(records)
    outcome = []
    seen_input = set()
    for original in bundle["records"]:
        validate(original)
        if original["valid_from"] is not None:
            if as_of is None:
                raise ContextError("dated changes need explicit caller as_of; do not infer today")
            if original["valid_from"] > as_of and (original["supersedes"]
                    or original["layer"] != "daily"):
                raise ContextError("future-effective changes must be daily plans without supersedes")
        new = copy.deepcopy(original)
        new["superseded_by"] = None
        rid = new["id"]
        if rid in seen_input:
            raise ContextError("duplicate input id")
        seen_input.add(rid)
        if rid in records:
            existing = records[rid]
            comparison = copy.deepcopy(existing)
            comparison["sources"] = new["sources"]
            historical = existing["status"] == "superseded"
            if historical:
                comparison["status"] = comparison.pop("original_status", new["status"])
                comparison["superseded_by"] = None
            if comparison != new:
                raise ContextError("existing id differs; use a new confirmed id with supersedes")
            for source in new["sources"]:
                if source not in existing["sources"]:
                    existing["sources"].append(source)
            validate(existing, incoming=False)
            outcome.append({"id": rid, "status": "historical_duplicate" if historical else "duplicate"})
            continue
        past = next((x for x in records.values() if historical_replay(x, new)), None)
        if past:
            outcome.append({"id": rid, "status": "historical_duplicate", "into": past["id"]})
            continue
        same = next((x for x in records.values() if semantic(x) == semantic(new)), None)
        if same:
            for source in new["sources"]:
                if source not in same["sources"]:
                    same["sources"].append(source)
            validate(same, incoming=False)
            outcome.append({"id": rid, "status": "merged", "into": same["id"]})
            continue
        guard_current_fact(new, records)
        for oldid in new["supersedes"]:
            old = records.get(oldid)
            if old is None or old["status"] == "superseded":
                raise ContextError("replacement target missing or already superseded")
            if old["category"] != new["category"] or old["kind"] != new["kind"]:
                raise ContextError("replacement must retain category and fact/inference/advice type")
            old["original_status"] = old["status"]
            old["status"], old["superseded_by"] = "superseded", rid
        rel = rel_for(new)
        safe_path(root, rel)
        if rel not in contexts:
            template = (TEMPLATES / "DAILY_LOG.md").read_text().replace("[日期]", new["record_date"])
            parsed, prefix, suffix = parse(template)
            contexts[rel] = [parsed, prefix, suffix]
        contexts[rel][0].append(new)
        records[rid] = new
        outcome.append({"id": rid, "status": "added"})
    validate_state(records)
    changes = {}
    for rel, (items, prefix, suffix) in contexts.items():
        updated = (prefix + render(items) + suffix).encode()
        # Final rendered Markdown must be parseable before it reaches apply.
        parse(updated.decode("utf-8"))
        if updated != files.get(rel):
            changes[rel] = updated.decode()
    return {"version": 1, "expected": {p: sha(b) for p, b in files.items()},
            "bundle": bundle, "changes": changes, "outcome": outcome}


def recover(root, tid):
    if not re.fullmatch(r"[0-9a-f]{32}", tid):
        raise ContextError("invalid transaction id")
    base = safe_path(root, ".context/transactions/" + tid)
    journal = safe_path(root, str((base / "journal.json").relative_to(root)))
    state = json.loads(journal.read_text())
    if state["status"] != "pending":
        return {"status": state["status"], "transaction": tid}
    conflicts = []
    for rel, item in state["files"].items():
        p = safe_path(root, rel)
        backup = safe_path(root, ".context/transactions/" + tid + "/before/" + rel)
        if item["before"] is not None and (not backup.is_file() or sha(backup.read_bytes()) != item["before"]):
            raise ContextError("backup is missing or changed; inspect transaction manually")
        current = sha(p.read_bytes()) if p.exists() else None
        if current == item["before"]:
            continue
        if current != item["after"]:
            conflicts.append(rel)
            continue
        if item["before"] is None:
            p.unlink()
        else:
            atomic_write(p, backup.read_bytes())
    if conflicts:
        raise ContextError("recovery preserved human changes; resolve these files: " + ", ".join(conflicts))
    state["status"] = "rolled_back"
    atomic_write(journal, encode(state))
    return {"status": "rolled_back", "transaction": tid}


def apply_plan(root, plan):
    with lock(root):
        if not isinstance(plan, dict) or set(plan) != {"version", "expected", "bundle", "changes", "outcome"}:
            raise ContextError("invalid plan")
        actual = inventory(root)
        if {p: sha(b) for p, b in actual.items()} != plan["expected"]:
            raise ContextError("file conflict since planning; re-read and create a new plan")
        if make_plan(root, plan["bundle"]) != plan:
            raise ContextError("plan was modified; regenerate it from the bundle")
        if not plan["changes"]:
            return {"status": "no_change", "outcome": plan["outcome"]}
        tid = uuid.uuid4().hex
        base = safe_path(root, ".context/transactions/" + tid)
        base.mkdir(mode=0o700, parents=True)
        state = {"status": "pending", "files": {}}
        for rel, text in plan["changes"].items():
            p = safe_path(root, rel)
            old = actual.get(rel)
            state["files"][rel] = {"before": sha(old) if old is not None else None,
                                   "after": sha(text.encode())}
            if old is not None:
                create_new(base / "before" / rel, old)
        journal = base / "journal.json"
        create_new(journal, encode(state))
        try:
            # Recheck the whole read set after durable backups, before the first write.
            current = {rel: sha(safe_path(root, rel).read_bytes()) for rel in actual}
            if current != plan["expected"]:
                raise ContextError("file conflict while preparing transaction")
            for rel, text in plan["changes"].items():
                p = safe_path(root, rel)
                current_sha = sha(p.read_bytes()) if p.exists() else None
                if current_sha != state["files"][rel]["before"]:
                    raise ContextError("file conflict immediately before write: " + rel)
                atomic_write(p, text.encode())
                if p.read_bytes() != text.encode():
                    raise ContextError("readback failed: " + rel)
            state["status"] = "committed"
            atomic_write(journal, encode(state))
        except Exception:
            try:
                recover(root, tid)
            except Exception:
                raise ContextError("transaction incomplete; inspect .context/transactions/" + tid
                                   + " and use recover; human edits will be retained")
            raise
        return {"status": "committed", "transaction": tid,
                "changed_files": list(plan["changes"]), "outcome": plan["outcome"]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    planner = sub.add_parser("plan")
    planner.add_argument("--input", required=True)
    planner.add_argument("--output", required=True)
    writer = sub.add_parser("apply")
    writer.add_argument("--plan", required=True)
    repair = sub.add_parser("recover")
    repair.add_argument("--transaction", required=True)
    args = p.parse_args(argv)
    try:
        root = check_root(args.root)
        if args.command == "init":
            result = initialize(root)
        elif args.command == "plan":
            with lock(root):
                result = make_plan(root, json.loads(Path(args.input).read_text()))
                # Plans include the chosen private data. Never put them in the public package.
                out = Path(args.output)
                if not out.is_absolute() or out.is_symlink():
                    raise ContextError("--output must be a new absolute private file")
                check_root(out.parent)
                safe_path(out.parent, out.name)
                create_new(out, encode(result))
            result = {"status": "planned", "changed_files": list(result["changes"]),
                      "outcome": result["outcome"]}
        elif args.command == "apply":
            result = apply_plan(root, json.loads(Path(args.plan).read_text()))
        else:
            with lock(root):
                result = recover(root, args.transaction)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ContextError, OSError, ValueError, KeyError, TypeError) as e:
        print("context_store: " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
