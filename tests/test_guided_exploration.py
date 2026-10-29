"""Synthetic optional exploration; no personal history or external tools."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/maintain-personal-context/scripts'
sys.path.insert(0, str(SCRIPTS))
import onboarding
import guided_exploration as g


def fixture(case='existing'):
    return json.loads((ROOT/'tests/fixtures/quick-start.json').read_text())[case]


class GuidedExplorationTests(unittest.TestCase):
    def test_known_unknown_keeps_direct_inference_and_unknown_separate(self):
        d=fixture('existing')
        d['claims'].append({'key':'near_term_goal','value':'待核猜测','source_id':'fictional-assistant',
            'recorded_at':'2026-06-02T00:00:00+00:00','source_kind':'conversation',
            'author_role':'assistant','evidence_tier':'summary'})
        s=onboarding.start(d);before=copy.deepcopy(s)
        result=g.explore(s['session'],'known_unknown')
        self.assertTrue(any(x['key']=='name' for x in result['known']))
        self.assertTrue(any(x['value']=='待核猜测' for x in result['hypotheses']))
        self.assertFalse(any(x['value']=='待核猜测' for x in result['known']))
        self.assertEqual(result['account_enumeration_complete'],'unknown')
        self.assertEqual(s,before)

    def test_default_preview_exposes_known_unknown_without_starting_optional_mode(self):
        state=onboarding.start(fixture('existing'))
        result=onboarding.preview(state)
        self.assertTrue(result['known_unknown']['known'])
        self.assertEqual(result['known_unknown']['account_enumeration_complete'],'unknown')
        self.assertNotIn('mode',result['known_unknown'])

    def test_four_domains_allow_empty_and_skip_without_guessing(self):
        session=onboarding.initialize()['session']; before=copy.deepcopy(session)
        result=g.four_domains(session)
        self.assertEqual([d['id'] for d in result['domains']], ['mind','body','spirit','vocation'])
        self.assertTrue(all(not d['items'] for d in result['domains']))
        self.assertEqual(g.four_domains(session,skip=True)['status'],'skipped')
        self.assertEqual(session,before)

    def test_four_domains_only_attach_explicit_supported_evidence(self):
        session=onboarding.start(fixture('existing'))['session']
        result=g.four_domains(session,focus='vocation',selections=[{'domain':'vocation','topic_id':'identity'}])
        self.assertEqual(result['focus'],'vocation')
        self.assertEqual(len(result['domains'][3]['items']),1)
        self.assertTrue(result['domains'][3]['items'][0]['values'])
        self.assertTrue(all(not d['items'] for d in result['domains'][:3]))
        with self.assertRaises(g.ExplorationError):g.four_domains(session,focus='unknown')
        with self.assertRaises(g.ExplorationError):g.four_domains(session,selections=[{'domain':'mind','topic_id':'goal'}])
        with self.assertRaises(g.ExplorationError):g.four_domains(session,skip=True,focus='mind')

    def test_blindspots_report_evidence_conflict_not_personality(self):
        s=onboarding.start(fixture('conflict'))['session']; result=g.blindspots(s)
        self.assertEqual(result['conflicts_to_review'][0]['topic_id'],'goal')
        self.assertIn('不能据此断言',result['notice'])
        self.assertNotIn('diagnosis',result)

    def test_deep_interview_one_question_never_reasks_known_by_default(self):
        s=onboarding.start(fixture('existing'))['session']; result=g.deep_interview(s)
        self.assertEqual(result['question_count'],1)
        self.assertNotIn(result['question']['topic_id'],('situation','identity','capability'))
        chosen=g.deep_interview(s,topic_id='identity')
        self.assertEqual(chosen['question']['reason'],'user_selected')
        with self.assertRaises(g.ExplorationError):g.deep_interview(s,topic_id='not-a-topic')

    def test_excluded_topic_not_suggested_or_placed(self):
        s=onboarding.submit(onboarding.initialize(),event_id='x',source_id_value='fictional-user',
                            responses=[{'topic_id':'goal','action':'do_not_save'}])['session']
        self.assertNotEqual(g.deep_interview(s)['question']['topic_id'],'goal')
        with self.assertRaises(g.ExplorationError):g.deep_interview(s,topic_id='goal')
        out=g.blindspots(s);self.assertFalse(any(x['topic_id']=='goal' for x in out['areas_not_yet_confirmed']))

    def test_first_run_entrypoints_route_to_skeleton_first(self):
        root=ROOT / 'skills/maintain-personal-context'
        paths=[ROOT/'README.md', ROOT/'docs/QUICKSTART.md', root/'agents/openai.yaml',
               root/'SKILL.md', root/'references/quick-start.md',
               root/'references/first-run-result.md', root/'references/first-interview.md',
               root/'references/space-onboarding.md']
        for path in paths:
            text=path.read_text()
            with self.subTest(path=path):
                self.assertIn('骨架',text)
                self.assertIn('授权',text)
                self.assertNotIn('确认前不创建或重写正式库',text)
                self.assertNotIn('默认不创建、重写正式知识库',text)
        for path in [ROOT/'README.md', ROOT/'docs/QUICKSTART.md']:
            prompt=path.read_text().split('```text',1)[1].split('```',1)[0]
            self.assertLess(prompt.index('骨架'),prompt.index('读取') if '读取' in prompt else prompt.index('读本次'))
        for path in [root/'references/quick-start.md', root/'references/first-interview.md', root/'references/space-onboarding.md']:
            first=path.read_text().split('\n\n',2)[1]
            self.assertLess(first.index('骨架'),first.index('读取'))

    def test_skip_whole_table_survives_reload_and_explicit_exploration(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d).resolve()/'state.json'
            original=onboarding.initialize(); digest=onboarding.save(path,original)
            skipped=onboarding.submit(original,event_id='skip-all',source_id_value='fictional-user',responses=[])
            self.assertTrue(skipped['table_skipped'])
            self.assertIsNone(onboarding.preview(skipped)['next_question'])
            digest=onboarding.save(path,skipped,previous=original,expected_hash=digest)
            reloaded, loaded_hash=onboarding.load(path)
            self.assertEqual(digest,loaded_hash)
            self.assertIsNone(onboarding.preview(onboarding.ensure_skeleton(reloaded))['next_question'])
            repeated=onboarding.submit(reloaded,event_id='skip-all',source_id_value='fictional-user',responses=[])
            self.assertEqual(reloaded,repeated)
            self.assertEqual(onboarding.save(path,repeated,previous=reloaded,expected_hash=loaded_hash),loaded_hash)
            with_sources=onboarding.ingest(reloaded,fixture('existing'))
            self.assertIsNone(onboarding.preview(with_sources)['next_question'])
            self.assertEqual(g.deep_interview(reloaded['session'],topic_id='goal')['question']['reason'],'user_selected')

    def test_cli_exploration_never_mutates_private_state(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d).resolve()/'state.json';onboarding.save(p,onboarding.initialize());before=p.read_bytes()
            r=subprocess.run([sys.executable,'-B',str(SCRIPTS/'onboarding.py'),'--state',str(p),
                              'explore','--mode','known_unknown'],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(r.stdout)['mode'],'known_unknown')
            self.assertEqual(p.read_bytes(),before)

if __name__ == '__main__':unittest.main()
