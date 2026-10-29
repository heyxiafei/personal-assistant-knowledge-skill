#!/usr/bin/env python3
"""Build a private native source index from already captured host-tool receipts.

No network, host discovery, semantic extraction, authorization or uploads. Input
receipts remain unchanged. Existing indexes are verified and never overwritten.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

import context_store
import native_history


class IndexError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _private_file(p):
    st = p.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise IndexError('receipt must be an owner-only regular file')
    return p.read_bytes()


def _scan(root):
    ledger, records = [], []
    for path in sorted(root.glob('receipt-*.json')):
        raw = _private_file(path)
        ledger.append({'file': path.name, 'bytes': len(raw), 'sha256': digest(raw)})
        records.append(json.loads(raw))
    if not records:
        raise IndexError('no captured receipts in selected private directory')
    return ledger, records


def _write(path, value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    context_store.create_new(path, raw)


def build(root_value, name='source-index'):
    root = Path(root_value)
    if not root.is_absolute() or any(p.is_symlink() for p in [root, *root.parents]):
        raise IndexError('explicit non-symlink private directory required')
    context_store.check_root(root)
    st = root.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise IndexError('source directory must be owner-only')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
        raise IndexError('invalid index name')
    ledger, records = _scan(root)
    dest = root / name
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or not dest.is_dir():
            raise IndexError('existing index is not a private directory')
        dst = dest.stat()
        if dst.st_uid != os.getuid() or dst.st_mode & 0o077:
            raise IndexError('existing index directory must be owner-only')
        manifest = json.loads(_private_file(dest/'manifest.json'))
        if manifest.get('receipt_ledger') != ledger:
            raise IndexError('receipts changed; preserve the existing index and choose a new name')
        for filename, expected in manifest.get('outputs', {}).items():
            if filename not in {'source-index.sqlite3', 'threads.json', 'nontext-types.json'} \
                    or digest(_private_file(dest/filename)) != expected:
                raise IndexError('index output changed; preserve user edits and reconcile')
        if set(manifest.get('outputs', {})) != {'source-index.sqlite3', 'threads.json', 'nontext-types.json'}:
            raise IndexError('existing index manifest is incomplete')
        return {'status': 'unchanged', **manifest['summary']}
    groups, outcomes = collections.defaultdict(list), {}
    limit = None
    for rec in records:
        if rec.get('kind') == 'capture_contract':
            new_limit = rec.get('max_output_chars_per_item')
            if type(new_limit) is not int or new_limit <= 0:
                raise IndexError('invalid captured character limit')
            if limit is not None and limit != new_limit:
                raise IndexError('conflicting capture limits; separate the runs')
            limit = new_limit
        if rec.get('kind') == 'page':
            groups[(rec.get('request_host_id'), rec['thread_id'])].append(rec)
        elif rec.get('kind') == 'thread_outcome':
            outcomes[(rec.get('request_host_id'), rec['thread_id'])] = rec
    dest.mkdir(mode=0o700)
    counts, nontext, thread_index = collections.Counter(), collections.Counter(), []
    dbpath = dest/'source-index.sqlite3'
    db = sqlite3.connect(str(dbpath))
    os.chmod(dbpath, 0o600)
    try:
        db.execute('CREATE TABLE messages (host_id TEXT, thread_id TEXT, turn_id TEXT, message_id TEXT, role TEXT, recorded_at TEXT, read_status TEXT, text TEXT)')
        try:
            db.execute("CREATE VIRTUAL TABLE message_search USING fts5(text, content='messages', content_rowid='rowid')")
            fts = True
        except sqlite3.OperationalError:
            fts = False
        for (host, tid), pages in groups.items():
            for page in pages:
                for turn in page['payload'].get('turns', []):
                    for item in turn.get('items', []) or []:
                        typ = item.get('type', 'unknown')
                        if typ not in ('userMessage', 'agentMessage'):
                            nontext[typ] += 1
                        if typ == 'userMessage':
                            for block in item.get('content', []) or []:
                                if isinstance(block, dict) and block.get('type') != 'text':
                                    nontext['user-block:' + str(block.get('type', 'unknown'))] += 1
            if not host:
                counts['unknown_host'] += 1
                continue
            reads = [{'request_cursor': p['request_cursor'], 'request_host_id': host,
                      'request_max_output_chars': p.get('request_max_output_chars', limit), 'payload': p['payload']} for p in pages]
            try:
                result = native_history.normalize_reads(reads, tid, host)
            except (native_history.HistoryError, KeyError, TypeError, ValueError):
                counts['normalization_failed'] += 1
                continue
            coverage = result['coverage']
            outcome = outcomes.get((host, tid))
            if not outcome or outcome.get('terminal') is not True or outcome.get('error'):
                coverage['thread_text_range_complete'] = False
                coverage['limitations'].append('capture_outcome_missing_or_failed')
            if any(read['request_max_output_chars'] is None for read in reads) and coverage['thread_text_range_complete'] is True:
                coverage['thread_text_range_complete'] = 'unknown'
                coverage['limitations'].append('request_character_limit_unknown')
            counts['normalized_threads'] += 1
            counts['complete_text_ranges'] += coverage['thread_text_range_complete'] is True
            counts['duplicate_messages'] += result['duplicate_messages']
            for msg in result['messages']:
                counts[msg['author_role'] + '_messages'] += 1
                counts['possible_truncations'] += bool(msg['truncation_observed'])
                cur = db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',
                    (msg['host_id'], msg['thread_id'], msg['turn_id'], msg['message_id'], msg['author_role'],
                     msg['recorded_at_utc'], msg['read_status'], msg['text']))
                if fts:
                    db.execute('INSERT INTO message_search(rowid,text) VALUES (?,?)', (cur.lastrowid, msg['text']))
            thread_index.append({'host_id': host, 'thread_id': tid, 'coverage': coverage,
                                 'outcome': outcomes.get((host, tid)), 'message_count': len(result['messages'])})
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise IndexError('index integrity check failed')
    finally:
        db.close()
    _write(dest/'threads.json', thread_index)
    _write(dest/'nontext-types.json', nontext)
    if _scan(root)[0] != ledger:
        raise IndexError('receipts changed during indexing; preserve output for review')
    summary = dict(counts)
    summary.update(receipts=len(ledger), threads_with_pages=len(groups),
                   full_text_index=fts, account_enumeration_complete='unknown')
    outputs = {name: digest(_private_file(dest/name)) for name in ('source-index.sqlite3','threads.json','nontext-types.json')}
    _write(dest/'manifest.json', {'version': 1, 'receipt_ledger': ledger, 'outputs': outputs, 'summary': summary})
    return {'status': 'created', **summary}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receipts', required=True, help='owner-only private receipt directory')
    p.add_argument('--name', default='source-index', help='new index subdirectory name')
    args = p.parse_args(argv)
    try:
        print(json.dumps(build(args.receipts, args.name), ensure_ascii=False, indent=2))
        return 0
    except (IndexError, native_history.HistoryError, context_store.ContextError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
