#!/usr/bin/env python3
"""Offline first-run formatter for Agent-reviewed annotations; no source access or writes."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit

from native_history import reconcile_claims
from source_checks import ReadCheckError, check_read


class ResultError(ValueError):
    pass


IDENTITY = (("name", "称呼"), ("introduction", "简介"),
            ("skills", "技能或经历"), ("resources", "资源与协作"))
LABELS = dict(IDENTITY, current_problem="当前问题", representative_experience="代表性经历", time_resources="可用时间资源")
GAPS = (
    ("current_problem", "说一个你现在最想解决的问题", "帮助助手选准本轮优先问题",
     "口述或粘贴一句话：我现在想解决……，完成时希望看到……"),
    ("representative_experience", "提供一个有代表性的经历或作品片段", "让能力判断有具体依据",
     "口述你做过什么、负责哪部分，或粘贴一段允许整理的作品说明"),
    ("time_resources", "说一下这件事可用的时间和资源", "把建议缩成现在能做的一步",
     "口述或粘贴大致可用时间、已有工具或可求助的人；无需敏感明细"),
)

SOURCE_ROLES = {
    "personal_profile": "已有个人主档", "workbench": "已有工作台",
    "materials_index": "资料与项目索引", "related_memory": "相关定向记忆",
}
SOURCE_STATES = {"read", "not_found_in_checked_scope", "discovered_unread",
                 "partial", "stale", "entry_missing", "entry_unavailable",
                 "permission_denied", "failed", "user_excluded",
                 "experiment_excluded", "not_attempted"}
BLOCKED_SOURCE_STATES = {"entry_missing", "entry_unavailable", "permission_denied",
                         "user_excluded", "experiment_excluded"}
PROJECT_STATES = {"planned", "active", "completed", "paused", "cancelled", "unknown"}
PROJECT_ACTIONS = {"advance", "review", "archive", "clarify", "resume", "reopen"}


def review_sources(data):
    """Check Agent-supplied source review, not live access or interpretation truth."""
    review = data.get("source_review")
    if not isinstance(review, list):
        raise ResultError("source_review required before asking for personal details")
    by_role = {}
    for item in review:
        if not isinstance(item, dict) or not isinstance(item.get("role"), str) \
                or not isinstance(item.get("status"), str) or item["role"] not in SOURCE_ROLES \
                or item["status"] not in SOURCE_STATES:
            raise ResultError("source review role/status invalid")
        role = item["role"]
        if role in by_role:
            raise ResultError("duplicate source review role")
        try:
            row = check_read(item)
        except ReadCheckError as exc:
            raise ResultError(str(exc)) from exc
        row["own_status"] = row["status"]
        row["scope"] = nonempty(row.get("scope"), "checked authorization scope")
        if row["status"] == "read":
            ids = row.get("source_ids")
            if not isinstance(ids, list) or not ids or any(not isinstance(s, str) or not s.strip() for s in ids):
                raise ResultError("read sources need actual source identifiers")
            row["source_ids"] = list(dict.fromkeys(ids))
        else:
            row["reason"] = nonempty(row.get("reason"), "source gap reason")
        discovered = row.get("discovered_sources", [])
        if not isinstance(discovered, list):
            raise ResultError("discovered sources must be a list")
        seen, checked, gaps = set(), [], []
        for source in discovered:
            if not isinstance(source, dict) or not isinstance(source.get("status"), str) \
                    or source["status"] not in SOURCE_STATES - {"not_found_in_checked_scope"}:
                raise ResultError("discovered source status invalid")
            try:
                source = check_read(source)
            except ReadCheckError as exc:
                raise ResultError(str(exc)) from exc
            sid = nonempty(source.get("source_id"), "discovered source identifier")
            if sid in seen:
                raise ResultError("duplicate discovered source")
            seen.add(sid)
            source["source_id"] = sid
            if source["status"] == "read":
                ids = row.get("source_ids")
                if not isinstance(ids, list) or sid not in ids:
                    raise ResultError("read discovered source absent from source_ids")
            else:
                source["reason"] = nonempty(source.get("reason"), "discovered source gap")
                gaps.append(sid + "：" + source["status"] + " / " + source["reason"])
            checked.append(source)
        if checked:
            if row["status"] == "not_found_in_checked_scope":
                raise ResultError("found sources cannot be marked not found")
            row["discovered_sources"] = checked
        if gaps and row["status"] == "read":
            row.update(status="partial", reason="已发现相关源尚待核对：" + "；".join(gaps))
        by_role[role] = row
    if set(by_role) != set(SOURCE_ROLES):
        raise ResultError("review existing profile, workbench, index and related memory")
    pending = [role for role, item in by_role.items()
               if item["status"] not in ("read", "not_found_in_checked_scope")]
    blocked = []
    for role in pending:
        item = by_role[role]
        own_continuation = item["own_status"] not in {"read", "not_found_in_checked_scope"} \
            and item["own_status"] not in BLOCKED_SOURCE_STATES and item.get("continuation_available") is not False
        child_continuation = any(s["status"] != "read" and s["status"] not in BLOCKED_SOURCE_STATES
                                 and s.get("continuation_available") is not False
                                 for s in item.get("discovered_sources", []))
        if not own_continuation and not child_continuation:
            blocked.append(role)
    return {"items": list(by_role.values()), "complete_in_checked_scope": not pending,
            "pending_roles": pending, "blocked_roles": blocked,
            "continuation_roles": [role for role in pending if role not in blocked]}


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ResultError(label + " must be nonempty text")
    return value.strip()


def check_action_scope(recommendation, known):
    """Validate source-backed lifecycle annotations, not natural-language meaning.

    Legacy advice stays usable but explicitly unassessed. A new project-scoped
    action must name its own state; unrelated projects never gate general advice.
    """
    scope = recommendation.get("scope")
    context = recommendation.get("project_context")
    if scope is None and context is None:
        return {"scope": "legacy", "state_check": "not_assessed"}
    if scope == "general":
        if context is not None:
            raise ResultError("general advice cannot carry a project context")
        return {"scope": "general", "state_check": "not_applicable"}
    if scope != "project" or not isinstance(context, dict):
        raise ResultError("project advice requires scope=project and project_context")
    key = context.get("project_key")
    if not isinstance(key, str) or key not in known:
        raise ResultError("project_key must cite a known current project state")
    fact = known[key]
    status = context.get("status")
    if not isinstance(status, str) or status not in PROJECT_STATES or fact["current_value"] != status:
        raise ResultError("project status must match its source-backed canonical state claim")
    current = next(v for v in fact["values"] if v["value"] == fact["current_value"])
    sources = current["sources"]
    source_ids = context.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids or any(
            not isinstance(s, str) or s not in {r["source_id"] for r in sources} for s in source_ids):
        raise ResultError("project context requires actual current-state source identifiers")
    if not any(s["source_id"] in source_ids and s["author_role"] == "user"
               and s["source_kind"] != "memory" and s["evidence_tier"] in {"direct", "saved"}
               for s in sources):
        raise ResultError("project state context cannot rely only on a derived source")
    action = context.get("action_type")
    allowed = context.get("allowed_actions")
    if not isinstance(action, str) or action not in PROJECT_ACTIONS \
            or not isinstance(allowed, list) or not allowed or any(
                not isinstance(a, str) or a not in PROJECT_ACTIONS for a in allowed) or action not in allowed:
        raise ResultError("project action must be listed in the reviewed allowed_actions")
    if action == "advance" and status not in {"planned", "active"}:
        raise ResultError("completed, paused, cancelled or unknown projects cannot be advanced")
    authorization = context.get("restart_authorization")
    if action not in {"resume", "reopen"} and authorization is not None:
        raise ResultError("restart authorization is only valid for a resume/reopen action")
    checked_authorization = None
    if action in {"resume", "reopen"}:
        if status not in {"completed", "paused", "cancelled"} or not isinstance(authorization, dict):
            raise ResultError("restart needs a stopped project and a new direct user authorization")
        auth_key = authorization.get("claim_key")
        auth_source = authorization.get("source_id")
        if not isinstance(auth_key, str) or auth_key not in known or known[auth_key]["current_value"] != action:
            raise ResultError("restart authorization must cite the known explicit action")
        auth_value = next(v for v in known[auth_key]["values"] if v["value"] == action)
        direct = [s for s in auth_value["sources"] if s["source_id"] == auth_source
                  and s["author_role"] == "user" and s["source_kind"] == "conversation"
                  and s["evidence_tier"] == "direct" and s.get("project_key") == key]
        if not direct:
            raise ResultError("restart requires direct user evidence scoped to this project")

        def recorded(row):
            try:
                stamp = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
                return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
            except (KeyError, AttributeError, TypeError, ValueError):
                return None

        state_dates = [recorded(s) for s in sources if s["author_role"] == "user"
                       and s["source_kind"] != "memory" and s["evidence_tier"] in {"direct", "saved"}]
        auth_date = recorded(direct[0])
        if auth_date is None or not state_dates or any(d is None or d >= auth_date for d in state_dates):
            raise ResultError("restart authorization must be dated after all current state evidence")
        checked_authorization = {"claim_key": auth_key, "source_id": auth_source,
                                 "recorded_at": direct[0]["recorded_at"]}
    result = {"scope": "project", "state_check": "annotations_checked", "project_context": {
        "project_key": key, "status": status, "action_type": action,
        "allowed_actions": list(dict.fromkeys(allowed)), "source_ids": list(dict.fromkeys(source_ids))}}
    if checked_authorization is not None:
        result["project_context"]["restart_authorization"] = checked_authorization
    return result


def build_result(data, *, interview=False):
    """Validate structure/provenance grade, not raw-text truth or live Page permissions.

    Callers supply already reviewed canonical claims, actual entry status and advice.
    Recommendations never change input claims or become facts.
    interview=True leaves missing-field questions to interview.py, permits a
    source-backed draft without advice, and includes known current constraints.
    """
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        raise ResultError("claims list required")
    source_review = review_sources(data)
    for claim in data["claims"]:
        if not isinstance(claim, dict):
            raise ResultError("claim object required")
        nonempty(claim.get("value"), "claim value")
    reconciled = reconcile_claims(data["claims"])
    known = {f["key"]: f for f in reconciled["facts"] if f["status"] == "known"}
    unresolved = {f["key"]: f["status"] for f in reconciled["facts"] if f["status"] != "known"}

    def evidence(key):
        f = known[key]
        current = next(v for v in f["values"] if v["value"] == f["current_value"])
        return {"key": key, "value": f["current_value"],
                "source_ids": list(dict.fromkeys(s["source_id"] for s in current["sources"])),
                "source_notes": list(dict.fromkeys(s["source_id"] + " / " + (
                    "已存记忆摘要" if s["source_kind"] == "memory" else
                    "用户原话" if s["author_role"] == "user" and s["evidence_tier"] == "direct" else
                    "用户自存资料" if s["author_role"] == "user" and s["evidence_tier"] == "saved" else
                    "助手说法" if s["author_role"] == "assistant" else
                    "第三方说法" if s["author_role"] == "third_party" else "摘要或作者未核实资料"
                ) for s in current["sources"]))}

    entry = data.get("entry")
    if not isinstance(entry, dict) or entry.get("status") not in ("verified", "pending", "unavailable"):
        raise ResultError("actual entry status required")
    entry = dict(entry)
    if entry["status"] == "verified":
        url = nonempty(entry.get("url"), "actual root URL")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password \
                or any(c.isspace() or c in "<>\\[]" for c in url) or parsed.query or parsed.fragment:
            raise ResultError("entry must be a plain HTTPS root URL without credentials/query")
        entry["url"] = url
    else:
        if entry.get("url"):
            raise ResultError("unverified entry cannot present a working root URL")
        entry["reason"] = nonempty(entry.get("reason"), "entry gap")

    supplied = data.get("recommendations", [])
    if not isinstance(supplied, list) or len(supplied) > 3:
        raise ResultError("at most three recommendations")
    actions = []
    for recommendation in supplied:
        if not isinstance(recommendation, dict):
            raise ResultError("recommendation object required")
        keys = recommendation.get("basis_keys")
        if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or k not in known for k in keys):
            raise ResultError("recommendation needs known source-backed basis keys")
        actions.append({"kind": "recommendation", "label": nonempty(recommendation.get("label"), "recommendation"),
                        "basis": [evidence(k) for k in dict.fromkeys(keys)],
                        "first_step": nonempty(recommendation.get("first_step"), "first step"),
                        "confirmation_condition": nonempty(recommendation.get("confirmation_condition"), "confirmation condition"),
                        **check_action_scope(recommendation, known)})
    grounded_actions = list(actions)
    missing = [gap for gap in GAPS if gap[0] not in known]
    # Missing high-value context has priority over extra advice. Total first-round
    # actions stays <=3; do not consume all slots with advice and hide missing inputs.
    reserved = 0 if interview else min(3, len(missing))
    actions = actions[:3 - reserved]
    for key, what, why, how in missing[:reserved]:
        if key == "time_resources" and "resources" in known:
            what = "只补充这件事尚未确认的可用时间"
            how = "口述或粘贴近期实际可用时段；已有工具、渠道和合作记录无需重填"
        actions.append({"kind": "information_action", "key": key, "label": what,
                        "basis": "尚无足够已读依据：" + LABELS[key], "what": what, "why": why,
                        "how": how, "first_step": how,
                        "confirmation_condition": "只整理你愿意提供并允许保存的普通资料"})
    if source_review["continuation_roles"]:
        actions = [{"kind": "source_review_action", "label": "先续读或刷新获准已有资料",
                    "basis": "必要来源尚未读完；不能据此判定个人资料缺失",
                    "first_step": "核对" + "、".join(SOURCE_ROLES[r] for r in source_review["continuation_roles"]),
                    "confirmation_condition": "只用本次已有授权；确定受阻来源保留覆盖缺口，不扩权限或重试拒绝，不要求重填整份档案"}]
    elif source_review["blocked_roles"]:
        # Missing context behind an unavailable entry is a coverage gap, not a
        # request to re-enter the profile or repeatedly try a refused source.
        actions = grounded_actions
    if not actions and not source_review["blocked_roles"] and not interview:
        raise ResultError("known context requires at least one executable recommendation")
    coverage = data.get("coverage_gaps", [])
    if not isinstance(coverage, list) or any(not isinstance(s, str) or not s.strip() for s in coverage):
        raise ResultError("coverage gaps must be text list")
    gaps = [SOURCE_ROLES[item["role"]] + "：" + item["reason"] for item in source_review["items"]
            if item["status"] not in ("read", "not_found_in_checked_scope")]
    for item in source_review["items"]:
        gaps.extend(SOURCE_ROLES[item["role"]] + "：" + limit for limit in item.get("limitations", []))
        for source in item.get("discovered_sources", []):
            gaps.extend(source["source_id"] + "：" + limit for limit in source.get("limitations", []))

    def field_state(key):
        if key in known:
            return "known"
        if key in unresolved:
            return "needs_refresh"
        return "source_review_pending" if not source_review["complete_in_checked_scope"] else "unknown_in_checked_scope"

    doing_keys = data.get("current_project_keys", ["current_situation", "current_problem", "near_term_goal"])
    if not isinstance(doing_keys, list) or any(not isinstance(k, str) for k in doing_keys):
        raise ResultError("current_project_keys must be a list of reviewed claim keys")
    identity_fields = IDENTITY + (("time_resources", "当前约束"),
                                  ("collaboration_preference", "协作偏好")) if interview else IDENTITY
    priority_key = next((k for k in ("current_problem", "current_situation", "near_term_goal") if k in known), None)
    return {"entry": entry, "source_review": source_review,
            "doing": [evidence(k) for k in dict.fromkeys(doing_keys) if k in known],
            "identity": [{"key": k, "label": label, "state": field_state(k),
                          "fact": evidence(k) if k in known else None} for k, label in identity_fields],
            "priority": {"kind": "recommendation", "fact": evidence(priority_key) if priority_key else None},
            "actions": actions, "coverage_gaps": list(dict.fromkeys(list(coverage) + gaps)),
            "unresolved_keys": list(dict.fromkeys(f["key"] for f in reconciled["facts"] if f["status"] != "known"))}


def literal(text):
    """Render annotations as literal prose, not embedded instructions/Markdown."""
    text = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", " ".join(text.split()))


def cite(fact):
    return literal(fact["value"]) + "（来源：" + "、".join(literal(s) for s in fact["source_notes"]) + "）"


def render_result(result):
    lines = ["# 你是谁＋下一步怎么做", ""]
    entry = result["entry"]
    if entry["status"] == "verified":
        # URL parentheses would break a regular Markdown destination; angle form is safe.
        lines += ["知识库：[打开实际根页](<" + entry["url"] + ">)", ""]
    else:
        lines += ["知识库入口尚未确认：" + literal(entry["reason"]), ""]
    lines += ["## 你是谁", "", "以下只承接已读有来源的自述；字段缺口与来源未读分开。", ""]
    for row in result["identity"]:
        gap = {"needs_refresh": "已有待核说法、摘要或冲突，需要核对或刷新原来源",
               "source_review_pending": "有源未读或来源受阻，尚未判定",
               "unknown_in_checked_scope": "本轮必要来源已核对，仍无该字段依据"}.get(row["state"], "")
        lines.append("- " + row["label"] + "：" + (cite(row["fact"]) if row["fact"] else gap))
    lines += ["", "## 你在做什么", ""]
    lines += ["- " + cite(f) for f in result.get("doing", [])] or ["- 当前工作尚无足够已读依据；不从旧线程推定仍在进行。"]
    advice = ["", "## 建议", ""]
    priority = result["priority"]["fact"]
    advice.append("当前主线的依据：" + cite(priority) + "。具体候选与条件见上文，不另增任务。" if priority else
                 "必要资料尚未读完，先核已有目标，不重新询问主线。" if result["source_review"]["continuation_roles"] else
                 "部分来源受阻，当前问题尚无足够已读依据；保留覆盖缺口，暂不追加方案或重问主线。" if result["source_review"]["blocked_roles"] else
                 "当前主线尚无足够依据；可保留未知，不因字段名另行重问。")
    lines += ["", "## 你接下来做什么（候选，最多三项）", ""]
    if not result["actions"]:
        lines += ["当前没有足够依据生成可执行建议；已知内容与覆盖缺口仍保留。", ""]
    for number, action in enumerate(result["actions"], 1):
        lines.append(str(number) + ". " + literal(action["label"]))
        basis = "；".join(cite(f) for f in action["basis"]) if isinstance(action["basis"], list) else literal(action["basis"])
        lines += ["   - 依据：" + basis, "   - 第一步：" + literal(action["first_step"]),
                  "   - 需要确认的条件：" + literal(action["confirmation_condition"])]
        if action["kind"] == "recommendation":
            if action["state_check"] == "not_assessed":
                lines.append("   - 项目状态：未核（旧格式）；执行前需核适用项目、当前状态与允许动作。")
            elif action["state_check"] == "not_applicable":
                lines.append("   - 适用范围：一般建议；调用者标注不推进已有项目。")
            else:
                context = action["project_context"]
                lines.append("   - 项目状态：" + literal(context["status"]) + "；动作：" + literal(context["action_type"])
                             + "（来源：" + "、".join(literal(s) for s in context["source_ids"])
                             + "；只核标注结构，原话含义仍需审核）")
                if "restart_authorization" in context:
                    lines.append("   - 本次重启授权来源：" + literal(context["restart_authorization"]["source_id"]))
        if action["kind"] == "information_action":
            lines += ["   - 提供什么：" + literal(action["what"]), "   - 为什么：" + literal(action["why"]),
                      "   - 怎么提供：" + literal(action["how"])]
    lines += advice
    lines += ["", "## 覆盖缺口", ""]
    lines += ["- " + literal(s) for s in result["coverage_gaps"]] or ["- 本次未提供覆盖说明，不能推断全量已读。"]
    if result["unresolved_keys"]:
        lines += ["", "仍待核对的摘要、冲突或未确认项：" + "、".join(literal(LABELS.get(k, k)) for k in result["unresolved_keys"])]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="private JSON of reviewed claims, actual entry and advice")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as handle:
        data = json.load(handle)
    print(render_result(build_result(data)), end="")


if __name__ == "__main__":
    main()
