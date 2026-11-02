"""Synthetic graph exports; never discover or open real user sources."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import shutil
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/maintain-personal-context/scripts'))
import knowledge_graph as g
import onboarding as o
import quick_start


def state():
    data = json.loads((ROOT / 'tests/fixtures/quick-start.json').read_text())['all_known']
    return o.start(data)


class GraphTests(unittest.TestCase):
    def test_empty_valid_and_legacy_checkpoint(self):
        self.assertEqual(g.project(o.initialize(), '0'*64)['nodes'], [])
        s = state(); s['stage'] = 'table'; s['library'] = None
        self.assertTrue(g.project(s, '0'*64)['nodes'])

    def test_identity_does_not_depend_on_order_or_label(self):
        s = state(); before = g.project(s, 'a'*64)
        s['session']['data']['claims'].reverse()
        s['session']['data']['claims'][0]['value'] = '新的显示名称'
        s['library'] = o._library(s['session'], False, None)
        after = g.project(s, 'b'*64)
        self.assertEqual(before['identity_map'], after['identity_map'])
        self.assertEqual({n['id'] for n in before['nodes']}, {n['id'] for n in after['nodes']})

    def test_only_explicit_edges_and_no_dangling_links(self):
        graph = g.project(state(), 'a'*64)
        ids = {n['id'] for n in graph['nodes']}
        self.assertTrue(graph['edges'])
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in graph['edges']))
        self.assertEqual({e['type'] for e in graph['edges']}, {'supported_by'})
        self.assertTrue(all(e['evidence'] == 'explicit' for e in graph['edges']))

    def test_explicit_correction_and_advice_basis(self):
        s = state(); old = next(c['value'] for c in s['session']['data']['claims'] if c['key']=='near_term_goal')
        s = o.submit(s, event_id='correction', source_id_value='synthetic-new', responses=[{
            'topic_id':'goal','action':'answer','key':'near_term_goal','value':'新目标',
            'recorded_at':'2026-06-03T00:00:00+00:00','correction_of':old}],
            recommendations=[{'label':'合成建议','basis_keys':['near_term_goal'], 'scope':'general',
                              'first_step':'合成一步','confirmation_condition':'本人选择后'}])
        graph = g.project(s, 'a'*64)
        self.assertEqual({e['type'] for e in graph['edges']}, {'supported_by','based_on','supersedes'})
        self.assertTrue(any(n['status']=='superseded' for n in graph['nodes']))

    def test_do_not_save_and_explicit_exclusion(self):
        s = state(); secret = next(c['value'] for c in s['session']['data']['claims'] if c['key']=='near_term_goal')
        removed = o.submit(s,event_id='remove',source_id_value='synthetic',responses=[{'topic_id':'goal','action':'do_not_save'}])
        graph = g.project(removed, 'a'*64)
        self.assertNotIn(secret, json.dumps(graph,ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(g.project(s,'a'*64,['near_term_goal']),ensure_ascii=False))

    def test_replacement_uses_complete_source_identity(self):
        s=state();claims=s['session']['data']['claims']
        old=next(c for c in claims if c['key']=='near_term_goal')
        correction=dict(old,source_id='same-id',value='correct',correction_of=old['value'],
                        recorded_at='2026-06-03T00:00:00+00:00')
        other=dict(old,source_id='same-id',source_kind='saved_material',value='other material')
        claims.extend([correction,other]);s['library']=o._library(s['session'],False,None)
        graph=g.project(s,'a'*64);nodes={n['id']:n for n in graph['nodes']}
        links=[e for e in graph['edges'] if e['type']=='supersedes']
        self.assertTrue(links)
        self.assertEqual({nodes[e['source']]['label'] for e in links},{'correct'})

    def test_ambiguous_advice_does_not_block_evidence_graph(self):
        s=state()
        rec=dict(label='建议甲',basis_keys=['near_term_goal'],scope='general',
                 first_step='一步甲',confirmation_condition='本人选择')
        s=o.submit(s,event_id='advice',responses=[],source_id_value='synthetic',
                   recommendations=[rec,dict(rec,label='建议乙',first_step='一步乙')])
        graph=g.project(s,'a'*64)
        self.assertTrue(any(n['type']=='claim' for n in graph['nodes']))
        self.assertFalse(any(n['type']=='advice' for n in graph['nodes']))
        self.assertTrue(any('缺少独立身份' in x for x in graph['coverage_limits']))

    def test_failed_and_partial_sources_keep_limits(self):
        s = state();s['session']['data']['coverage_gaps'].append('合成来源受阻')
        s['library']=o._library(s['session'],False,None)
        graph = g.project(s,'a'*64)
        self.assertIn('合成来源受阻',graph['coverage_limits'])
        self.assertFalse(graph['full_history_complete'])
        self.assertTrue(all(n['status']=='referenced_only' for n in graph['nodes'] if n['type']=='source'))

    def test_assistant_claim_does_not_inherit_known_user_status(self):
        s=state();c=next(c for c in s['session']['data']['claims'] if c['key']=='skills')
        s['session']['data']['claims'].append(dict(c,source_id='assistant-summary',
            author_role='assistant',evidence_tier='summary',value='另一项猜测'))
        s['library']=o._library(s['session'],False,None)
        graph=g.project(s,'a'*64)
        inferred=next(n for n in graph['nodes'] if n['label']=='另一项猜测')
        self.assertEqual(inferred['status'],'inferred')

    def test_bundle_sensitive_omission_and_paused_state(self):
        data = {'schema_version':1,'title':'synthetic','as_of':'2026-06-01T00:00:00+00:00',
            'sources':[dict(id='s',title='来源',locator='javascript:alert(1)',kind='conversation',read_status='read',
                            time_range='未知',limitations=[],snapshot='不导出的原文')],
            'coverage':dict(scope='合成',limits=[],uncovered=[]),
            'facts':[dict(id='f',text='暂停项目',status='paused',category='ongoing',source_ids=['s'],sensitivity='normal'),
                     dict(id='secret',text='敏感不保存',status='current',category='identity',source_ids=['s'],sensitivity='sensitive')],
            'advice':[],'questions':[]}
        before=copy.deepcopy(data);graph=g.project(data,'a'*64)
        self.assertEqual(data,before)
        self.assertNotIn('敏感不保存',json.dumps(graph,ensure_ascii=False))
        self.assertNotIn('不导出的原文',json.dumps(graph,ensure_ascii=False))
        self.assertIn('paused',[n['status'] for n in graph['nodes']])
        data['sources'][0]['read_status']='partial'
        data['sources'][0]['limitations']=['attachment unread']
        partial=g.project(data,'a'*64)
        source=next(n for n in partial['nodes'] if n['type']=='source')
        self.assertEqual(source['limitations'],['attachment unread'])
        self.assertEqual(source['source_kind'],'conversation')
        self.assertIn(b'attachment unread',g.render_files(partial)['vault/'+source['id']+'.md'])
        data['sources'].append(dict(id='isolated',title='独立来源',locator='note-only',kind='note',
            read_status='unread',time_range='未知',limitations=['没有读取正文'],snapshot=None))
        isolated=g.project(data,'a'*64)
        orphan=next(n for n in isolated['nodes'] if n['label']=='独立来源')
        self.assertFalse(any(orphan['id'] in (e['source'],e['target']) for e in isolated['edges']))
        data['facts'][0]['source_ids']=['missing']
        with self.assertRaises(ValueError):g.project(data,'a'*64)

    def test_xss_and_markdown_injection_are_inert(self):
        s = state(); attack='</script><img src="https://invalid.example/x" onerror="alert(1)"> [[fake]]'
        s['session']['data']['claims'][0]['value']=attack;s['library']=o._library(s['session'],False,None)
        files=g.render_files(g.project(s,'a'*64))
        self.assertNotIn(attack.encode(),files['index.html'])
        self.assertIn(b"connect-src 'none'",files['index.html'])
        self.assertNotIn(b'innerHTML',files['index.html'])
        for name,raw in files.items():
            if name.endswith('.md'):
                self.assertNotIn(b'[[fake]]',raw);self.assertNotIn(b'<img',raw)
        for raw in files.values():
            if b'href="javascript:' in raw: self.fail('unsafe active locator')

    def test_deterministic_export_stale_check_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();src=root/'state.json';o.save(src,state())
            a=g.export(src,root,'v1');b=g.export(src,root,'v2')
            files=lambda name:{str(p.relative_to(root/name)):p.read_bytes() for p in (root/name).rglob('*') if p.is_file()}
            self.assertEqual(files('v1'),files('v2'))
            self.assertEqual(g.check(src,root/'v1')['status'],'current')
            src.write_bytes(src.read_bytes()+b'\n')
            self.assertEqual(g.check(src,root/'v1')['status'],'stale')
            with self.assertRaises(FileExistsError):g.export(src,root,'v1')
            note=next((root/'v1/vault').glob('claim-*.md'));note.write_text('手改')
            with self.assertRaises(ValueError):g.check(src,root/'v1')
            self.assertEqual(note.read_text(),'手改')
            with self.assertRaises(ValueError):g.export(src,root,'../escape')

    def test_paths_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();src=root/'state.json';o.save(src,o.initialize())
            link=root/'link.json';link.symlink_to(src)
            with self.assertRaises(ValueError):g.export(link,root,'v1')
            src.chmod(0o644)
            with self.assertRaises(ValueError):g.export(src,root,'v1')
            with self.assertRaises(ValueError):g.private_path(ROOT)

    def test_snapshot_read_does_not_mutate_source_or_resume_skip(self):
        s=o.submit(state(),event_id='skip',responses=[],source_id_value='synthetic')
        before=copy.deepcopy(s);g.project(s,'a'*64)
        self.assertEqual(s,before);self.assertIsNone(o.next_gap(s))

    def test_vault_wikilinks_resolve_and_ids_persist(self):
        graph=g.project(state(),'a'*64);files=g.render_files(graph)
        for name,raw in files.items():
            if name.endswith('.md'):
                for target in re.findall(r'\[\[([^\]]+)\]\]',raw.decode()):
                    self.assertIn('vault/'+target+'.md',files)
        mapping=json.loads(files['identity-map.json'])
        self.assertEqual(set(mapping['entries'].values()),{n['id'] for n in graph['nodes']})

    def test_html_has_no_external_resources_or_network_primitives(self):
        page=g.render_files(g.project(state(),'a'*64))['index.html'].decode()
        class Resources(HTMLParser):
            def handle_starttag(self, tag, attrs):
                for key,value in attrs:
                    if key in ('src','href','action','srcset','poster'):
                        raise AssertionError('unexpected resource/navigation attribute')
        Resources().feed(page)
        script=re.search(r'<script>(.*?)</script>',page,re.S).group(1)
        for forbidden in ('fetch(', 'XMLHttpRequest', 'WebSocket', 'sendBeacon', 'import(', 'eval('):
            self.assertNotIn(forbidden,script)
        self.assertNotRegex(page,r'url\s*\(')
        node=os.environ.get('SESSION_BUNDLE_NODE')
        if node:
            result=subprocess.run([node,'--check'],input=script,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_offline_dom_search_filter_select_and_local_view(self):
        node=os.environ.get('SESSION_BUNDLE_NODE') or shutil.which('node')
        if not node:
            self.skipTest('existing Node required for synthetic DOM test')
        page=g.render_files(g.project(state(),'a'*64))['index.html'].decode()
        script=re.search(r'<script>(.*?)</script>',page,re.S).group(1)
        harness=r'''
const vm=require('vm'),assert=require('assert');
class El {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.checked=false;this.textContent='';this.handlers={};this.classList={add(){}};}
 setAttribute(k,v){this[k]=v;} append(...xs){this.children.push(...xs);} replaceChildren(...xs){this.children=xs;}
 addEventListener(k,f){this.handlers[k]=f;}
}
const ids={};for(const id of ['search','type','local','reset','summary','freshness','source-file','graph','empty','list','details','limits'])ids[id]=new El(id);
ids.type.value='all';let calls=0;
const context={TextDecoder,Uint8Array,Map,Set,console,atob:s=>Buffer.from(s,'base64').toString('binary'),
 document:{getElementById:id=>ids[id],createElement:t=>new El(t),createElementNS:(_,t)=>new El(t)},
 fetch(){calls++;throw Error('network forbidden');},XMLHttpRequest:function(){calls++;throw Error('network forbidden');}};
vm.createContext(context);vm.runInContext(SCRIPT,context);
const total=ids.list.children.length;assert(total>0);assert(ids.graph.children.length>0);
ids.list.children[0].children[0].onclick();assert(ids.details.children[0].textContent);assert(ids.details.children[1].textContent.includes('id'));
ids.local.checked=true;ids.local.handlers.change();assert(ids.list.children.length>0&&ids.list.children.length<total);
ids.reset.onclick();assert.equal(ids.list.children.length,total);
ids.type.value='source';ids.type.handlers.change();assert(ids.list.children.every(x=>x.children[0].textContent.startsWith('source')));
ids.search.value='NO_MATCH_SYNTHETIC';ids.search.handlers.input();assert.equal(ids.list.children.length,0);assert.equal(ids.empty.hidden,false);
ids.reset.onclick();assert.equal(ids.list.children.length,total);assert.equal(calls,0);
'''.replace('SCRIPT',json.dumps(script))
        result=subprocess.run([node,'-e',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
