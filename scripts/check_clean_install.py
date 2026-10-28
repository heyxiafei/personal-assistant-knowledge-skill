#!/usr/bin/env python3
"""Check a clean temporary install using synthetic fixtures; never a formal install."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

import install


def run(*args, cwd=None, expected=0, input=None):
    result = subprocess.run([sys.executable, "-B", *map(str, args)], cwd=cwd,
                            input=input, text=True, capture_output=True)
    if result.returncode != expected:
        raise RuntimeError("temporary install verification failed: " + result.stderr)
    return result.stdout


def main():
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "tests/fixtures/quick-start.json"
    with tempfile.TemporaryDirectory(prefix="personal-context-install-check-") as temporary:
        root = Path(temporary).resolve()
        destination = root / "project/.agents/skills"
        receipt = json.loads(run(repo / "scripts/install.py", "install", "--dest", destination))
        installed = destination / install.NAME
        original = install.digest_tree(installed)
        if original != install.digest_tree(install.SOURCE):
            raise RuntimeError("installed runtime differs from reviewed source")
        for case in ("new", "existing", "all_known", "conflict"):
            preview = json.loads(run(installed / "scripts/quick_start.py", "--input", fixture,
                                     "--case", case, cwd=root))
            if not preview["progress"]["usable_now"] or preview["knowledge_base_updated"]:
                raise RuntimeError("installed preview incorrectly gates use or claims a write")
            if case == "all_known" and preview["question"] is not None:
                raise RuntimeError("installed helper repeats known answers")
        # Import only from the installed scripts. Exercise persistence beside the
        # install, and ensure it remains forbidden inside the installed Skill.
        program = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import quick_start as q, space_state
root=Path(sys.argv[2]);data=json.load(sys.stdin)
s=q.start(data,checkpoint_consent="fictional-checkpoint-consent")
s=q.respond(s,"situation","skip","fictional-skip")
s=q.pause(s);path=root/"synthetic-checkpoint.json";q.checkpoint(path,s)
loaded,digest=q.load_checkpoint(path);resumed=q.resume(loaded)
assert q.question(resumed)["id"]=="goal"
q.checkpoint(path,resumed,expected_hash=digest)
try:q.checkpoint(Path(sys.argv[1]).parent/"forbidden.json",resumed)
except space_state.StateError:pass
else:raise AssertionError("installed Skill accepted private state")
'''
        run("-I", "-c", program, installed / "scripts", root,
            input=json.dumps(json.loads(fixture.read_text())["new"]), cwd=root)
        run(repo / "scripts/install.py", "install", "--dest", destination, expected=2)
        if original != install.digest_tree(installed):
            raise RuntimeError("duplicate install changed existing files")
        skill_file = installed / "SKILL.md"
        skill_file.write_text(skill_file.read_text() + "\nSynthetic manual note: preserve this edit.\n")
        edited = install.digest_tree(installed)
        run(repo / "scripts/install.py", "install", "--dest", destination, expected=2)
        run(repo / "scripts/install.py", "uninstall", "--dest", destination, expected=2)
        if edited != install.digest_tree(installed):
            raise RuntimeError("manual changes were overwritten")
        second = root / "second/skills"
        run(repo / "scripts/install.py", "install", "--dest", second)
        recovered = json.loads(run(repo / "scripts/install.py", "uninstall", "--dest", second))
        if (second / install.NAME).exists() or install.digest_tree(Path(recovered["recoverable_folder"])) != original:
            raise RuntimeError("uninstall did not preserve a recoverable copy")
    print(json.dumps({"status": "passed", "runtime_files": receipt["files"],
                      "checks": ["runtime hashes", "installed CLI: four synthetic cases",
                                 "installed pause/checkpoint/resume", "private-state isolation",
                                 "duplicate install refusal", "manual edit protection", "recoverable uninstall"],
                      "scope": "temporary isolated directories and synthetic data only"}))


if __name__ == "__main__":
    main()
