"""Fictional adult annotations: preview -> interview -> confirmation -> local readback."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/maintain-personal-context/scripts'))
import context_store as store
import first_run
import interview as m
import space_state


def claim(key, value, sid='fictional-u1', date='2026-06-01T09:00:00+00:00'):
    return dict(key=key, value=value, source_id=sid, recorded_at=date,
                source_kind='conversation', author_role='user', evidence_tier='direct')


def data():
    return {'entry': {'status':'unavailable', 'reason':'合成预览；未访问真实云端'},
            'claims':[claim('name','样例成人A'), claim('current_situation','我在整理纸偶展览'),
                      claim('current_problem','整理一页演示说明')],
            'recommendations':[], 'coverage_gaps':['仅合成成年人材料；真实账户与附件未读'],
            'source_review':[{'role':r,'status':'not_found_in_checked_scope',
                              'scope':'本次合成输入，2026-06-01；非真实账户',
                              'reason':'指定合成材料没有该类主档'} for r in first_run.SOURCE_ROLES]}


def record():
    return {'id':'fictional-adult-role','category':'identity','layer':'profile','kind':'fact',
            'status':'confirmed','text':'我是一位成年自由插画师。',
            'sources':[{'label':'fictional-save-u1','locator':'完整自述'}],
            'event_date':None,'record_date':'2026-06-02','valid_from':None,
            'confirmed_on':'2026-06-02','confirmation':'fictional-save-u2：确认保存所示条目','supersedes':[]}


class InterviewTests(unittest.TestCase):
    def test_preview_is_source_backed_and_does_not_mutate_input(self):
        d=data(); before=copy.deepcopy(d); s=m.start(d); view=m.draft(s)
        self.assertEqual(d,before)
        self.assertEqual(view['phase'],'preview')
        self.assertIn('fictional\\-u1',view['markdown'])
        self.assertEqual(view['question']['id'],'goal')  # reliable situation not asked again
        self.assertEqual(len(view['coverage']['items']),4)
        self.assertFalse(s.get('confirmation'))
        self.assertEqual([x for x in view['markdown'].splitlines() if x.startswith('## ')][:4],
                         ['## 你是谁','## 你在做什么','## 你接下来做什么（候选，最多三项）','## 建议'])

    def test_empty_core_interview_has_at_most_four_single_questions(self):
        d=data(); d['claims']=[]; s=m.start(d); seen=[]
        while (q:=m.question(s)):
            self.assertTrue(q['free_text']); self.assertEqual(len(q['options']),3)
            seen.append(q['id'])
            s=m.respond(s,q['id'],'uncertain','fictional-submit-'+q['id'])
        self.assertEqual(seen,['situation','goal','constraints','collaboration'])
        self.assertEqual(s['data']['claims'],[])
        self.assertFalse(s.get('confirmation'))

    def test_all_core_topics_known_do_not_reask_under_an_old_field_name(self):
        d=data(); d['claims']=[c for c in d['claims'] if c['key']!='current_problem']
        d['claims'] += [claim('near_term_goal','整理一页说明'),claim('time_resources','每周半小时'),
                        claim('collaboration_preference','先简短列选项')]
        s=m.start(d)
        self.assertIsNone(m.question(s))
        self.assertIsNotNone(m.result(s)['priority']['fact'])
        self.assertNotIn('缺当前问题',m.draft(s)['markdown'])

    def test_installed_helper_can_checkpoint_beside_install_but_not_inside_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); dest=root/'installed-skills'
            subprocess.run([sys.executable,'-B',str(ROOT/'scripts/install.py'),'install','--dest',str(dest)],
                           check=True,capture_output=True)
            script="""import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]); import interview,space_state
