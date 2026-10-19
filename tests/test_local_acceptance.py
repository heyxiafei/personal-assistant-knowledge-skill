"""Fictional annotations exercise storage/planning, not automatic extraction or media decoding."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/maintain-personal-context/scripts'
sys.path.insert(0, str(SCRIPTS))
import context_store as store
import first_run

RAW = json.loads((ROOT / 'tests/fixtures/local-acceptance-input.json').read_text())
FIRST = json.loads((ROOT / 'tests/fixtures/first-run.json').read_text())['rich']


def annotated(index, rid, statement, supersedes=(), kind='fact', status='confirmed'):
    """An explicit test annotation; no claim that the helper inferred this text."""
    source = RAW['events'][index]
    confirmed = status == 'confirmed'
    return {'id': rid, 'category': 'daily-projects', 'layer': 'profile' if confirmed else 'daily',
            'kind': kind, 'status': status, 'text': statement,
            'sources': [{'label': source['id'], 'locator': '完整用户原话' if source['role'] == 'user' else '助手候选'}],
            'event_date': None, 'record_date': source['date'], 'valid_from': None,
            'confirmed_on': source['date'] if confirmed else None,
            'confirmation': source['id'] + '：保存对应状态' if confirmed else None,
            'supersedes': list(supersedes)}


class LocalAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'private-context'
        store.initialize(self.root)
        self.original = annotated(0, 'design-delivered', RAW['events'][0]['text'])
        self.sample = annotated(1, 'sample-reported', RAW['events'][1]['text'], ['design-delivered'])
        # One current cancellation clause; preserve future non-reopening instructions
        # in the full evidence instead of labelling the whole mixed message current.
        cancel_clause = RAW['events'][2]['text'].split('，', 1)[0]
        self.cancelled = annotated(2, 'production-cancelled', cancel_clause, ['sample-reported'])
        self.cancelled['assertion'] = {
            'state': 'observed', 'author_role': 'user',
            'source': copy.deepcopy(self.cancelled['sources'][0]),
            'source_text': RAW['events'][2]['text'], 'statement': cancel_clause}

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, *records):
        bundle = {'version': 1, 'records': list(records)}
        return store.apply_plan(self.root, store.make_plan(self.root, bundle))

    def chain(self):
        for record in (self.original, self.sample, self.cancelled):
            self.apply(record)

    def test_delivery_manufacture_report_and_cancellation_preserve_distinct_evidence(self):
        self.chain()
        records = store.parse((self.root / 'PERSON_PROFILE.md').read_text())[0]
        by_id = {r['id']: r for r in records}
        self.assertEqual([by_id[rid]['status'] for rid in ('design-delivered', 'sample-reported', 'production-cancelled')],
                         ['superseded', 'superseded', 'confirmed'])
        self.assertEqual(by_id['design-delivered']['superseded_by'], 'sample-reported')
        self.assertEqual(by_id['sample-reported']['superseded_by'], 'production-cancelled')
        self.assertIn('没有验收', by_id['sample-reported']['text'])
        self.assertIn('没有公开发布', by_id['sample-reported']['text'])
        self.assertEqual(by_id['sample-reported']['sources'][0]['label'], 'fictional-m2')
        self.assertIsNone(by_id['sample-reported']['event_date'])
        self.assertEqual(by_id['production-cancelled']['assertion']['source_text'], RAW['events'][2]['text'])
        self.assertIn('以后读到旧设计也不要重开',
                      by_id['production-cancelled']['assertion']['source_text'])

    def test_duplicate_and_old_design_replay_do_not_reactivate_cancelled_production(self):
        self.chain()
        before = store.inventory(self.root)
        self.assertEqual(self.apply(self.cancelled)['status'], 'no_change')
        replay = dict(self.original, id='old-design-replayed', record_date='2026-04-05')
        result = self.apply(replay)
        self.assertEqual(result['status'], 'no_change')
        self.assertEqual(result['outcome'][0]['status'], 'historical_duplicate')
        self.assertEqual(store.inventory(self.root), before)

    def test_optional_opportunity_remains_pending_recommendation(self):
        self.chain()
        suggestion = annotated(3, 'optional-batch', RAW['events'][3]['text'],
                               kind='recommendation', status='pending')
        self.apply(suggestion)
        daily = self.root / 'daily/2026-04-04.md'
        records = store.parse(daily.read_text())[0]
        self.assertEqual((records[0]['kind'], records[0]['status']), ('recommendation', 'pending'))
        current = store.parse((self.root / 'PERSON_PROFILE.md').read_text())[0]
        self.assertEqual([r['id'] for r in current if r['status'] == 'confirmed'], ['production-cancelled'])

    def test_cancelled_state_blocks_today_advance_even_when_an_old_goal_is_known(self):
        data = copy.deepcopy(FIRST)
        data['claims'].append({'key': 'display_state', 'value': 'cancelled',
                               'source_id': 'fictional-m3', 'source_kind': 'conversation',
                               'author_role': 'user', 'evidence_tier': 'direct',
                               'recorded_at': '2026-04-03T09:00:00+00:00'})
        data['recommendations'] = [dict(data['recommendations'][0], scope='project', project_context={
            'project_key': 'display_state', 'status': 'cancelled', 'action_type': 'advance',
            'allowed_actions': ['advance'], 'source_ids': ['fictional-m3']})]
        with self.assertRaises(first_run.ResultError):
            first_run.build_result(data)
        data['recommendations'][0]['project_context'].update(action_type='archive', allowed_actions=['archive'])
        result = first_run.build_result(data)
        self.assertEqual(result['actions'][0]['project_context']['action_type'], 'archive')

    def test_stale_cancellation_plan_keeps_manual_note_then_replans(self):
        self.apply(self.original)
        plan = store.make_plan(self.root, {'version': 1, 'records': [self.sample]})
        target = self.root / 'PERSON_PROFILE.md'
        target.write_text(target.read_text() + '\n' + RAW['manual_note'] + '\n')
        before = target.read_bytes()
        with self.assertRaises(store.ContextError):
            store.apply_plan(self.root, plan)
        self.assertEqual(target.read_bytes(), before)
        self.apply(self.sample)
        self.assertIn(RAW['manual_note'], target.read_text())

    def test_failed_multi_file_update_restores_original_and_keeps_source_fixture(self):
        self.apply(self.original)
        before = store.inventory(self.root)
        raw_before = (ROOT / 'tests/fixtures/local-acceptance-input.json').read_bytes()
        note = annotated(1, 'sample-log', '来源自述一个小样；未核验实物。')
        note['layer'] = 'daily'
        plan = store.make_plan(self.root, {'version': 1, 'records': [self.sample, note]})
        original = store.atomic_write
        calls = []
        def fail_second(path, payload):
            calls.append(path)
            if len(calls) == 2:
                raise PermissionError('fictional write failure')
            return original(path, payload)
        with patch.object(store, 'atomic_write', side_effect=fail_second):
            with self.assertRaises(PermissionError):
                store.apply_plan(self.root, plan)
        self.assertEqual(store.inventory(self.root), before)
        self.assertEqual((ROOT / 'tests/fixtures/local-acceptance-input.json').read_bytes(), raw_before)


if __name__ == '__main__':
    unittest.main()
