"""Synthetic end-to-end onboarding, isolated private targets, no host accounts."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/maintain-personal-context/scripts'
sys.path.insert(0, str(SCRIPTS))
import onboarding as m
import native_index
import space_state

FIXTURE = ROOT / 'tests/fixtures/quick-start.json'
NATIVE = ROOT / 'tests/fixtures/native-history.json'
STAMP = '2026-06-02T09:00:00+00:00'


def data(case='new'):
    return json.loads(FIXTURE.read_text())[case]


def answer(value='做一张草图', source='form-reply', correction=None):
    row = {'topic_id': 'goal', 'action': 'answer', 'key': 'near_term_goal',
           'value': value, 'recorded_at': STAMP}
    if correction:
        row['correction_of'] = correction
    return row


def make_index(path):
    fixture = json.loads(NATIVE.read_text())
    result = m.normalize_source(fixture['reads'], fixture['thread_id'], fixture['host_id'])
    db = sqlite3.connect(str(path))
    db.execute('create table messages(host_id,thread_id,message_id,role,read_status,text)')
    for row in result['messages']:
        db.execute('insert into messages values (?,?,?,?,?,?)',
                   (row['host_id'],row['thread_id'],row['message_id'],row['author_role'],row['read_status'],row['text']))
    db.commit(); db.close(); os.chmod(path, 0o600)
    return result


class OnboardingTests(unittest.TestCase):
    def test_empty_skeleton_before_read_and_recovery_from_read_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); p=root/'state.json'
            skeleton=m.initialize(); self.assertIsNotNone(skeleton['library'])
            self.assertEqual(skeleton['library']['advice_stage'],'initial')
            self.assertEqual(skeleton['library']['advice'][0]['basis']['kind'],'generic')
            self.assertEqual(len(skeleton['library']['table']),8)
            h=m.save(p,skeleton)
            with self.assertRaises(FileNotFoundError):m.ingest(skeleton,data(),source_index=root/'missing.sqlite3')
            loaded,digest=m.load(p);self.assertEqual(digest,h);self.assertEqual(loaded,skeleton)
            filled=m.ingest(loaded,data('existing'));m.save(p,filled,previous=loaded,expected_hash=digest)
            self.assertEqual(m.ingest(filled,data('existing')),filled)
            self.assertEqual(m.preview(filled)['core_status'],'incomplete')

    def test_delegation_text_is_not_direct_user_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            idx=Path(d).resolve()/'index.sqlite3'; result=make_index(idx)
            user=next(x for x in result['messages'] if x['author_role']=='user')
            db=sqlite3.connect(str(idx));db.execute('update messages set text=? where message_id=?',('<codex_delegation>示例任务</codex_delegation>',user['message_id']));db.commit();db.close()
            sid=m.source_id(user['host_id'],user['thread_id'],user['message_id'])
            x=data();x['claims']=[{'key':'skills','value':'待核线索','source_id':sid,'recorded_at':STAMP,
                  'source_kind':'conversation','author_role':'user','evidence_tier':'direct'}]
            with self.assertRaises(m.OnboardingError):m.start(x,source_index=idx)
            x['claims'][0]['evidence_tier']='summary'
            state=m.start(x,source_index=idx)
            row=next(r for r in m.preview(state)['prefilled'] if r['id']=='capability')
            self.assertEqual(row['values'][0]['evidence'],'inferred')

    def test_reingesting_old_source_preserves_newer_explicit_correction(self):
        earlier=data('all_known');old=next(c['value'] for c in earlier['claims'] if c['key']=='near_term_goal')
        state=m.start(earlier)
        row=answer('较新的明确目标',correction=old);row['recorded_at']='2026-06-03T09:00:00+00:00'
        updated=m.submit(state,event_id='correction',responses=[row],source_id_value='newer-user')
        rescanned=m.ingest(updated,earlier)
        goal=next(row for row in rescanned['library']['table'] if row['id']=='goal')
        self.assertEqual([v['value'] for v in goal['values'] if v['current']],['较新的明确目标'])
        self.assertEqual(rescanned['events'],updated['events'])

    def test_incoming_scan_cannot_silently_remove_a_source_refusal(self):
        d=data();d['source_review'][0].update(status='permission_denied',reason='虚构拒绝')
        state=m.start(d)
        with self.assertRaises(m.OnboardingError):m.ingest(state,data())

    def test_skip_or_response_transitions_to_deeper_guidance_without_blocking(self):
        s=m.initialize();self.assertIsNotNone(s['library']);self.assertEqual(s['library']['advice_stage'],'initial')
        s=m.submit(s,event_id='skip',responses=[{'topic_id':'goal','action':'skip'}],source_id_value='user')
        self.assertEqual(s['library']['advice_stage'],'after_user_input')
        self.assertEqual(s['library']['deeper_guidance']['priority_tradeoff'],'待本人选择')
        self.assertTrue(s['library']['deeper_guidance']['actions'])

    def test_legacy_prefill_migrates_only_when_explicitly_initialized(self):
        session=m.quick_start.start(data('existing'))
        legacy={'schema_version':1,'kind':'local_onboarding','revision':0,'stage':'table',
                'session':session,'events':{},'excluded_topics':[],'source_index':None,'library':None}
        converted=m.ensure_skeleton(legacy)
        self.assertEqual(converted['stage'],'library')
        self.assertEqual(converted['session'],legacy['session'])
        self.assertEqual(converted['revision'],1)
        self.assertEqual(m.ensure_skeleton(converted),converted)
        self.assertEqual(legacy['stage'],'table')

    def test_user_role_cannot_be_assumed_to_be_human(self):
        with tempfile.TemporaryDirectory() as d:
            idx=Path(d).resolve()/'index.sqlite3'; result=make_index(idx)
            user=next(x for x in result['messages'] if x['author_role']=='user')
            sid=m.source_id(user['host_id'],user['thread_id'],user['message_id'])
            x=data();x['claims']=[{'key':'skills','value':'待核线索','source_id':sid,'recorded_at':STAMP,
                'source_kind':'conversation','author_role':'user','evidence_tier':'direct'}]
            with self.assertRaises(m.OnboardingError):m.start(x,source_index=idx)
            x['claims'][0].update(human_origin_verified=True,origin_evidence='合成用户原话回执')
            s=m.start(x,source_index=idx)
            row=next(r for r in m.preview(s)['prefilled'] if r['id']=='capability')
            self.assertEqual(row['values'][0]['evidence'],'sourced_user')

    def test_index_receipts_and_preserve_existing_edits(self):
        f=json.loads(NATIVE.read_text())
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            for i, read in enumerate(f['reads']):
                row={'kind':'page','thread_id':f['thread_id'],'request_host_id':f['host_id'],
                     'request_cursor':read['request_cursor'],'payload':read['payload']}
                path=root/('receipt-%06d.json' % (i+1));path.write_text(json.dumps(row));path.chmod(0o600)
            path=root/'receipt-000098.json';path.write_text(json.dumps({'kind':'thread_outcome','thread_id':f['thread_id'],'request_host_id':f['host_id'],'terminal':True,'error':None}));path.chmod(0o600)
            path=root/'receipt-000099.json';path.write_text(json.dumps({'kind':'capture_contract','max_output_chars_per_item':20000}));path.chmod(0o600)
            created=native_index.build(root);self.assertEqual(created['status'],'created')
            self.assertEqual(created['user_messages'],2);self.assertEqual(created['assistant_messages'],2)
            idx=root/'source-index/source-index.sqlite3';self.assertEqual(len(m.read_index(idx)['sources']),4)
            self.assertEqual(native_index.build(root)['status'],'unchanged')
            edited=root/'source-index/threads.json';edited.write_text('[]');edited.chmod(0o600);before=edited.read_bytes()
            with self.assertRaises(native_index.IndexError):native_index.build(root)
            self.assertEqual(edited.read_bytes(),before)

    def test_missing_capture_limit_does_not_claim_full_text(self):
        f=json.loads(NATIVE.read_text())
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            for i,read in enumerate(f['reads']):
                row={'kind':'page','thread_id':f['thread_id'],'request_host_id':f['host_id'],
                     'request_cursor':read['request_cursor'],'payload':read['payload']}
                p=root/('receipt-%06d.json' % (i+1));p.write_text(json.dumps(row));p.chmod(0o600)
            p=root/'receipt-000099.json';p.write_text(json.dumps({'kind':'thread_outcome','thread_id':f['thread_id'],'request_host_id':f['host_id'],'terminal':True,'error':None}));p.chmod(0o600)
            result=native_index.build(root)
            self.assertEqual(result.get('complete_text_ranges',0),0)
            threads=json.loads((root/'source-index/threads.json').read_text())
            self.assertEqual(threads[0]['coverage']['thread_text_range_complete'],'unknown')

    def test_index_failed_or_missing_outcome_cannot_claim_complete(self):
        f=json.loads(NATIVE.read_text())
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            for i,read in enumerate(f['reads']):
                row={'kind':'page','thread_id':f['thread_id'],'request_host_id':f['host_id'],
                     'request_cursor':read['request_cursor'],'payload':read['payload']}
                p=root/('receipt-%06d.json' % (i+1));p.write_text(json.dumps(row));p.chmod(0o600)
            p=root/'receipt-000099.json';p.write_text(json.dumps({'kind':'capture_contract','max_output_chars_per_item':20000}));p.chmod(0o600)
            result=native_index.build(root)
            self.assertEqual(result.get('complete_text_ranges',0),0)
            threads=json.loads((root/'source-index/threads.json').read_text())
            self.assertIs(threads[0]['coverage']['thread_text_range_complete'],False)
            self.assertIn('capture_outcome_missing_or_failed',threads[0]['coverage']['limitations'])

    def test_native_receipts_index_table_submit_and_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); index = root/'index.sqlite3'; statepath = root/'state.json'
            messages = make_index(index)['messages']
            user = next(x for x in messages if x['author_role'] == 'user')
            sid = m.source_id(user['host_id'],user['thread_id'],user['message_id'])
            d = data(); d['claims'] = [{'key':'skills','value':'木艺经验','source_id':sid,
                'recorded_at':STAMP,'source_kind':'conversation','author_role':'user','evidence_tier':'direct',
                'human_origin_verified':True,'origin_evidence':'合成来源经核对为用户原话'}]
            s = m.start(d,source_index=index)
            self.assertEqual(s['stage'],'library'); self.assertTrue(s['library']['provisional']); self.assertEqual(s['library']['advice_stage'],'initial')
            row = next(x for x in m.preview(s)['prefilled'] if x['id']=='capability')
            self.assertEqual(row['values'][0]['evidence'],'sourced_user')
            self.assertNotEqual(m.preview(s)['next_question']['topic_id'],'capability')
            digest=m.save(statepath,s)
            resumed, loaded_hash=m.load(statepath); self.assertEqual(digest,loaded_hash)
            initial=m.submit(resumed,event_id='form-1',responses=[answer()],source_id_value='form-source')
            self.assertEqual(initial['stage'],'library')
            self.assertTrue(initial['library']['provisional'])
            self.assertTrue(initial['library']['advice'])
            self.assertEqual(initial['library']['account_enumeration_complete'],'unknown')
            m.save(statepath,initial,previous=resumed,expected_hash=loaded_hash)
            again, digest2=m.load(statepath)
            self.assertEqual(m.submit(again,event_id='form-1',responses=[answer()],source_id_value='form-source'),again)
            self.assertEqual(stat.S_IMODE(statepath.stat().st_mode),0o600)
            self.assertTrue(digest2)

    def test_correction_and_exclusion_update_same_library(self):
        s=m.start(data())
        s=m.submit(s,event_id='one',responses=[answer('旧目标')],source_id_value='user-one')
        corrected=m.submit(s,event_id='two',responses=[dict(answer('新目标', correction='旧目标'),recorded_at='2026-06-03T09:00:00+00:00')],source_id_value='user-two')
        goals=[x for x in corrected['library']['table'] if x['id']=='goal'][0]
        self.assertEqual([x['value'] for x in goals['values'] if x['current']],['新目标'])
        removed=m.submit(corrected,event_id='three',responses=[{'topic_id':'goal','action':'do_not_save'}],source_id_value='user-three')
        self.assertNotIn('新目标',json.dumps(removed,ensure_ascii=False))
        self.assertEqual(next(x for x in removed['library']['table'] if x['id']=='goal')['state'],'excluded')
        with self.assertRaises(m.OnboardingError):
            m.submit(removed,event_id='four',responses=[answer('旧目标')],source_id_value='user-four')

    def test_partial_or_denied_sources_do_not_block_initial_library(self):
        d=data(); d['source_review'][0].update(status='permission_denied',reason='虚构入口被拒绝')
        s=m.start(d); p=m.preview(s)
        self.assertEqual(p['next_question']['state'],'source_review_pending')
        s=m.submit(s,event_id='table',responses=[],source_id_value='user-accept')
        self.assertTrue(s['library']['provisional'])
        self.assertTrue(s['library']['coverage_gaps'])
        self.assertIsNone(m.next_gap(s))  # explicit whole-table skip remains respected

    def test_conflicting_external_edit_or_deletion_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d).resolve()/'state.json'; s=m.start(data()); h=m.save(p,s)
            next_state=m.submit(s,event_id='x',responses=[answer()],source_id_value='source')
            with p.open('a') as f: f.write(' ')
            before=p.read_bytes()
            with self.assertRaises(space_state.StateError):m.save(p,next_state,previous=s,expected_hash=h)
            self.assertEqual(before,p.read_bytes())
            p.unlink()
            with self.assertRaises((space_state.StateError,FileNotFoundError)):
                m.save(p,next_state,previous=s,expected_hash=h)
            self.assertFalse(p.exists())
            with self.assertRaises(m.OnboardingError):m.save(p,m.start(data()))
            self.assertFalse(p.exists())

    def test_interrupted_write_preserves_old_state_and_can_resume(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d).resolve()/'state.json';state=m.start(data());h=m.save(p,state);before=p.read_bytes()
            changed=m.submit(state,event_id='form',responses=[answer()],source_id_value='source')
            with patch.object(space_state.os,'replace',side_effect=OSError('simulated interruption')):
                with self.assertRaises(OSError):m.save(p,changed,previous=state,expected_hash=h)
            self.assertEqual(p.read_bytes(),before)
            saved,old_hash=m.load(p);m.save(p,changed,previous=saved,expected_hash=old_hash)
            self.assertEqual(m.load(p)[0]['stage'],'library')

    def test_manual_library_edit_is_preserved_and_blocks_rebuild(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d).resolve()/'state.json'; state=m.start(data());h=m.save(p,state)
            state2=m.submit(state,event_id='form',responses=[answer()],source_id_value='user-form')
            m.save(p,state2,previous=state,expected_hash=h)
            edited=json.loads(p.read_text());edited['library']['advice']=[{'manual':'keep'}]
            p.write_text(json.dumps(edited));p.chmod(0o600);before=p.read_bytes()
            with self.assertRaises(m.OnboardingError):m.load(p)
            self.assertEqual(before,p.read_bytes())

    def test_source_index_cli_paginates_and_reads_one_selected_source(self):
        with tempfile.TemporaryDirectory() as d:
            idx=Path(d).resolve()/'index.sqlite3';make_index(idx)
            cmd=[sys.executable,'-B',str(SCRIPTS/'onboarding.py'),'source-list','--index',str(idx),'--limit','1']
            result=json.loads(subprocess.run(cmd,capture_output=True,text=True,check=True).stdout)
            self.assertEqual(len(result['sources']),1);self.assertEqual(result['next_offset'],1)
            self.assertNotIn('text',result['sources'][0])
            source=result['sources'][0]['source_id']
            actual=json.loads(subprocess.run([sys.executable,'-B',str(SCRIPTS/'onboarding.py'),'source-read','--index',str(idx),'--source-id',source],capture_output=True,text=True,check=True).stdout)
            self.assertEqual(actual['source_id'],source);self.assertTrue(actual['text'])

    def test_native_claim_must_match_actual_source_and_user_role(self):
        with tempfile.TemporaryDirectory() as d:
            idx=Path(d).resolve()/'index.sqlite3'; result=make_index(idx)
            assistant=next(x for x in result['messages'] if x['author_role']=='assistant')
            sid=m.source_id(assistant['host_id'],assistant['thread_id'],assistant['message_id'])
            x=data();x['claims']=[{'key':'skills','value':'待核内容','source_id':sid,'recorded_at':STAMP,
                'source_kind':'conversation','author_role':'user','evidence_tier':'direct'}]
            with self.assertRaises(m.OnboardingError):m.start(x,source_index=idx)
            x['claims'][0]['source_id']='native-does-not-exist'
            with self.assertRaises(m.OnboardingError):m.start(x,source_index=idx)

    def test_partial_captured_pages_remain_partial(self):
        f=json.loads(NATIVE.read_text()); read=f['reads'][:1]
        result=m.normalize_source(read,f['thread_id'],f['host_id'])
        self.assertIsNot(result['coverage']['thread_text_range_complete'],True)
        self.assertEqual(result['coverage']['account_enumeration_complete'],'unknown')

    def test_answer_requires_real_timezone_and_partial_source_stays_limited(self):
        bad=answer();bad['recorded_at']='2026-06-02'
        with self.assertRaises(m.OnboardingError):
            m.submit(m.start(data()),event_id='x',responses=[bad],source_id_value='source')
        with tempfile.TemporaryDirectory() as d:
            idx=Path(d).resolve()/'index.sqlite3'; result=make_index(idx)
            db=sqlite3.connect(str(idx)); db.execute("update messages set read_status='partial' where role='user'"); db.commit();db.close()
            user=next(x for x in result['messages'] if x['author_role']=='user')
            sid=m.source_id(user['host_id'],user['thread_id'],user['message_id'])
            x=data();x['claims']=[{'key':'skills','value':'木艺经验','source_id':sid,'recorded_at':STAMP,
                'source_kind':'conversation','author_role':'user','evidence_tier':'direct',
                'human_origin_verified':True,'origin_evidence':'合成来源经核对为用户原话'}]
            state=m.start(x,source_index=idx)
            self.assertTrue(any('片段' in item for item in state['session']['data']['coverage_gaps']))

    def test_replayed_id_with_changed_answer_is_rejected(self):
        s=m.submit(m.start(data()),event_id='same',responses=[answer()],source_id_value='source')
        with self.assertRaises(m.OnboardingError):
            m.submit(s,event_id='same',responses=[answer('不同内容')],source_id_value='source')

    def test_untrusted_or_nonprivate_index_and_state_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); idx=root/'index.sqlite3';make_index(idx);idx.chmod(0o644)
            with self.assertRaises(m.OnboardingError):m.read_index(idx)
            root.chmod(0o755)
            with self.assertRaises(m.OnboardingError):m.save(root/'state.json',m.start(data()))

    def test_installed_cli_start_resume_and_submit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();dest=root/'installed'; private=root/'private';private.mkdir(mode=0o700)
            install=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/install.py'),'install','--dest',str(dest)],capture_output=True,text=True,check=True)
            result=json.loads(install.stdout);self.assertFalse(result['first_use']['automatic_install_hook'])
            script=result['first_use']['script'];state=private/'state.json'; inp=private/'input.json';inp.write_text(json.dumps(data()))
            args=[sys.executable,'-B',script,'--state',str(state),'start','--input',str(inp)]
            first=subprocess.run(args,capture_output=True,text=True,check=True);self.assertEqual(json.loads(first.stdout)['stage'],'library')
            again=subprocess.run(args,capture_output=True,text=True,check=True);self.assertEqual(json.loads(again.stdout)['stage'],'library')
            request=private/'submit.json';request.write_text(json.dumps({'event_id':'form','responses':[answer()],'source_id_value':'user-form'}))
            done=subprocess.run([sys.executable,'-B',script,'--state',str(state),'submit','--input',str(request)],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(done.stdout)['stage'],'library')
            resumed=subprocess.run(args,capture_output=True,text=True,check=True);self.assertEqual(json.loads(resumed.stdout)['stage'],'library')

if __name__ == '__main__':
    unittest.main()
