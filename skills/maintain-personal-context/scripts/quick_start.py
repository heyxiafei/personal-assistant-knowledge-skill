#!/usr/bin/env python3
"""Bounded quick-start projection/reducer for already reviewed first_run annotations.

The Agent reads only authorized, readable sources before calling start; this helper
does not discover accounts, scan files, extract prose, show native UI or save facts.
It reuses interview's source reconciliation, exact-plan save and private checkpoint
guards. All functions return copies; CLI prints a preview without creating files.
6–8 questions / 3–5 minutes is a design target, not measured user performance.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import first_run
import interview

TOPICS = (
    ("situation", "当前现状", ("current_situation", "current_problem")),
    ("goal", "近期目标", ("near_term_goal", "current_problem")),
    ("collaboration", "本轮协作方式", ("collaboration_preference",)),
    ("constraints", "当前约束", ("time_resources",)),
    ("identity", "简介或称呼", ("introduction", "name")),
    ("capability", "经历与能力", ("skills", "representative_experience")),
    ("resources", "已有资源", ("resources",)),
    ("boundaries", "本轮协作边界", ("assistant_boundaries",)),
)
DEFAULTS = {
    "situation": ("有正在推进的项目", "目前没有固定项目", "本轮先梳理现状"),
    "goal": ("本轮先整理现状", "本轮先选一个近期目标"),
    "collaboration": ("本轮先给简短建议", "本轮先比较选项", "本轮先听我补充"),
    "constraints": ("当前时间有限，先做很小的一步", "当前约束需要边做边确认"),
    "identity": ("目前主要在学习或探索", "目前有固定工作", "目前自由职业或经营项目"),
    "capability": ("正在积累经验，暂不填写具体能力", "有过往经历，具体作品稍后补充"),
    "resources": ("先使用当前已有工具", "当前可用资源仍需核对"),
    "boundaries": ("本轮先提供建议，执行动作逐项核对授权", "本轮先整理普通资料，敏感细节另行说明"),
}
META = "_quick_start"
Error = interview.InterviewError
DATA_FIELDS = {"entry", "claims", "source_review", "recommendations", "coverage_gaps", "current_project_keys", META}
CLAIM_FIELDS = {"key", "value", "source_id", "recorded_at", "source_kind", "author_role", "evidence_tier",
                "correction_of", "project_key", "event_date", "effective_date", "confirmation_date",
                "human_origin_verified", "origin_evidence"}
RECEIPT_FIELDS = {"role", "status", "scope", "reason", "source_ids", "source_id", "limitations",
                  "continuation_available", "discovered_sources", "read_check"}
CHECK_FIELDS = {"scope_kind", "source_kind", "reader_kind", "requested_message_ids", "returned_message_ids",
                "content_omitted", "truncated", "previous_cursor", "next_cursor", "returned_messages",
                "continuation_available", "status_origin", "read_version", "latest_version", "target_completeness"}
SESSION_FIELDS = {"schema_version", "kind", "revision", "phase", "data", "decisions", "submissions",
                  "advice_needs_refresh", "checkpoint_consent", "confirmation", "receipt",
                  "resumed_confirmation_invalidated"}


def minimal_data(data):
    """Keep canonical annotations and read metadata, never unused history bodies."""
    result = {k: copy.deepcopy(v) for k, v in data.items() if k in DATA_FIELDS}
    result["claims"] = [{k: copy.deepcopy(v) for k, v in c.items() if k in CLAIM_FIELDS} for c in data["claims"]]
    result["entry"] = {k: copy.deepcopy(v) for k, v in data["entry"].items() if k in {"status", "url", "reason"}}
    if "recommendations" in result:
        advice = []
        for item in result["recommendations"]:
            row = {k: copy.deepcopy(v) for k, v in item.items()
                   if k in {"label", "basis_keys", "first_step", "confirmation_condition", "scope", "project_context"}}
            if isinstance(row.get("project_context"), dict):
                row["project_context"] = {k: copy.deepcopy(v) for k, v in row["project_context"].items()
                                          if k in {"project_key", "status", "source_ids", "action_type",
                                                   "allowed_actions", "restart_authorization"}}
                context = row["project_context"]
                if isinstance(context.get("restart_authorization"), dict):
                    context["restart_authorization"] = {k: copy.deepcopy(v) for k, v in context["restart_authorization"].items()
                                                         if k in {"claim_key", "source_id"}}
            advice.append(row)
        result["recommendations"] = advice

    def receipt(row):
        projected = {k: copy.deepcopy(v) for k, v in row.items() if k in RECEIPT_FIELDS}
        if isinstance(row.get("read_check"), dict):
            check = {k: copy.deepcopy(v) for k, v in row["read_check"].items() if k in CHECK_FIELDS}
            if "returned_messages" in check:
                check["returned_messages"] = [{k: copy.deepcopy(v) for k, v in m.items()
                                               if k in {"id", "content_omitted", "truncated"}}
                                              for m in check["returned_messages"]]
            projected["read_check"] = check
        if "discovered_sources" in row:
            projected["discovered_sources"] = [receipt(r) for r in row["discovered_sources"]]
        return projected

    result["source_review"] = [receipt(r) for r in data["source_review"]]
    return result


def exclude_topic(session, topic_id):
    """Remove the excluded topic's body from this state; leave old files alone."""
    keys = list(next(keys for topic, _, keys in TOPICS if topic == topic_id))
    claims = session["data"]["claims"]
    # One reviewed statement may have several canonical projections. Follow
    # exact source identity and body rather than deleting unrelated text matches.
    # Source kind is part of identity: different readers can reuse a source ID.
    signatures = {(c["source_kind"], c["source_id"], c["value"]) for c in claims if c["key"] in keys}
    keys = list(dict.fromkeys(keys + [c["key"] for c in claims
                                   if (c["source_kind"], c["source_id"], c["value"]) in signatures]))
    # Keep the existing whole-key exclusion contract, including its history;
    # checked/checkpoint/supplement all guard these same affected canonical keys.
    removed = [c["value"] for c in claims if c["key"] in keys]
    session["data"]["claims"] = [c for c in claims if c["key"] not in keys]
    session["data"]["recommendations"] = []
    session["advice_needs_refresh"] = True
    meta = session["data"][META]
    meta["excluded_keys"] = list(dict.fromkeys(meta["excluded_keys"] + list(keys)))
    # Only free prose can quote a body. Never replace structural enums, keys,
    # source IDs or tokens: a perfectly valid answer can itself be "read".
    def redact(value):
        for body in sorted(set(removed), key=len, reverse=True):
            value = value.replace(body, "[本轮已排除正文]")
        return value

    def receipt(row):
        for field in ("scope", "reason"):
            if field in row:
                row[field] = redact(row[field])
        if "limitations" in row:
            row["limitations"] = [redact(v) for v in row["limitations"]]
        for child in row.get("discovered_sources", []):
            receipt(child)

    for row in session["data"]["source_review"]:
        receipt(row)
    if "coverage_gaps" in session["data"]:
        session["data"]["coverage_gaps"] = [redact(v) for v in session["data"]["coverage_gaps"]]
    if "reason" in session["data"]["entry"]:
        session["data"]["entry"]["reason"] = redact(session["data"]["entry"]["reason"])
    return session


