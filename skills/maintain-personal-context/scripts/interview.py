#!/usr/bin/env python3
"""Optional local interview reducer, not a history reader, NLP parser or UI.

start(data) consumes the existing first_run.py reviewed-annotation schema.
draft(session) returns a source-backed preview/token and at most one question.
respond/ correct/ set_advice return new sessions; they never write the library.
confirm binds the displayed token, exact target and reviewed save plan. apply_local
uses the existing context_store transaction writer, only for an already initialized
explicit local target. Real Pages writes still use actual tools and space_state.
Checkpointing is optional, requires separate user consent, and is outside the
package. load_checkpoint returns both state and a byte hash; checkpoint requires
that hash to replace it, preserving manual changes. No-save input must be omitted
from annotations; skip/uncertain/do_not_save never add a fact or grant consent.
CLI --input previews annotated first_run data on stdout, without writing files.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path

import context_store
import first_run
import space_state

TOPICS = (
    ("situation", "当前现状", ("current_situation", "current_problem"), "current_situation"),
    ("goal", "近期目标", ("near_term_goal",), "near_term_goal"),
    ("constraints", "实际约束", ("time_resources",), "time_resources"),
    ("collaboration", "协作偏好", ("collaboration_preference",), "collaboration_preference"),
)


class InterviewError(ValueError):
    pass


def token(value):
    return hashlib.sha256(context_store.encode(value)).hexdigest()


def text(value, label):
    return first_run.nonempty(value, label)


def checked(session):
    if not isinstance(session, dict) or session.get("schema_version") != 1 \
            or session.get("kind") != "first_interview" or type(session.get("revision")) is not int:
        raise InterviewError("invalid interview checkpoint")
    if session["phase"] == "discarded":
        raise InterviewError("session discarded; start a new explicitly requested preview")
    return session


def result(session):
    checked(session)
    return first_run.build_result(session["data"], interview=True)


def start(data, checkpoint_consent=None):
    first_run.build_result(data, interview=True)
    return {"schema_version": 1, "kind": "first_interview", "revision": 0,
            "phase": "preview", "data": copy.deepcopy(data), "decisions": {},
            "submissions": {}, "advice_needs_refresh": False,
            "checkpoint_consent": text(checkpoint_consent, "checkpoint consent source")
            if checkpoint_consent is not None else None}


def question(session):
    view = result(session)
    if session["phase"] != "preview" or view["source_review"]["continuation_roles"]:
        return None
    facts = {f["key"]: f for f in first_run.reconcile_claims(session["data"]["claims"])["facts"]}
    for topic, label, keys, default in TOPICS:
        if topic in session["decisions"]:
            continue
        unresolved = [k for k in keys if k in facts and facts[k]["status"] != "known"]
        if not unresolved and any(k in facts and facts[k]["status"] == "known" for k in keys):
            continue
        # A blocked source is not proof of missing personal information.
        if not unresolved and view["source_review"]["blocked_roles"]:
            continue
        return {"id": topic, "key": unresolved[0] if unresolved else default,
                "title": "只核对" + label + "中的待确认说法" if unresolved else "只补充尚缺的" + label,
                "reason": "unresolved" if unresolved else "missing_in_checked_scope",
                "options": ["补充或纠正一句话", "暂时不确定", "跳过，不保存此项"],
                "free_text": True, "voice_input": "仅接受系统语音转文字；可先纠正文字"}
    return None


def draft(session, *, target=None, reviewed_plan=None):
    view = result(session)
    if (target is None) != (reviewed_plan is None):
        raise InterviewError("show target and reviewed plan together")
    preview_token = token({"revision": session["revision"], "data": session["data"],
                           "decisions": session["decisions"], "target": target,
                           "plan_digest": token(reviewed_plan) if reviewed_plan is not None else None})
    proposed = ""
    if reviewed_plan is not None:
        proposed = "\n## 本轮具体保存计划（待确认）\n\n目标：" + first_run.literal(target) + "\n"
        # Show exact managed-file changes, including superseded records, rather
        # than a second independently summarized inventory that could omit them.
        changes = reviewed_plan.get("changes", {})
        if not isinstance(changes, dict) or any(not isinstance(v, str) for v in changes.values()):
            raise InterviewError("reviewed plan changes must contain actual proposed text")
        for path, content in changes.items():
            proposed += "\n文件：" + first_run.literal(path) + "\n\n" + first_run.literal(content) + "\n"
    return {"phase": session["phase"], "preview_token": preview_token,
            "markdown": ("本轮已保存，当前状态仍以目标读回为准。\n\n" if session["phase"] == "saved"
                         else "本轮预览，尚未据此更新知识库。\n\n") + first_run.render_result(view) + proposed,
            "question": question(session), "advice_needs_refresh": session["advice_needs_refresh"],
            "coverage": view["source_review"], "unresolved_keys": view["unresolved_keys"]}


def revised(session):
    checked(session)
    if session["phase"] == "saved":
        raise InterviewError("saved session is immutable; start a new maintenance preview")
    new = copy.deepcopy(session)
    new["phase"] = "preview"
    new["revision"] += 1
    new.pop("confirmation", None)
    return new


def add_claim(session, key, value, source_id, recorded_at, correction_of=None, author_role="user"):
    # These fields are explicit caller annotations of a real user reply, not
    # verified identity, event completion or permission to save that reply.
    stamp = datetime.fromisoformat(text(recorded_at, "actual reply timestamp").replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise InterviewError("reply timestamp needs a timezone")
    new = revised(session)
    claim = {"key": text(key, "claim key"), "value": text(value, "reply text"),
             "source_id": text(source_id, "actual reply source"), "recorded_at": recorded_at,
             "source_kind": "conversation", "author_role": author_role, "evidence_tier": "direct"}
    if correction_of is not None:
        claim["correction_of"] = text(correction_of, "explicitly corrected old value")
    new["data"]["claims"].append(claim)
    # Corrections affect both the portrait and advice. Never keep advice based
    # on stale constraints until an Agent has explicitly reviewed it again.
    new["data"]["recommendations"] = []
    new["advice_needs_refresh"] = True
    result(new)
    return new


def respond(session, topic_id, action, submission_id, *, value=None,
            source_id=None, recorded_at=None, correction_of=None, author_role="user"):
    checked(session)
    if action not in {"answer", "skip", "uncertain", "do_not_save"}:
        raise InterviewError("unknown reply action")
    sid = text(submission_id, "submission identity")
    payload = {"topic": topic_id, "action": action}
    if action == "answer":
        payload.update(value=value, source_id=source_id, recorded_at=recorded_at,
                       correction_of=correction_of, author_role=author_role)
    digest = token(payload)
    if sid in session["submissions"]:
        if session["submissions"][sid] != digest:
            raise InterviewError("submission identity reused with different content")
        return copy.deepcopy(session)
    q = question(session)
    if not q or q["id"] != topic_id:
        raise InterviewError("reply must match the one current missing/conflicting question")
    new = add_claim(session, q["key"], value, source_id, recorded_at, correction_of, author_role) \
        if action == "answer" else revised(session)
    new["decisions"][topic_id] = action
    new["submissions"][sid] = digest
    return new


def correct(session, key, value, source_id, recorded_at, correction_of):
    return add_claim(session, key, value, source_id, recorded_at, correction_of)


def set_advice(session, recommendations):
    new = revised(session)
    new["data"]["recommendations"] = copy.deepcopy(recommendations)
    result(new)  # existing source and paused/cancelled-project guards apply
    new["advice_needs_refresh"] = False
    return new


def confirm(session, preview_token, target, source_id, reviewed_plan):
    checked(session)
    if session["phase"] != "preview" or draft(session, target=target, reviewed_plan=reviewed_plan)["preview_token"] != preview_token:
        raise InterviewError("confirmation is stale; show the current preview")
    if session["advice_needs_refresh"]:
        raise InterviewError("review advice after changes before confirming")
    if not isinstance(reviewed_plan, dict):
        raise InterviewError("exact reviewed save plan required")
    new = copy.deepcopy(session)
    new["phase"] = "confirmed"
    new["confirmation"] = {"preview_token": preview_token, "target": text(target, "explicit target"),
                           "source_id": text(source_id, "explicit save confirmation source"),
                           "plan_digest": token(reviewed_plan)}
    return new


def apply_local(session, root, plan):
    checked(session)
    root = context_store.check_root(root)
    auth = session.get("confirmation", {})
    if session["phase"] not in {"confirmed", "saved"} or auth.get("target") != str(root) \
            or auth.get("plan_digest") != token(plan) \
            or auth.get("preview_token") != draft(session, target=str(root), reviewed_plan=plan)["preview_token"]:
        raise InterviewError("save needs the exact confirmed preview, target and plan")
    if session["phase"] == "saved":
        return copy.deepcopy(session)
    new = copy.deepcopy(session)
    new["receipt"] = context_store.apply_plan(root, plan)
    new["phase"] = "saved"
    return new


def discard(session):
    checked(session)
    return {"schema_version": 1, "kind": "first_interview", "revision": session["revision"] + 1,
            "phase": "discarded", "data": None, "decisions": {}, "submissions": {},
            "checkpoint_consent": None, "previous_checkpoints_automatically_deleted": False}


def load_checkpoint(path):
    state, digest = space_state.load_state(path, with_hash=True)
    checked(state)
    result(state)
    if state["phase"] == "confirmed":
        state = revised(state)
        state["resumed_confirmation_invalidated"] = True
    return state, digest


def checkpoint(path, session, expected_hash=None):
    checked(session)
    if not session.get("checkpoint_consent"):
        raise InterviewError("private checkpoint requires separate user consent")
    p = Path(path)
    previous_revision = None
    if p.exists():
        if expected_hash is None:
            raise InterviewError("existing checkpoint requires its last read byte hash")
        previous = space_state.load_state(p)
        checked(previous)
        previous_revision = previous["revision"]
        if session["revision"] < previous_revision:
            raise InterviewError("checkpoint revision cannot go backwards")
    elif expected_hash is not None:
        raise InterviewError("checkpoint disappeared; reconcile before replacing")
    space_state.save_state(p, session, expected_revision=previous_revision, expected_file_hash=expected_hash)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="private first_run annotated JSON; stdout preview only")
    args = parser.parse_args()
    print(json.dumps(draft(start(json.loads(Path(args.input).read_text()))), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
