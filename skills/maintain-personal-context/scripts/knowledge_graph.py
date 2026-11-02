#!/usr/bin/env python3
"""Explicit evidence graph projection. Offline, stdlib, no source mutation or sync."""
from __future__ import annotations
import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import stat
import sys

import onboarding
import session_bundle
import quick_start

ASSET = Path(__file__).resolve().parents[1] / 'assets/knowledge-graph.html'
SCHEME = 'evidence-graph-v1'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def identity(kind, key):
    # Titles, text, array positions and export directory names never form IDs.
    return kind + '-' + digest(encoded([SCHEME, kind, key]))


def text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def private_path(path, file=False):
    p = Path(path)
    if not p.is_absolute() or any(q.is_symlink() for q in (p, *p.parents)):
        raise ValueError('explicit absolute non-symlink private path required')
    for q in (p, *p.parents):
        if (q / 'skills/maintain-personal-context/SKILL.md').is_file() or (
                q.name == 'maintain-personal-context' and (q / 'SKILL.md').is_file()):
            raise ValueError('private exports must be outside source and installed Skill')
    st = p.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('private input/output must be owner-only')
    if file and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
        raise ValueError('private input must be a non-linked regular file')
    if not file and not stat.S_ISDIR(st.st_mode):
        raise ValueError('private export parent must be a directory')
    return p