def checked(session):
    interview.checked(session)
    if set(session) - SESSION_FIELDS:
        raise Error("extra checkpoint fields need review; preserve the original rather than dropping or copying them")
    meta = session.get("data", {}).get(META)
    if not isinstance(meta, dict) or meta.get("version") != 1 \
            or type(meta.get("limit")) is not int or not 1 <= meta["limit"] <= 8 \
            or type(meta.get("ended")) is not bool or any(not isinstance(meta.get(k), list)
                for k in ("confirmed_sources", "deferred_topics", "excluded_keys")) \
            or set(meta) != {"version", "limit", "ended", "confirmed_sources", "deferred_topics", "excluded_keys"}:
        raise Error("start or migrate a supported quick-start session first")
    if minimal_data(session["data"]) != session["data"]:
        raise Error("unexpected raw payload in quick-start state; preserve and review it before checkpointing")
    if any(c["key"] in meta["excluded_keys"] for c in session["data"]["claims"]):
        raise Error("excluded content was reintroduced; review before using this state")
    interview.result(session)
    return meta


def start(data, checkpoint_consent=None):
    if META in data:
        raise Error("reserved quick-start metadata; migrate the original session instead")
    return migrate(interview.start(data, checkpoint_consent=checkpoint_consent))


def migrate(session):
    """Keep legacy answers, exclusions and IDs; migration never reactivates consent."""
    interview.checked(session)
    if META in session["data"]:
        checked(session)
        return copy.deepcopy(session)
    if session["phase"] == "saved":
        raise Error("saved sessions stay immutable; start a new maintenance preview")
    new = interview.revised(session)
    new["data"] = minimal_data(new["data"])
    new["data"][META] = {"version": 1, "limit": 8, "ended": False, "confirmed_sources": [],
                         "deferred_topics": [], "excluded_keys": []}
    for topic, _, _ in TOPICS:
        if new["decisions"].get(topic) == "do_not_save":
            exclude_topic(new, topic)
    if session["phase"] == "paused":
        new["phase"] = "paused"
    checked(new)
    return new