d=json.load(sys.stdin); s=interview.start(d,checkpoint_consent='fictional-consent')
p=Path(sys.argv[2]); interview.checkpoint(p,s); assert p.is_file()
try: interview.checkpoint(Path(sys.argv[1]).parent/'forbidden-checkpoint.json',s)
except space_state.StateError: pass
else: raise AssertionError('installed Skill must remain protected')
"""
            completed=subprocess.run([sys.executable,'-B','-c',script,
                                      str(dest/'maintain-personal-context/scripts'),str(root/'checkpoint.json')],
                                     input=json.dumps(data()),text=True,capture_output=True)
            self.assertEqual(completed.returncode,0,completed.stderr)

    def test_answers_skip_unknown_and_no_save_never_imply_agreement(self):
        s=m.start(data())
        s=m.respond(s,'goal','answer','fictional-submit-1',value='近期先做一页预览',
                    source_id='fictional-u2',recorded_at='2026-06-02T09:00:00+00:00')
        self.assertTrue(s['advice_needs_refresh'])
        s=m.respond(s,'constraints','skip','fictional-submit-2')
        secret='SYNTHETIC-DO-NOT-PERSIST-77'
        s=m.respond(s,'collaboration','do_not_save','fictional-submit-3',value=secret)
        self.assertIsNone(m.question(s))
        self.assertNotIn(secret,json.dumps(s))
        self.assertFalse(any(c['key'] in {'time_resources','collaboration_preference'} for c in s['data']['claims']))
        self.assertFalse(s.get('confirmation'))

    def test_replayed_reply_idempotent_and_changed_replay_rejected(self):
        s=m.start(data())
        args=dict(value='先完成一页预览',source_id='fictional-u2',recorded_at='2026-06-02T09:00:00+00:00')
        done=m.respond(s,'goal','answer','fictional-submit-1',**args)
        self.assertEqual(m.respond(done,'goal','answer','fictional-submit-1',**args),done)
        with self.assertRaises(m.InterviewError):
            m.respond(done,'goal','answer','fictional-submit-1',**dict(args,value='不同回答'))

    def test_readable_unread_sources_continue_before_questions_denied_never_retried(self):
        d=data(); d['source_review'][0].update(status='partial',reason='还有获准正文可续读')
        self.assertIsNone(m.question(m.start(d)))
        d['source_review'][0].update(status='permission_denied',reason='当前权限拒绝')
        s=m.start(d); view=m.draft(s)
        self.assertIsNone(view['question'])
        self.assertIn('personal_profile',view['coverage']['blocked_roles'])
        self.assertIn('当前权限拒绝',view['markdown'])

    def test_correction_rebuilds_portrait_and_invalidates_advice_and_confirmation(self):
        d=data(); d['claims'].append(claim('time_resources','每天两小时'))
        d['recommendations']=[dict(label='按两小时排版',basis_keys=['time_resources'],first_step='整理版面',
                                   confirmation_condition='仅建议',scope='general')]
        s=m.start(d); old=m.draft(s,target='fictional-target',reviewed_plan={})['preview_token']
        s=m.confirm(s,old,'fictional-target','fictional-confirm',{})
        new=m.correct(s,'time_resources','每周只有半小时','fictional-correction',
                      '2026-06-02T09:00:00+00:00','每天两小时')
        self.assertNotIn('confirmation',new)
        self.assertEqual(new['data']['recommendations'],[])
        self.assertNotIn('每天两小时',m.draft(new)['markdown'])
        self.assertIn('每周只有半小时',m.draft(new)['markdown'])
        self.assertIn('每周只有半小时',json.dumps(first_run.reconcile_claims(new['data']['claims']),ensure_ascii=False))
        with self.assertRaises(m.InterviewError): m.confirm(new,old,'fictional-target','fictional-confirm',{})
        with self.assertRaises(m.InterviewError):
            m.confirm(new,m.draft(new)['preview_token'],'fictional-target','fictional-confirm',{})
        ready=m.set_advice(new,[])
        self.assertFalse(ready['advice_needs_refresh'])

    def test_untimed_or_ambiguous_correction_retains_conflict(self):
        d=data(); d['claims'] += [claim('near_term_goal','方案甲'),claim('near_term_goal','方案乙','fictional-u2')]
        s=m.start(d); self.assertEqual(m.question(s)['reason'],'unresolved')
        with self.assertRaises((m.InterviewError,ValueError)):
            m.respond(s,'goal','answer','fictional-submit',value='方案乙',source_id='fictional-u3',recorded_at='2026-06-02')
        unresolved=m.respond(s,'goal','answer','fictional-submit',value='方案乙',source_id='fictional-u3',
                             recorded_at='2026-06-01T09:00:00+00:00',correction_of='方案甲')
        self.assertIn('near_term_goal',m.draft(unresolved)['unresolved_keys'])

    def test_third_party_and_unknown_answer_annotations_are_not_user_facts(self):
        for role in ('third_party','unknown'):
            s=m.respond(m.start(data()),'goal','answer','fictional-quote',value='同事推测的目标',
                        source_id='fictional-u2',recorded_at='2026-06-02T09:00:00+00:00',author_role=role)
            self.assertIn('near_term_goal',m.draft(s)['unresolved_keys'])
            known=first_run.reconcile_claims(s['data']['claims'])['facts']
            self.assertFalse(any(f['key']=='near_term_goal' and f['status']=='known' for f in known))

    def test_paused_project_cannot_receive_advancing_advice_after_interview(self):
        d=data(); d['claims'].append(claim('fictional-project','paused'))
        s=m.start(d)
        advice=[dict(label='推进旧工程',basis_keys=['fictional-project'],first_step='制造',
                     confirmation_condition='已暂停',scope='project',project_context={
                         'project_key':'fictional-project','status':'paused','source_ids':['fictional-u1'],
                         'action_type':'advance','allowed_actions':['advance']})]
        with self.assertRaises(first_run.ResultError): m.set_advice(s,advice)

    def test_checkpoint_resume_only_remaining_gap_and_protect_same_revision_manual_edit(self):
        s=m.start(data(),checkpoint_consent='fictional-consent')
        s=m.respond(s,'goal','skip','fictional-skip')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp).resolve()/'interview.json'; m.checkpoint(p,s)
            resumed,h=m.load_checkpoint(p)
            self.assertEqual(m.question(resumed)['id'],'constraints')
            new=m.respond(resumed,'constraints','uncertain','fictional-unsure')
            manual=json.loads(p.read_text()); manual['human_note']='keep this manual edit'
            p.write_text(json.dumps(manual)); before=p.read_bytes()
            with self.assertRaises(space_state.StateError): m.checkpoint(p,new,expected_hash=h)
            self.assertEqual(p.read_bytes(),before)

    def test_checkpoint_requires_consent_and_discard_leaves_no_new_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'state.json'; s=m.start(data())
            with self.assertRaises(m.InterviewError): m.checkpoint(p,s)
            self.assertFalse(p.exists())
            closed=m.discard(s)
            self.assertIsNone(closed['data'])
            with self.assertRaises(m.InterviewError): m.checkpoint(p,closed)
            self.assertFalse(p.exists())

    def test_resume_does_not_reactivate_saved_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp).resolve()/'state.json'
            s=m.start(data(),checkpoint_consent='fictional-checkpoint-consent')
            view=m.draft(s,target='fictional-target',reviewed_plan={})
            s=m.confirm(s,view['preview_token'],'fictional-target','fictional-save',{})
            m.checkpoint(p,s)
            resumed,h=m.load_checkpoint(p)
            self.assertEqual(resumed['phase'],'preview')
            self.assertNotIn('confirmation',resumed)
            self.assertTrue(resumed['resumed_confirmation_invalidated'])
            m.checkpoint(p,resumed,expected_hash=h)
            closed=m.discard(s)
            self.assertIsNone(closed['data'])
            with self.assertRaises(m.InterviewError): m.checkpoint(p,closed)
            self.assertTrue(p.exists())  # prior consented checkpoint is not secretly deleted

    def test_confirm_exact_plan_target_and_preview_then_write_readback_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()/'context'; store.initialize(root)
            plan=store.make_plan(root,{'version':1,'records':[record()]})
            s=m.start(data()); before=store.inventory(root)
            with self.assertRaises(m.InterviewError): m.apply_local(s,root,plan)
            self.assertEqual(store.inventory(root),before)
            view=m.draft(s,target=str(root),reviewed_plan=plan)
            self.assertIn('fictional\\-adult\\-role',view['markdown'])
            s=m.confirm(s,view['preview_token'],str(root),'fictional-save-u2',plan)
            with self.assertRaises(m.InterviewError): m.apply_local(s,Path(tmp).resolve()/'other',plan)
            tampered=copy.deepcopy(plan); tampered['bundle']['records'][0]['text']='不同内容'
            with self.assertRaises(m.InterviewError): m.apply_local(s,root,tampered)
            saved=m.apply_local(s,root,plan)
            actual=store.parse((root/'PERSON_PROFILE.md').read_text())[0]
            self.assertEqual(actual[0]['text'],record()['text'])
            hashes=store.inventory(root)
            self.assertEqual(m.apply_local(saved,root,plan),saved)
            self.assertEqual(store.inventory(root),hashes)

    def test_manual_library_edit_and_injected_failure_retain_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()/'context'; store.initialize(root)
            plan=store.make_plan(root,{'version':1,'records':[record()]})
            s=m.start(data()); s=m.confirm(s,m.draft(s,target=str(root),reviewed_plan=plan)['preview_token'],str(root),'fictional-save-u2',plan)
            p=root/'PERSON_PROFILE.md'; p.write_text(p.read_text()+'\n手工备注，保留。\n')
            before=store.inventory(root)
            with self.assertRaises(store.ContextError): m.apply_local(s,root,plan)
            self.assertEqual(store.inventory(root),before)
            new_plan=store.make_plan(root,{'version':1,'records':[record()]})
            s=m.revised(s); s=m.confirm(s,m.draft(s,target=str(root),reviewed_plan=new_plan)['preview_token'],str(root),'fictional-save-u3',new_plan)
            original=store.atomic_write; calls=0
            def fail_once(path, content):
                nonlocal calls
                calls+=1
                if calls==2: raise PermissionError('injected first content write failure')
                return original(path,content)
            with patch.object(store,'atomic_write',side_effect=fail_once):
                with self.assertRaises((PermissionError,store.ContextError)): m.apply_local(s,root,new_plan)
            self.assertEqual(store.inventory(root),before)


if __name__=='__main__': unittest.main()