def project(data, source_digest, exclude_keys=()):
    nodes, edges, mapping = {}, {}, {}
    projection_limits = []

    def node(kind, identity_key, label, status, dates=None, locator=None, **extra):
        nid = identity(kind, identity_key)
        item = dict(id=nid, type=kind, label=text(label), status=status,
                    dates=dates or {}, locator=locator, **extra)
        if nid in nodes and nodes[nid] != item:
            raise ValueError('conflicting node identity; reconcile source before export')
        nodes[nid] = item
        mapping[digest(encoded([kind, identity_key]))] = nid
        return nid

    def edge(a, b, kind):
        if a not in nodes or b not in nodes:
            raise ValueError('dangling graph reference')
        eid = identity('edge', [a, b, kind])
        edges[eid] = dict(id=eid, source=a, target=b, type=kind, evidence='explicit')

    if data.get('kind') == 'local_onboarding':
        onboarding.checked(data)
        session = data['session']; annotations = session['data']
        blocked = set(exclude_keys) | set(annotations.get(quick_start.META, {}).get('excluded_keys', []))
        claims = [c for c in annotations['claims'] if c['key'] not in blocked]
        facts = onboarding.first_run.reconcile_claims(claims)['facts']
        current, correction_nodes, buckets = {}, [], []
        for fact in facts:
            for bucket in fact['values']:
                claim_nodes = []
                for c in bucket['sources']:
                    sid = node('source', [c['source_kind'], c['source_id']], c['source_id'],
                               'referenced_only', locator=c['source_id'], source_kind=c['source_kind'])
                    authoritative = c['author_role'] == 'user' and c['source_kind'] != 'memory' and c['evidence_tier'] in ('direct', 'saved')
                    claim_status = 'inferred'
                    if authoritative:
                        claim_status = fact['status'] if fact['status'] != 'known' or bucket['value'] == fact['current_value'] else 'unconfirmed'
                    if 'superseded_by' in bucket:
                        claim_status = 'superseded'
                    cid = node('claim', [c['source_kind'], c['source_id'], c['key']], c['value'],
                               claim_status,
                               dates={k: c.get(k) for k in ('recorded_at', 'event_date', 'effective_date', 'confirmation_date')},
                               key=c['key'], author_role=c['author_role'], evidence_tier=c['evidence_tier'])
                    edge(cid, sid, 'supported_by')
                    if 'correction_of' in c:
                        correction_nodes.append((c, cid))
                    claim_nodes.append(cid)
                    if authoritative and fact['status'] == 'known' and bucket['value'] == fact['current_value'] and 'superseded_by' not in bucket:
                        current.setdefault(c['key'], []).append(cid)
                buckets.append((fact['key'], bucket, claim_nodes))
        for key, bucket, old_nodes in buckets:
            replacements = [cid for c, cid in correction_nodes
                            if c['source_id'] == bucket.get('superseded_by') and c['key'] == key
                            and c['correction_of'] == bucket['value']
                            and c['author_role'] == 'user' and c['evidence_tier'] == 'direct'
                            and c['source_kind'] != 'memory']
            if 'superseded_by' in bucket and len(replacements) != 1:
                raise ValueError('ambiguous replacement identity; reconcile before graph export')
            if replacements:
                for old in old_nodes:
                    edge(replacements[0], old, 'supersedes')
        # Same basis cannot identify multiple distinct actions. Keep the evidence
        # graph usable and report this local omission rather than inventing IDs.
        advice_groups = {}
        for rec in annotations.get('recommendations', []):
            group = tuple(sorted(rec.get('basis_keys', [])))
            if rec not in advice_groups.setdefault(group, []):
                advice_groups[group].append(rec)
        for group, recommendations in sorted(advice_groups.items()):
            if len(recommendations) > 1:
                projection_limits.append('同一组依据对应多条建议，缺少独立身份；此组建议未绘制，事实图保留。')
                continue
            rec = recommendations[0]
            keys = rec.get('basis_keys', [])
            if not keys or any(k in blocked or k not in current for k in keys):
                continue
            rid = node('advice', sorted(keys), rec['label'], 'conditional',
                       first_step=rec['first_step'], condition=rec['confirmation_condition'])
            for key in keys:
                for cid in current[key]:
                    edge(rid, cid, 'based_on')
        limits = list(annotations.get('coverage_gaps', []))
        source_kind = 'onboarding'
    else:
        # Accept the documented portable session payload, not arbitrary guessed JSON.
        session_bundle.validate(data)
        if exclude_keys:
            raise ValueError('exclude-key applies to onboarding; bundle sensitive facts are excluded automatically')
        source_kind = 'session_payload'
        included = [f for f in data['facts'] if f['sensitivity'] == 'normal']
        needed = {sid for f in included for sid in f['source_ids']}
        referenced = {sid for f in data['facts'] for sid in f['source_ids']}
        source_ids = {}
        for s in data['sources']:
            if s['id'] in needed or s['id'] not in referenced:
                source_ids[s['id']] = node('source', ['bundle', s['id']], s['title'], s['read_status'],
                                          dates={'time_range': s['time_range']}, locator=s['locator'],
                                          source_kind=s['kind'], limitations=list(s['limitations']))
        fact_ids = {}
        for f in included:
            fid = node('claim', ['bundle', f['id']], f['text'], f['status'], category=f['category'])
            fact_ids[f['id']] = fid
            for sid in f['source_ids']:
                edge(fid, source_ids[sid], 'supported_by')
        for a in data['advice']:
            if all(fid in fact_ids for fid in a['fact_ids']):
                aid = node('advice', ['bundle', a['id']], a['text'], 'conditional', condition=a['condition'])
                for fid in a['fact_ids']:
                    edge(aid, fact_ids[fid], 'based_on')
        limits = list(data['coverage']['limits']) + list(data['coverage']['uncovered'])
    return dict(schema_version=1, identity_scheme=SCHEME, source_sha256=source_digest,
                source_kind=source_kind, nodes=sorted(nodes.values(), key=lambda n: n['id']),
                edges=sorted(edges.values(), key=lambda e: e['id']), identity_map=mapping,
                coverage_limits=list(dict.fromkeys(limits + projection_limits)), full_history_complete=False,
                view_only=True, excluded_keys=sorted(exclude_keys))


def markdown(value):
    # Escape all Markdown link/embed delimiters and HTML. Never emit source URLs
    # as links: locators can be private, untrusted, or plain identifiers.
    value = html.escape(text(value), quote=True)
    return ''.join('&#%d;' % ord(c) if c in '[]()!#*_`|\\' else c for c in value)