def prefilled(session):
    meta = checked(session)
    complete = interview.result(session)["source_review"]["complete_in_checked_scope"]
    confirmed = set(meta["confirmed_sources"])
    facts = {f["key"]: f for f in first_run.reconcile_claims(session["data"]["claims"])["facts"]}
    result = []
    for topic, label, keys in TOPICS:
        selected = [facts[k] for k in keys if k in facts]
        unresolved = [f for f in selected if f["status"] != "known"]
        values = []
        for fact in selected:
            for bucket in fact["values"]:
                if "superseded_by" in bucket:
                    continue
                groups = {}
                for source in bucket["sources"]:
                    authoritative = source["author_role"] == "user" and source["source_kind"] != "memory" \
                        and source["evidence_tier"] in {"direct", "saved"}
                    grade = "user_confirmed" if authoritative and source["source_id"] in confirmed else \
                        "sourced_user" if authoritative else "inferred"
                    groups.setdefault(grade, []).append(source)
                for grade, sources in groups.items():
                    source_dates = []
                    for source in sources:
                        item = {"source_id": source["source_id"], "recorded_at": source.get("recorded_at")}
                        if item not in source_dates:
                            source_dates.append(item)
                    values.append({"key": fact["key"], "value": bucket["value"],
                                   "source_ids": list(dict.fromkeys(source["source_id"] for source in sources)),
                                   "source_dates": source_dates, "evidence": grade,
                                   "current": fact["status"] == "known" and bucket["value"] == fact["current_value"]})
        result.append({"id": topic, "label": label,
                       "state": "excluded" if session["decisions"].get(topic) == "do_not_save" or
                           not selected and any(k in meta["excluded_keys"] for k in keys) else
                           "unresolved" if unresolved else "details_pending" if topic in meta["deferred_topics"] else
                           "known" if selected else
                           "missing" if complete else "source_review_pending",
                       "values": values, "decision": session["decisions"].get(topic)})
    return result


def used(session):
    return sum(topic in session["decisions"] for topic, _, _ in TOPICS)


def question(session):
    meta = checked(session)
    coverage = interview.result(session)["source_review"]
    if session["phase"] != "preview" or meta["ended"] or used(session) >= meta["limit"] \
            or coverage["continuation_roles"]:
        return None
    rows = prefilled(session)
    # A conflict is more useful to settle than adding optional profile details.
    candidates = [r for r in rows if not r["decision"] and r["state"] == "unresolved"]
    if not coverage["blocked_roles"]:
        candidates += [r for r in rows if not r["decision"] and r["state"] == "missing"]
    if not candidates:
        return None
    row = candidates[0]
    keys = next(keys for topic, _, keys in TOPICS if topic == row["id"])
    facts = {f["key"]: f for f in first_run.reconcile_claims(session["data"]["claims"])["facts"]}
    key = next((k for k in keys if k in facts and facts[k]["status"] != "known"), keys[0])
    values = [v for v in row["values"] if v["key"] == key]
    choices, seen = [], set()
    for value in values:
        if value["value"] in seen:
            continue
        seen.add(value["value"])
        choices.append({"id": "c-" + interview.token({"key": key, "value": value["value"]})[:16],
                        "label": value["value"], "source_ids": list(dict.fromkeys(
                            sid for v in values if v["value"] == value["value"] for sid in v["source_ids"]))})
    if not choices:
        choices = [{"id": "g" + str(i), "label": label, "source_ids": [],
                    "needs_details": row["id"] in {"capability", "resources"} or row["id"] == "constraints" and i == 2}
                   for i, label in enumerate(DEFAULTS[row["id"]], 1)]
    return {"id": row["id"], "key": key, "title": "只核对" + row["label"] if values else "只补充" + row["label"],
            "reason": "unresolved" if values else "missing_in_checked_scope", "choices": choices[:3],
            "actions": ["other", "skip", "uncertain", "do_not_save", "pause", "finish"],
            "free_text": True, "default_choice": None,
            "notice": "选项只记录你的实际回答；本轮偏好不扩大权限，不构成人格判断或保存授权。"}


