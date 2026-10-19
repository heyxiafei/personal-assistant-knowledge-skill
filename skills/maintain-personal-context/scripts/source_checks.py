"""Check supplied read receipts; no account access, routing calls or truth inference."""
from __future__ import annotations


class ReadCheckError(ValueError):
    pass


KINDS = {"chatgpt_conversation", "codex_thread", "space_page", "library_file",
         "project_file", "memory", "external_web"}


def check_read(row):
    """Preserve a current refusal; downgrade mismatched or incomplete read claims.

    All metadata is supplied by the caller from actual receipts. Selected-message
    completeness never implies conversation completeness. The function calls no
    tools and cannot establish whether omitted metadata or content exists.
    """
    result = dict(row)
    available = result.get("continuation_available")
    if available is not None and not isinstance(available, bool):
        raise ReadCheckError("continuation availability must be an observed boolean")
    check = result.get("read_check")
    if check is None:
        return result
    if not isinstance(check, dict):
        raise ReadCheckError("read_check must be an object")
    check = dict(check)
    scope = check.get("scope_kind", "source")
    if scope not in {"source", "selected_messages"}:
        raise ReadCheckError("read scope invalid")
    kinds = [check.get(k) for k in ("source_kind", "reader_kind")]
    if any(k not in KINDS for k in kinds):
        raise ReadCheckError("observed source and reader kinds required")
    targets = check.get("requested_message_ids", [])
    returned = check.get("returned_message_ids", [])
    for ids in (targets, returned):
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i.strip() for i in ids):
            raise ReadCheckError("message identifiers must be a text list")
    if scope == "selected_messages" and not targets:
        raise ReadCheckError("selected messages need requested identifiers")
    omitted = check.get("content_omitted")
    if "content_omitted" in check and not isinstance(omitted, bool):
        raise ReadCheckError("content_omitted must be boolean")
    truncated = check.get("truncated")
    if "truncated" in check and not isinstance(truncated, bool):
        raise ReadCheckError("truncated must be boolean")
    cursors = [check.get(k) for k in ("previous_cursor", "next_cursor")]
    if any(c is not None and (not isinstance(c, str) or not c.strip()) for c in cursors):
        raise ReadCheckError("pagination cursors must be null or nonempty text")
    messages = check.get("returned_messages", [])
    if not isinstance(messages, list):
        raise ReadCheckError("returned message receipts must be a list")
    by_id = {}
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("id"), str) or not message["id"].strip():
            raise ReadCheckError("returned message receipt needs its actual id")
        if message["id"] in by_id or message["id"] not in returned:
            raise ReadCheckError("message receipt must uniquely match a returned identifier")
        for flag in ("content_omitted", "truncated"):
            if flag in message and not isinstance(message[flag], bool):
                raise ReadCheckError("message completeness flags must be boolean")
        by_id[message["id"]] = message
    available = check.get("continuation_available", available)
    if available is not None and not isinstance(available, bool):
        raise ReadCheckError("continuation availability must be an observed boolean")
    origin = check.get("status_origin", "current")
    if origin not in {"current", "inherited"}:
        raise ReadCheckError("status origin invalid")
    versions = [check.get(k) for k in ("read_version", "latest_version")]
    for v in versions:
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, str))
                              or isinstance(v, str) and not v.strip()):
            raise ReadCheckError("version must be an observed number or text")
    if (versions[0] is None) != (versions[1] is None):
        raise ReadCheckError("version comparison needs both observed versions")
    limitations = result.get("limitations", [])
    if not isinstance(limitations, list) or any(not isinstance(x, str) or not x.strip() for x in limitations):
        raise ReadCheckError("limitations must be nonempty text")
    limitations = list(limitations)
    reasons = []
    status = result["status"]
    completeness = {}
    for mid in targets:
        message = by_id.get(mid, {})
        # Only an explicitly false aggregate flag proves this dimension for
        # an unreported target flag. Missing metadata is not a clean receipt.
        flags = [message.get("content_omitted", False if omitted is False else None),
                 message.get("truncated", False if truncated is False else None)]
        completeness[mid] = ("missing" if mid not in returned else "incomplete" if True in flags
                             else "unknown" if None in flags else "complete")
    if scope == "selected_messages":
        check["target_completeness"] = completeness
    # Refusal is not permission to try another path. A mismatched old lookup
    # that never addressed the target is not a refusal by that target.
    if status not in {"permission_denied", "user_excluded", "experiment_excluded"}:
        if kinds[0] != kinds[1]:
            status = "discovered_unread"
            reasons.append("读取入口与来源类型不一致，尚未验证指定来源")
        elif origin == "inherited" and status in {"entry_missing", "entry_unavailable"}:
            status = "stale"
            reasons.append("旧入口结论需按当前实际能力核对，不能永久继承")
        elif status == "read":
            missing = [i for i in targets if i not in returned]
            if missing:
                status = "partial"
                reasons.append("指定消息未返回：" + "、".join(missing))
            incomplete = [mid for mid, state in completeness.items() if state in {"incomplete", "unknown"}]
            if scope == "selected_messages" and incomplete:
                status = "partial"
                reasons.append("指定消息正文遗漏、截断或完整性未确认：" + "、".join(incomplete))
            if scope == "source" and (omitted or truncated or any(c is not None for c in cursors)
                                      or any(m.get("content_omitted") or m.get("truncated") for m in messages)):
                status = "partial"
                reasons.append("来源存在正文遗漏、截断或前后分页未结束")
        if all(v is not None for v in versions) and versions[0] != versions[1]:
            status = "stale"
            reasons.append("生成所用版本与本次最新读取版本不同，需核变化章节")
    if scope == "selected_messages":
        limitations.append("仅核指定消息，不证明整段对话完整读取")
        if omitted:
            limitations.append("回执content_omitted=true；未返回内容仍未覆盖")
        if truncated:
            limitations.append("回执truncated=true；响应截断范围仍需核对")
        if any(c is not None for c in cursors):
            limitations.append("对话前后分页未完；保留实际previous_cursor/next_cursor")
    if available is not None:
        result["continuation_available"] = available
    if available is False:
        limitations.append("本次回执确认无获准补读入口；保留已返回事实及缺口，停止此源续读")
    result["status"] = status
    if reasons:
        result["reason"] = "；".join(reasons + ([row["reason"]] if row.get("reason") else []))
    result["read_check"] = check
    if limitations:
        result["limitations"] = list(dict.fromkeys(limitations))
    return result