def render_files(graph):
    files = {'graph.json': encoded(graph)}
    payload = base64.b64encode(encoded(graph)).decode('ascii')
    template = ASSET.read_text(encoding='utf-8')
    files['index.html'] = template.replace('__GRAPH_BASE64__', payload).encode()
    files['identity-map.json'] = encoded({'scheme': SCHEME, 'entries': graph['identity_map']})
    outgoing, incoming = {}, {}
    for e in graph['edges']:
        outgoing.setdefault(e['source'], []).append(e)
        incoming.setdefault(e['target'], []).append(e)
    index = ['# Evidence graph / 证据关系图', '', '只读派生快照，不与原库双向同步。',
             '来源摘要：' + graph['source_sha256'], '', '## 节点']
    for n in graph['nodes']:
        nid = n['id']
        lines = ['---', 'id: ' + nid, 'type: ' + n['type'], '---', '', '# ' + markdown(n['label']), '',
                 '状态：' + markdown(n['status']), '', '时间：' + markdown(n['dates']), '',
                 '来源定位（文本，不自动打开）：' + markdown(n['locator'] or '未知'), '',
                 '## 元数据', '', markdown({k: v for k, v in n.items() if k not in ('label', 'locator')}), '',
                 '## 显式关系']
        for e in outgoing.get(nid, []):
            lines.append('- ' + e['type'] + ' → [[' + e['target'] + ']]')
        for e in incoming.get(nid, []):
            lines.append('- ' + e['type'] + ' ← [[' + e['source'] + ']]')
        files['vault/' + nid + '.md'] = ('\n'.join(lines) + '\n').encode()
        index.append('- [[' + nid + ']] — ' + markdown(n['label']))
    index += ['', '## 覆盖限制'] + ['- ' + markdown(x) for x in graph['coverage_limits']]
    files['vault/INDEX.md'] = ('\n'.join(index) + '\n').encode()
    files['README.txt'] = ('Offline evidence graph. Open index.html; open vault as a Markdown folder if desired.\n'
        'No Obsidian installation required. No two-way sync. Source not embedded. No original excerpts.\n'
        'Snapshot freshness is unknown until checked against the selected source using the CLI or browser file picker.\n'
        'Keep this folder private. Old exports are snapshots: later deletion does not erase them.\n').encode()
    files['manifest.json'] = encoded({'schema_version': 1, 'source_sha256': graph['source_sha256'],
                                     'files': {k: digest(v) for k, v in sorted(files.items())}})
    return files


def read_source(path):
    p = private_path(path, file=True)
    raw = p.read_bytes()
    return json.loads(raw), digest(raw)


def export(source, parent, name, exclude_keys=()):
    data, sha = read_source(source)
    root = private_path(parent)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
        raise ValueError('export version name must be a safe single directory name')
    graph = project(data, sha, exclude_keys)
    files = render_files(graph)
    target = root / name
    # Atomic reservation; never mix or overwrite, including partial old exports.
    target.mkdir(mode=0o700)
    (target / 'vault').mkdir(mode=0o700)
    for rel, value in files.items():
        fd = os.open(str(target / rel), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
    return {'status': 'exported', 'directory': str(target), 'source_sha256': sha,
            'nodes': len(graph['nodes']), 'edges': len(graph['edges']), 'files': len(files),
            'full_history_complete': False}


def check(source, directory):
    root = private_path(directory)
    manifest_path = private_path(root / 'manifest.json', file=True)
    manifest = json.loads(manifest_path.read_bytes())
    expected = manifest['files']
    for rel, sha in expected.items():
        if not re.fullmatch(r'(?:vault/[a-zA-Z0-9_-]+\.md|graph\.json|identity-map\.json|index\.html|README\.txt)', rel):
            raise ValueError('unsafe manifest path')
        if digest(private_path(root / rel, file=True).read_bytes()) != sha:
            raise ValueError('export edited; preserve it and choose a new version directory')
    paths = list(root.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise ValueError('export contains symlinks; preserve and reconcile')
    actual = {str(p.relative_to(root)) for p in paths if p.is_file()}
    if actual != set(expected) | {'manifest.json'}:
        raise ValueError('export file inventory changed; preserve manual files')
    _, current = read_source(source)
    return {'status': 'current' if current == manifest['source_sha256'] else 'stale',
            'source_sha256': current, 'snapshot_sha256': manifest['source_sha256']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    sub = p.add_subparsers(dest='action', required=True)
    e = sub.add_parser('export'); e.add_argument('--parent', required=True); e.add_argument('--name', required=True)
    e.add_argument('--exclude-key', action='append', default=[])
    c = sub.add_parser('check'); c.add_argument('--directory', required=True)
    a = p.parse_args()
    try:
        result = export(a.source, a.parent, a.name, a.exclude_key) if a.action == 'export' else check(a.source, a.directory)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, session_bundle.BundleError) as exc:
        print('knowledge_graph: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