def render_question(session):
    q = question(session)
    if q is None:
        return "当前没有待答的快速题；可以使用初稿，或按需处理仍待核的缺口。"
    lines = [q["title"] + "（" + q["reason"] + "）"]
    lines += [chr(65 + i) + ". " + first_run.literal(c["label"]) for i, c in enumerate(q["choices"])]
    lines += ["其他：直接补充一句话；也可回复：跳过／不确定／这项不保存／暂停／先用初稿。", q["notice"]]
    return "\n".join(lines)


def followups(session):
    """Optional backlog, never a requirement for using or saving the current draft."""
    return [{"id": row["id"], "reason": row["state"], "decision": row["decision"],
             "optional": True} for row in prefilled(session) if row["state"] not in {"known", "excluded"}]


def draft(session, *, target=None, reviewed_plan=None):
    checked(session)
    view = interview.draft(session, target=target, reviewed_plan=reviewed_plan)
    view.update(question=question(session), prefilled=prefilled(session), followups=followups(session),
                progress={"processed": used(session), "limit": checked(session)["limit"],
                          "ended": checked(session)["ended"], "usable_now": True},
                text_question=render_question(session), knowledge_base_updated=session["phase"] == "saved")
    lines = ["\n## 快速入门预填核对\n", "以下来自本次已读标注；已知不重问，整体认可不自动确认推测。"]
    labels = {"sourced_user": "已有用户资料", "user_confirmed": "本轮用户确认", "inferred": "摘要、第三方或推测待核"}
    for row in view["prefilled"]:
        lines.append("- " + row["label"] + "：" + ("；".join(first_run.literal(v["value"]) + "（" + labels[v["evidence"]]
                     + "；来源：" + "、".join(first_run.literal(s) for s in v["source_ids"])
                     + "；记录时间：" + "、".join(first_run.literal(str(s["recorded_at"])) if s["recorded_at"] else "未知"
                                                  for s in v["source_dates"]) + "）"
                     for v in row["values"]) or ("本轮排除，不保存正文" if row["state"] == "excluded" else
                                                "来源仍待核对，不能判作个人信息缺失" if row["state"] == "source_review_pending"
                                                else "本次已读范围未知")))
    lines += ["", "快速轮最多 8 题，可少问或零题；6–8 题、3–5 分钟为尚待真人验证的设计目标。",
              "当前初稿可立即使用，仍缺或待核项可稍后选做深入访谈。", "", view["text_question"]]
    view["markdown"] += "\n".join(lines) + "\n"
    return view


def respond(session, topic_id, action, submission_id, *, value=None, choice_id=None,
            source_id=None, recorded_at=None, correction_of=None, author_role="user"):
    checked(session)
    if action not in {"answer", "choice", "other", "skip", "uncertain", "do_not_save"}:
        raise Error("unknown quick-start reply action")
    sid = interview.text(submission_id, "submission identity")
    payload = {"topic": topic_id, "action": action}
    is_answer = action in {"answer", "choice", "other"}
    if is_answer:
        payload.update(value=value if action != "choice" else None, choice_id=choice_id,
                       source_id=source_id, recorded_at=recorded_at, correction_of=correction_of, author_role=author_role)
    digest = interview.token(payload)
    if sid in session["submissions"]:
        if session["submissions"][sid] != digest:
            raise Error("submission identity reused with different content")
        return copy.deepcopy(session)
    q = question(session)
    if q is None or q["id"] != topic_id:
        raise Error("reply must match the one current quick-start question")
    needs_details = False
    if action == "choice":
        selected = next((c for c in q["choices"] if c["id"] == choice_id), None)
        if selected is None:
            raise Error("choice must match a current offered option")
        value = selected["label"]
        needs_details = selected.get("needs_details", False)
    new = interview.add_claim(session, q["key"], value, source_id, recorded_at, correction_of, author_role) \
        if is_answer else interview.revised(session)
    if is_answer and author_role == "user":
        new["data"][META]["confirmed_sources"] = list(dict.fromkeys(
            new["data"][META]["confirmed_sources"] + [source_id]))
    new["decisions"][topic_id] = action
    new["submissions"][sid] = digest
    if needs_details and topic_id not in new["data"][META]["deferred_topics"]:
        new["data"][META]["deferred_topics"].append(topic_id)
    if action == "do_not_save":
        exclude_topic(new, topic_id)
    checked(new)
    return new


