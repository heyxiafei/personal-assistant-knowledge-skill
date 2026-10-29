#!/usr/bin/env python3
"""Optional, non-diagnostic ways to explore reviewed onboarding evidence.

This helper makes no personality classification, automatic diagnosis, source
reads, user choices, or writes. The caller chooses a mode explicitly. Model
interpretation and the user's own decisions remain separate from source facts.
"""
from __future__ import annotations
import copy

import first_run
import quick_start


class ExplorationError(ValueError):
    pass


def knowledge_map(session):
    """Separate supported current statements, hypotheses, conflicts and gaps."""
    quick_start.checked(session)
    rows = quick_start.prefilled(session)
    facts = {x['key']: x for x in first_run.reconcile_claims(session['data']['claims'])['facts']}
    known, hypotheses, conflicts, unknown, excluded = [], [], [], [], []
    for row in rows:
        if row['state'] == 'excluded':
            excluded.append({'topic_id': row['id'], 'label': row['label']})
            continue
        for value in row['values']:
            entry = {'topic_id': row['id'], 'label': row['label'], **copy.deepcopy(value)}
            if value['evidence'] in ('sourced_user', 'user_confirmed') and value['current']:
                known.append(entry)
            elif value['evidence'] == 'inferred':
                hypotheses.append(entry)
        has_conflict = any(facts.get(v['key'], {}).get('status') == 'conflict' for v in row['values'])
        if has_conflict:
            conflicts.append({'topic_id': row['id'], 'label': row['label'],
                              'candidates': copy.deepcopy(row['values'])})
        elif row['state'] in ('unresolved', 'missing', 'source_review_pending', 'details_pending'):
            unknown.append({'topic_id': row['id'], 'label': row['label'], 'state': row['state'],
                            'decision': row['decision']})
    return {'known': known, 'hypotheses': hypotheses, 'conflicts': conflicts,
            'unknown': unknown, 'excluded': excluded,
            'source_coverage': copy.deepcopy(first_run.review_sources(session['data'])),
            'account_enumeration_complete': 'unknown',
            'notice': '已知指有来源的当前陈述，不是独立真伪认证；未知不推断为缺陷。'}


DOMAINS = (
    ('mind', 'Mind 心智', '思考、学习与看问题的方式'),
    ('body', 'Body 身体精力', '身体状态、精力与日常节奏'),
    ('spirit', 'Spirit 价值关系意义', '价值、关系与意义'),
    ('vocation', 'Vocation 事业资源', '事业、项目与可用资源'),
)


def four_domains(session, *, focus=None, selections=None, skip=False):
    """Offer four optional domains; attach evidence only by explicit selection."""
    evidence = knowledge_map(session)
    if type(skip) is not bool:
        raise ExplorationError('skip must be a boolean')
    ids = {item[0] for item in DOMAINS}
    if focus is not None and focus not in ids:
        raise ExplorationError('choose one of the four domains')
    if skip and (focus is not None or selections):
        raise ExplorationError('skip cannot be combined with selections')
    if selections is None:
        selections = []
    if not isinstance(selections, list):
        raise ExplorationError('selections must be a list')
    rows = {r['id']: r for r in quick_start.prefilled(session)}
    domains = [{'id': key, 'label': label, 'description': desc, 'items': []}
               for key, label, desc in DOMAINS]
    seen = set()
    for item in selections:
        if not isinstance(item, dict) or set(item) != {'domain', 'topic_id'} \
                or item['domain'] not in ids or item['topic_id'] not in rows:
            raise ExplorationError('each selection needs a valid domain and topic')
        pair = (item['domain'], item['topic_id'])
        row = rows[item['topic_id']]
        if pair in seen or row['state'] == 'excluded' or not row['values']:
            raise ExplorationError('duplicate, excluded or unsupported selection')
        seen.add(pair)
        target = next(d for d in domains if d['id'] == item['domain'])
        target['items'].append({'topic_id': row['id'], 'label': row['label'],
                                'state': row['state'], 'values': copy.deepcopy(row['values'])})
    return {'mode': 'four_domains', 'status': 'skipped' if skip else 'ready',
            'domains': domains, 'focus': focus, 'evidence': evidence,
            'notice': '任选领域、跳过或暂停；空白是未知，不评价人格、阶段或健康。'}


def blindspots(session):
    """Offer evidence conflicts and unreviewed areas, not claims about the person."""
    review = knowledge_map(session)
    return {'mode': 'blindspots', 'conflicts_to_review': review['conflicts'],
            'areas_not_yet_confirmed': review['unknown'],
            'hypotheses_to_check': review['hypotheses'],
            'notice': '这些只是证据冲突或未核区域；不能据此断言你有某种盲点或人格特征。'}


def deep_interview(session, *, topic_id=None):
    """Offer at most one open question; default never repeats a known or skipped topic."""
    rows = quick_start.prefilled(session)
    if topic_id is not None:
        row = next((r for r in rows if r['id'] == topic_id), None)
        if not row or row['state'] == 'excluded':
            raise ExplorationError('choose an available, non-excluded topic')
        return {'mode': 'deep_interview', 'question': {'topic_id': row['id'], 'label': row['label'],
                'prompt': '你希望从哪个方面深入这个话题？可以补充、跳过或暂停。',
                'reason': 'user_selected'}, 'question_count': 1}
    for state in ('unresolved', 'missing', 'source_review_pending', 'details_pending'):
        for row in rows:
            if row['state'] == state and not row['decision']:
                return {'mode': 'deep_interview', 'question': {'topic_id': row['id'], 'label': row['label'],
                        'prompt': '在已读范围中尚未确认；你愿意补充哪一点？也可跳过或暂停。',
                        'reason': state}, 'question_count': 1}
    return {'mode': 'deep_interview', 'question': None, 'question_count': 0}


def explore(session, mode, **options):
    if mode == 'known_unknown':
        if options:
            raise ExplorationError('known/unknown mode takes no options')
        return {'mode': mode, **knowledge_map(session)}
    if mode == 'four_domains':
        return four_domains(session, **options)
    if mode == 'blindspots':
        if options:
            raise ExplorationError('blindspot mode takes no options')
        return blindspots(session)
    if mode == 'deep_interview':
        return deep_interview(session, **options)
    raise ExplorationError('choose an available optional mode')
