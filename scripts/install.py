#!/usr/bin/env python3
"""Install/uninstall only this reviewed local skill. No network or dependencies."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

NAME = "maintain-personal-context"
SOURCE = Path(__file__).resolve().parents[1] / "skills" / NAME
MARKER = ".personal-context-install.json"


def digest_tree(root):
    result = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError("symlinks are not supported")
        if p.is_file() and p.name != MARKER:
            if p.stat().st_nlink > 1:
                raise ValueError("hard-linked files are not supported")
            result[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def destination(value):
    p = Path(value).expanduser()
    if not p.is_absolute():
        raise ValueError("--dest must be an explicit absolute skills directory")
    for q in [p, *p.parents]:
        if q.is_symlink():
            raise ValueError("symlink destination ancestor is not supported")
    p = p.resolve()
    target = p / NAME
    if target.is_symlink():
        raise ValueError("symlink skill destination is not supported")
    return p, target


def install(dest):
    parent, target = destination(dest)
    if target.exists():
        raise ValueError("skill already exists; refusing overwrite; keep edits and uninstall explicitly")
    if any(p.is_symlink() for p in [SOURCE, *SOURCE.parents]):
        raise ValueError("symlink source root or ancestor is not supported")
    # Check source links before copying, when their identity is still visible.
    digest_tree(SOURCE)
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".personal-context-install-", dir=parent))
    try:
        skill = stage / NAME
        # Preserve any links introduced after preflight so stage validation
        # rejects them without dereferencing files outside the source package.
        shutil.copytree(SOURCE, skill, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        manifest = digest_tree(skill)
        (skill / MARKER).write_text(json.dumps({"name": NAME, "files": manifest}, sort_keys=True))
        # Reserve the target with mkdir so a concurrent install cannot be replaced.
        target.mkdir()
        try:
            for p in skill.iterdir():
                shutil.move(str(p), target / p.name)
        except Exception:
            # Preserve any partial copy for inspection. No existing install is deleted.
            raise ValueError("partial install retained; inspect destination before retrying")
    finally:
        shutil.rmtree(stage)
    return {"status": "installed", "skill": NAME, "files": len(manifest)}


def uninstall(dest):
    parent, target = destination(dest)
    marker = target / MARKER
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("no verified install marker; move the specific skill manually")
    manifest = json.loads(marker.read_text())
    if manifest.get("name") != NAME or manifest.get("files") != digest_tree(target):
        raise ValueError("installed files were modified; preserve your edits and move the skill manually")
    archive_base = parent.parent / ".personal-context-uninstalled"
    if archive_base.is_symlink():
        raise ValueError("symlink backup destination is not supported")
    archive_base.mkdir(mode=0o700, exist_ok=True)
    archived = archive_base / (NAME + "-" + uuid.uuid4().hex)
    # Recoverable move; private context outside this folder is never touched.
    target.rename(archived)
    return {"status": "uninstalled", "recoverable_folder": str(archived)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("install", "uninstall"))
    p.add_argument("--dest", required=True)
    args = p.parse_args(argv)
    try:
        print(json.dumps((install if args.action == "install" else uninstall)(args.dest)))
        return 0
    except (OSError, ValueError) as e:
        print("install: " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