def supplement(session, claims):
    """Agent-reviewed extra replies can fill several topics without asking again."""
    checked(session)
    if not isinstance(claims, list):
        raise Error("supplement needs reviewed claims")
    if any(c.get("key") in checked(session)["excluded_keys"] for c in claims if isinstance(c, dict)):
        raise Error("excluded topic needs a new explicit request before accepting its content")
    claims = minimal_data(dict(session["data"], claims=claims))["claims"]
    # Validate conflicting reuse of a source identity before changing state.
    # Repeating an already reviewed scan/reply must not create a new revision.
    first_run.reconcile_claims(session["data"]["claims"] + claims)
    additions = []
    for claim in claims:
        if claim not in session["data"]["claims"] and claim not in additions:
            additions.append(claim)
    if not additions:
        return copy.deepcopy(session)
    new = interview.revised(session)
    new["data"]["claims"].extend(copy.deepcopy(additions))
    new["data"]["recommendations"] = []
    new["advice_needs_refresh"] = True
    new["data"][META]["confirmed_sources"] = list(dict.fromkeys(new["data"][META]["confirmed_sources"] + [
        c["source_id"] for c in additions if c["author_role"] == "user" and c["source_kind"] == "conversation"
        and c["evidence_tier"] == "direct"]))
    for topic, _, keys in TOPICS:
        if topic in new["data"][META]["deferred_topics"] and any(
                c["key"] in keys and c["author_role"] == "user" and c["source_kind"] != "memory"
                and c["evidence_tier"] in {"direct", "saved"} and c["value"] not in DEFAULTS[topic] for c in additions):
            new["data"][META]["deferred_topics"].remove(topic)
    if session["phase"] == "paused":
        new["phase"] = "paused"
    checked(new)
    return new


def import_decisions(session, decisions):
    """Carry Agent-reviewed legacy control replies after bundle validation.

    Each row has topic_id/action/submission_id/source_id. The Agent supplies the
    semantic question-to-topic mapping and actual reply locator; no mapping or
    source identity is inferred. Answers use reviewed claims plus supplement.
    """
    checked(session)
    if not isinstance(decisions, list):
        raise Error("reviewed control decisions must be a list")
    staged_decisions = copy.deepcopy(session["decisions"])
    staged_submissions = copy.deepcopy(session["submissions"])
    changes = []
    for row in decisions:
        if not isinstance(row, dict) or set(row) != {"topic_id", "action", "submission_id", "source_id"}:
            raise Error("control migration needs topic, action, submission identity and actual source")
        topic, action = row["topic_id"], row["action"]
        if topic not in {t[0] for t in TOPICS} or action not in {"skip", "uncertain", "do_not_save"}:
            raise Error("only reviewed skip, uncertain and exclusion controls can be imported")
        sid = interview.text(row["submission_id"], "submission identity")
        interview.text(row["source_id"], "actual legacy reply source")
        digest = interview.token(row)
        if sid in staged_submissions:
            if staged_submissions[sid] != digest:
                raise Error("submission identity reused with different content")
            continue
        if topic in staged_decisions and staged_decisions[topic] != action:
            raise Error("legacy control conflicts with this session's decision; review before replacing")
        staged_decisions[topic] = action
        staged_submissions[sid] = digest
        changes.append(row)
    if not changes:
        return copy.deepcopy(session)
    new = interview.revised(session)
    new["decisions"], new["submissions"] = staged_decisions, staged_submissions
    for row in changes:
        if row["action"] == "do_not_save":
            exclude_topic(new, row["topic_id"])
    if session["phase"] == "paused":
        new["phase"] = "paused"
    checked(new)
    return new


def pause(session):
    checked(session)
    if session["phase"] == "paused":
        return copy.deepcopy(session)
    new = interview.revised(session)
    new["phase"] = "paused"
    return new


def resume(session):
    checked(session)
    return interview.revised(session) if session["phase"] == "paused" else copy.deepcopy(session)


def finish(session):
    checked(session)
    if checked(session)["ended"]:
        return copy.deepcopy(session)
    new = interview.revised(session)
    new["data"][META]["ended"] = True
    return new


def checkpoint(path, session, expected_hash=None):
    checked(session)
    return interview.checkpoint(path, session, expected_hash=expected_hash)


def load_checkpoint(path):
    session, digest = interview.load_checkpoint(path)
    return migrate(session), digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="already reviewed annotations; no implicit reads")
    parser.add_argument("--case", help="select a named synthetic fixture case")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.case:
        data = data[args.case]
    print(json.dumps(draft(start(data)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
