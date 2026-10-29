#!/usr/bin/env python3
"""Local resumable onboarding: private skeleton -> sources -> advice -> optional table.

The host Agent discovers/reads authorized sources and reviews annotations; this
module does not discover accounts, infer personal facts, invoke UI or grant consent.
The single owner-only state file is also the initial private knowledge base.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from urllib.parse import quote

import first_run
import interview
import native_history
import quick_start
import context_store
import space_state


class OnboardingError(ValueError):
    pass


def source_id(host, thread, item):
    return 'native-' + hashlib.sha256(context_bytes([host, thread, item])).hexdigest()


def context_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _file_digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_index(path, selected_source_id=None):
    """Read metadata and, when selected, one source. This is not account enumeration."""
    p = Path(path)
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise OnboardingError('source index needs an explicit non-symlink absolute path')
    st = p.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
        raise OnboardingError('source index must be owner-only and a non-linked regular file')
    if Path(str(p) + '-wal').exists():
        raise OnboardingError('source index has a live WAL; reconcile it before reading')
    before = _file_digest(p)
    db = sqlite3.connect('file:' + quote(str(p)) + '?mode=ro&immutable=1', uri=True)
    sources = {}
    try:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute("SELECT host_id,thread_id,message_id,role,read_status,typeof(text), instr(lower(text),'<codex_delegation') > 0 OR instr(lower(text),'<transcript_evidence') > 0 FROM messages")
        for host, thread, item, role, status, text_type, delegated in rows:
            if not all(isinstance(v, str) and v for v in (host, thread, item)) or role not in ('user', 'assistant') \
                    or status not in ('text_returned', 'partial', 'unread') or text_type != 'text':
                raise OnboardingError('source index contains incomplete identity, role or read status')
            sid = source_id(host, thread, item)
            if sid in sources:
                raise OnboardingError('duplicate source identity in index')
            sources[sid] = {'source_id': sid, 'author_role': role, 'read_status': status, 'delegated_instruction': bool(delegated)}
            if sid == selected_source_id:
                text = db.execute('SELECT text FROM messages WHERE host_id=? AND thread_id=? AND message_id=?',
                                  (host, thread, item)).fetchone()
                if not text or not isinstance(text[0], str):
                    raise OnboardingError('selected source text unavailable')
                sources[sid]['text'] = text[0]
    finally:
        db.close()
    if _file_digest(p) != before:
        raise OnboardingError('source index changed during read')
    return {'sha256': before, 'sources': sources, 'account_enumeration_complete': 'unknown'}


def normalize_source(reads, thread_id, host_id):
    """Use real captured pages with actual request cursors; no host calls here."""
    return native_history.normalize_reads(reads, thread_id, host_id)


def checked(state):
    if not isinstance(state, dict) or state.get('schema_version') != 1 or state.get('kind') != 'local_onboarding' \
            or type(state.get('revision')) is not int or state.get('stage') not in ('table', 'library'):
        raise OnboardingError('invalid onboarding state')
    quick_start.checked(state['session'])
    if not isinstance(state.get('events'), dict) or not isinstance(state.get('excluded_topics'), list):
        raise OnboardingError('invalid onboarding controls')
    if type(state.get('table_skipped', False)) is not bool:
        raise OnboardingError('invalid table skip state')
    # Pre-existing table-only checkpoints remain readable and are not rewritten
    # without an explicit operation. New states always begin with a library.
    if state['stage'] == 'table' and state.get('library') is not None:
        raise OnboardingError('invalid legacy pre-submission state')
    if state['stage'] == 'library':
        expected = _library(state['session'], state.get('user_submitted', False), state.get('tradeoff_suggestion'))
        if 'user_submitted' not in state:  # read legacy checkpoint without silently rewriting it
            expected.pop('advice_stage')
            expected.pop('deeper_guidance')
        if state.get('library') != expected:
            raise OnboardingError('library was edited; preserve it and reconcile before updating')
    return state


def _validate_sources(data, index):
    for claim in data.get('claims', []):
        sid = claim.get('source_id', '')
        if not isinstance(sid, str):
            raise OnboardingError('claim source identity required')
        if sid.startswith('native-'):
            src = index['sources'].get(sid) if index else None
            if not src or claim.get('author_role') != src['author_role']:
                raise OnboardingError('native annotation requires matching actual indexed source and role')
            if src['read_status'] == 'unread':
                raise OnboardingError('unread native source cannot support a claim')
            if src['delegated_instruction'] and claim.get('evidence_tier') in ('direct', 'saved'):
                raise OnboardingError('delegated instruction text is a lead, not direct user evidence')
            if src['author_role'] == 'user' and claim.get('evidence_tier') in ('direct', 'saved') \
                    and (claim.get('human_origin_verified') is not True
                         or not isinstance(claim.get('origin_evidence'), str) or not claim['origin_evidence'].strip()):
                raise OnboardingError('user-role history requires independently reviewed human origin evidence')


def _empty_data():
    return {'entry': {'status': 'unavailable', 'reason': '私有骨架已建；来源尚未读取'},
            'claims': [], 'recommendations': [],
            'coverage_gaps': ['历史和记忆的完整覆盖仍未知。'],
            'source_review': [{'role': role, 'status': 'not_attempted',
                'scope': '尚未发现或读取来源', 'reason': '来源尚未读取，不能推断不存在',
                'continuation_available': False} for role in first_run.SOURCE_ROLES]}


def initialize():
    """Create an empty private skeleton in memory before any account read."""
    session = quick_start.start(_empty_data())
    return {'schema_version': 1, 'kind': 'local_onboarding', 'revision': 0,
            'stage': 'library', 'session': session, 'events': {}, 'excluded_topics': [],
            'source_index': None, 'user_submitted': False, 'table_skipped': False, 'tradeoff_suggestion': None, 'library': _library(session, False, None)}


def ensure_skeleton(state):
    """Convert an explicitly selected legacy table checkpoint without losing edits."""
    checked(state)
    if state['stage'] == 'library':
        return copy.deepcopy(state)
    out = copy.deepcopy(state)
    out['revision'] += 1
    out['stage'] = 'library'
    out['user_submitted'] = False
    out['tradeoff_suggestion'] = None
    out['library'] = _library(out['session'], False, None)
    return out


def ingest(state, data, *, source_index=None):
    """Merge Agent-reviewed source annotations into the existing library."""
    checked(state)
    index = read_index(source_index) if source_index else None
    _validate_sources(data, index)
    data = copy.deepcopy(data)
    if index and any(index['sources'].get(c.get('source_id'), {}).get('read_status') == 'partial'
                     for c in data.get('claims', [])):
        data.setdefault('coverage_gaps', []).append('部分引用来源仅返回片段，原文及附件覆盖仍待核。')
    # Validate the received source report before altering existing state.
    first_run.build_result(data, interview=True)
    session = copy.deepcopy(state['session'])
    old_roles = {r['role']: r for r in session['data']['source_review']}
    for row in data['source_review']:
        prior = old_roles.get(row['role'], {})
        if prior.get('status') in ('permission_denied', 'user_excluded') and row.get('status') != prior['status']:
            raise OnboardingError('preserve refused or excluded source until separately authorized and reconciled')
    previous_gaps = session['data'].get('coverage_gaps', [])
    for key in ('entry', 'source_review', 'coverage_gaps'):
        session['data'][key] = copy.deepcopy(data[key])
    session['data']['coverage_gaps'] = list(dict.fromkeys(previous_gaps + session['data']['coverage_gaps']))
    previously_confirmed = list(session['data'][quick_start.META]['confirmed_sources'])
    session = quick_start.supplement(session, data['claims'])
    # Historical direct user messages are sourced evidence, not confirmation
    # of this current prefilled table. Preserve only actual prior confirmations.
    session['data'][quick_start.META]['confirmed_sources'] = previously_confirmed
    # Never replace a user's later reviewed advice with an older source scan.
    # Changed evidence invalidates advice until the Agent reviews it again.
    evidence_changed = bool(session.get('advice_needs_refresh')) or (
        session['data']['source_review'] != state['session']['data']['source_review'])
    if state.get('user_submitted', False):
        if evidence_changed:
            session = interview.set_advice(session, [])
    elif data.get('recommendations') is not None and (session.get('advice_needs_refresh') or
            session['data'].get('recommendations', []) != data['recommendations']):
        session = interview.set_advice(session, data['recommendations'])
    source_meta = {'sha256': index['sha256'], 'count': len(index['sources']),
                   'account_enumeration_complete': 'unknown'} if index else None
    if session == state['session'] and source_meta == state['source_index']:
        return copy.deepcopy(state)
    out = copy.deepcopy(state)
    out['revision'] += 1
    out['stage'] = 'library'
    out['session'] = session
    out['source_index'] = source_meta
    out['user_submitted'] = state.get('user_submitted', False)
    if evidence_changed:
        out['tradeoff_suggestion'] = None
    out['library'] = _library(session, out['user_submitted'], out.get('tradeoff_suggestion'))
    return out


def start(data, *, source_index=None):
    """Compatibility helper: skeleton first, then merge reviewed sources."""
    return ingest(initialize(), data, source_index=source_index)


def preview(state):
    checked(state)
    import guided_exploration
    view = quick_start.draft(state['session'])
    return {'stage': state['stage'], 'revision': state['revision'],
            'prefilled': view['prefilled'], 'known_unknown': guided_exploration.knowledge_map(state['session']),
            'preview': view['markdown'],
            'next_question': next_gap(state), 'library': copy.deepcopy(state['library']),
            'core_status': 'incomplete', 'account_enumeration_complete': 'unknown'}


def next_gap(state):
    checked(state)
    if state.get('table_skipped', False):
        return None
    rows = quick_start.prefilled(state['session'])
    for wanted in ('unresolved', 'missing', 'source_review_pending', 'details_pending'):
        for row in rows:
            if row['state'] == wanted and not row['decision'] and row['id'] not in state['excluded_topics']:
                return {'topic_id': row['id'], 'label': row['label'], 'state': row['state'],
                        'prompt': '在已读范围中尚未确认，可补充、跳过或暂不确定。', 'optional': True}
    return None


def _library(session, user_submitted=False, tradeoff_suggestion=None):
    view = quick_start.draft(session)
    result = first_run.build_result(session['data'], interview=True)
    advice = copy.deepcopy(result['actions'])
    if not advice:
        priority = result['priority']['fact']
        advice = [{'kind': 'generic_suggestion',
                   'label': '可先围绕已知现状或目标确定一个小的下一步。' if priority else '可先选择一个近期目标，再确定一个小的下一步。',
                   'basis': {'kind': 'source_backed_context', 'fact': copy.deepcopy(priority)} if priority
                            else {'kind': 'generic', 'note': '一般性建议，需结合你的实际情况确认'}}]
    facts = {f['key']: f for f in first_run.reconcile_claims(session['data']['claims'])['facts']}
    def known(key):
        f = facts.get(key)
        return f['current_value'] if f and f['status'] == 'known' else None
    return {'version': 1, 'provisional': True, 'table': copy.deepcopy(view['prefilled']),
            'profile': copy.deepcopy(result['identity']), 'projects': copy.deepcopy(result['doing']),
            'priority': copy.deepcopy(result['priority']), 'advice': advice[:3],
            'advice_stage': 'after_user_input' if user_submitted else 'initial',
            'deeper_guidance': {'goal': known('near_term_goal'), 'constraints': known('time_resources'),
                'priority_tradeoff': tradeoff_suggestion or '待本人选择', 'actions': copy.deepcopy(advice[:3]),
                'needs_agent_review': not bool(tradeoff_suggestion and session['data'].get('recommendations'))} if user_submitted else None,
            'coverage_gaps': copy.deepcopy(result['coverage_gaps']),
            'core_status': 'incomplete', 'account_enumeration_complete': 'unknown'}


def submit(state, *, event_id, responses, source_id_value, recommendations=None, tradeoff_suggestion=None):
    """Apply an actual table submission, correction or exclusion idempotently.

    The caller verifies real user authorization and the selected private target;
    a string provided here is an evidence locator, not a permission token.
    """
    checked(state)
    if not isinstance(event_id, str) or not event_id.strip() or not isinstance(source_id_value, str) or not source_id_value.strip():
        raise OnboardingError('actual submission and user source identities required')
    if not isinstance(responses, list):
        raise OnboardingError('responses must be a list')
    if tradeoff_suggestion is not None and (not isinstance(tradeoff_suggestion, str)
            or not tradeoff_suggestion.strip() or len(tradeoff_suggestion) > 2000):
        raise OnboardingError('tradeoff suggestion must be a short text or null')
    request = {'responses': responses, 'source_id': source_id_value, 'recommendations': recommendations,
               'tradeoff_suggestion': tradeoff_suggestion}
    digest = hashlib.sha256(context_bytes(request)).hexdigest()
    old = state['events'].get(event_id)
    if old is not None:
        legacy = hashlib.sha256(context_bytes({'responses': responses, 'source_id': source_id_value,
                                                'recommendations': recommendations})).hexdigest()
        if old != digest and not (tradeoff_suggestion is None and old == legacy):
            raise OnboardingError('submission identity reused with different content')
        return copy.deepcopy(state)
    session = copy.deepcopy(state['session'])
    excluded = list(state['excluded_topics'])
    topic_keys = {t: set(keys) for t, _, keys in quick_start.TOPICS}
    seen_topics = set()
    for i, row in enumerate(responses):
        if not isinstance(row, dict) or row.get('topic_id') not in topic_keys:
            raise OnboardingError('unknown table topic')
        topic, action = row['topic_id'], row.get('action')
        if topic in seen_topics:
            raise OnboardingError('one action per topic per submission')
        seen_topics.add(topic)
        if action in ('skip', 'uncertain', 'do_not_save'):
            session = quick_start.import_decisions(session, [{'topic_id': topic, 'action': action,
                  'submission_id': event_id + '-' + str(i), 'source_id': source_id_value}])
            if action == 'do_not_save' and topic not in excluded:
                excluded.append(topic)
        elif action == 'answer':
            if topic in excluded:
                raise OnboardingError('excluded topic needs an explicit separate reacceptance workflow')
            if row.get('key') not in topic_keys[topic] or not isinstance(row.get('value'), str) or not row['value'].strip():
                raise OnboardingError('answer needs an allowed key and value')
            recorded_at = row.get('recorded_at')
            try:
                stamp = datetime.fromisoformat(recorded_at.replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError('timezone missing')
            except (AttributeError, ValueError):
                raise OnboardingError('actual answer timestamp with timezone required')
            claim = {'key': row['key'], 'value': row['value'], 'source_id': source_id_value,
                     'recorded_at': recorded_at, 'source_kind': 'conversation',
                     'author_role': 'user', 'evidence_tier': 'direct'}
            if row.get('correction_of') is not None:
                claim['correction_of'] = row['correction_of']
            session = quick_start.supplement(session, [claim])
        else:
            raise OnboardingError('unsupported table action')
    # Every correction invalidates prior advice. The Agent may submit reviewed
    # recommendations, otherwise the result contains only a generic suggestion.
    if recommendations is not None:
        session = interview.set_advice(session, recommendations)
    elif session.get('advice_needs_refresh'):
        session = interview.set_advice(session, [])
    out = copy.deepcopy(state)
    out['session'] = session
    out['excluded_topics'] = excluded
    out['events'][event_id] = digest
    out['revision'] += 1
    out['stage'] = 'library'
    out['user_submitted'] = True
    # Empty response is an explicit skip of the whole optional table. Keep it
    # across later imports and corrections; explicit exploration remains available.
    out['table_skipped'] = state.get('table_skipped', False) or not responses
    out['tradeoff_suggestion'] = tradeoff_suggestion
    out['library'] = _library(session, True, tradeoff_suggestion)
    return out


def _private_path(path, *, require_file=False):
    p = Path(path)
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise OnboardingError('state requires an absolute non-symlink path')
    parent = p.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise OnboardingError('state directory must be owner-only')
    if require_file:
        st = p.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
            raise OnboardingError('state file must be owner-only and non-linked')
    return p


def save(path, state, *, previous=None, expected_hash=None):
    _private_path(path, require_file=previous is not None)
    checked(state)
    if previous is None:
        if expected_hash is not None:
            raise OnboardingError('new state cannot use an existing hash')
        # A small independent marker prevents a later start from silently
        # recreating a library the user removed. An interrupted creation is
        # deliberately left for inspection, not automatically reset.
        p = Path(path)
        context_store.check_root(p.parent)
        marker = p.with_name(p.name + '.initialized')
        if p.exists() or marker.exists() or marker.is_symlink():
            raise OnboardingError('existing or removed library requires reconciliation')
        context_store.create_new(marker, b'local-onboarding-v1\n')
        space_state.save_state(path, state)
    else:
        checked(previous)
        if not expected_hash:
            raise OnboardingError('existing state needs the last read hash')
        if state == previous:
            _, actual_hash = load(path)
            if actual_hash != expected_hash:
                raise OnboardingError('state bytes changed; preserve edits and reread')
            return actual_hash
        if state['revision'] != previous['revision'] + 1:
            raise OnboardingError('changed state needs exactly one new revision')
        space_state.save_state(path, state, expected_revision=previous['revision'], expected_file_hash=expected_hash)
    return space_state.load_state(path, with_hash=True)[1]


def load(path):
    _private_path(path, require_file=True)
    state, digest = space_state.load_state(path, with_hash=True)
    checked(state)
    return state, digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', help='absolute owner-only private state file')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('init')
    init = sub.add_parser('start')
    init.add_argument('--input', required=True, help='private reviewed first-run annotations')
    init.add_argument('--source-index', help='selected private native source index, read-only')
    sub.add_parser('show')
    explorer = sub.add_parser('explore')
    explorer.add_argument('--mode', required=True, choices=('known_unknown', 'four_domains', 'blindspots', 'deep_interview'))
    explorer.add_argument('--input', help='optional private mode options JSON')
    ingester = sub.add_parser('ingest')
    ingester.add_argument('--input', required=True, help='private reviewed source annotations')
    ingester.add_argument('--source-index', help='selected private source index, read-only')
    listing = sub.add_parser('source-list')
    listing.add_argument('--index', required=True)
    listing.add_argument('--offset', type=int, default=0)
    listing.add_argument('--limit', type=int, default=50)
    reading = sub.add_parser('source-read')
    reading.add_argument('--index', required=True)
    reading.add_argument('--source-id', required=True)
    update = sub.add_parser('submit')
    update.add_argument('--input', required=True, help='private user submission JSON')
    args = parser.parse_args(argv)
    try:
        if args.action in ('source-list', 'source-read'):
            index = read_index(args.index, selected_source_id=args.source_id if args.action == 'source-read' else None)
            if args.action == 'source-list':
                if args.offset < 0 or not 1 <= args.limit <= 100:
                    raise OnboardingError('invalid source page bounds')
                rows = list(index['sources'].values())
                response = {'sha256': index['sha256'], 'total_in_index': len(rows),
                            'sources': rows[args.offset:args.offset + args.limit],
                            'next_offset': args.offset + args.limit if args.offset + args.limit < len(rows) else None,
                            'account_enumeration_complete': 'unknown'}
            else:
                if args.source_id not in index['sources']:
                    raise OnboardingError('source is not in the selected index')
                response = index['sources'][args.source_id]
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 0
        if not args.state:
            raise OnboardingError('private state path required')
        path = Path(args.state)
        if args.action in ('init', 'start'):
            if path.exists():
                state, digest = load(path)
                state2 = ensure_skeleton(state)
                if state2 != state:
                    digest = save(path, state2, previous=state, expected_hash=digest)
                    state = state2
            else:
                # The skeleton is durable even if the subsequent source read fails.
                state = initialize()
                digest = save(path, state)
            if args.action == 'start':
                data = json.loads(Path(args.input).read_text(encoding='utf-8'))
                state2 = ingest(state, data, source_index=args.source_index)
                if state2 != state:
                    save(path, state2, previous=state, expected_hash=digest)
                state = state2
        else:
            state, digest = load(path)
            if args.action == 'explore':
                import guided_exploration
                options = json.loads(Path(args.input).read_text(encoding='utf-8')) if args.input else {}
                if not isinstance(options, dict):
                    raise OnboardingError('exploration options must be an object')
                print(json.dumps(guided_exploration.explore(state['session'], args.mode, **options),
                                 ensure_ascii=False, indent=2))
                return 0
            if args.action == 'submit':
                req = json.loads(Path(args.input).read_text(encoding='utf-8'))
                state2 = submit(state, **req)
                if state2 != state:
                    save(path, state2, previous=state, expected_hash=digest)
                state = state2
            elif args.action == 'ingest':
                data = json.loads(Path(args.input).read_text(encoding='utf-8'))
                state2 = ingest(state, data, source_index=args.source_index)
                if state2 != state:
                    save(path, state2, previous=state, expected_hash=digest)
                state = state2
        print(json.dumps(preview(state), ensure_ascii=False, indent=2))
        return 0
    except (OnboardingError, interview.InterviewError, space_state.StateError, context_store.ContextError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
