#!/usr/bin/env python3
"""Offline structure, local-reference and common private-identifier checks."""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/maintain-personal-context"
PRIVATE_PATTERNS = {
    "personal home path": re.compile("/" + r"(Users|home)/[A-Za-z0-9_.-]+/"),
    "private page/file id": re.compile(r"\b(?:lib" + r"file[_-]|page[_-])[a-z0-9]{16,}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "token": re.compile(r"\b(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_-]{20,}\b"),
    "signed transfer": re.compile(r"https://[^\s\"']+[?&](?:sig|X-Amz-Signature)="),
}


def check():
    errors, count = [], 0
    required = [
        ROOT / "LICENSE", ROOT / "README.md", ROOT / "docs/TEST_REPORT.md",
        ROOT / "docs/REVIEW_GUIDE.md", SKILL / "SKILL.md",
        SKILL / "agents/openai.yaml", SKILL / "assets/templates/PERSON_PROFILE.md",
        SKILL / "assets/templates/DAILY_LOG.md", SKILL / "assets/templates/LONG_TERM.md",
        SKILL / "references/space-onboarding.md", SKILL / "references/history-coverage.md",
        SKILL / "references/space-output.md", SKILL / "scripts/space_state.py",
        SKILL / "assets/templates/COVERAGE.md", SKILL / "assets/templates/SPACE_WORKBENCH.md",
        ROOT / "tests/test_space_state.py",
        SKILL / "references/native-history.md", SKILL / "scripts/native_history.py",
        ROOT / "tests/test_native_history.py", ROOT / "tests/fixtures/native-history.json",
        ROOT / "tests/test_mixed_sources.py", ROOT / "tests/fixtures/mixed-sources.json",
        SKILL / "references/first-run-result.md", SKILL / "scripts/first_run.py",
        ROOT / "tests/test_first_run.py", ROOT / "tests/fixtures/first-run.json",
        SKILL / "references/content-coverage.md", SKILL / "scripts/source_checks.py",
        SKILL / "scripts/content_review.py", ROOT / "tests/test_read_receipts.py",
        ROOT / "tests/test_content_review.py",
        SKILL / "scripts/corpus_ledger.py", SKILL / "references/corpus-ledger.md",
        SKILL / "references/media-intake.md", ROOT / "docs/LOCAL_ACCEPTANCE.md",
        ROOT / "tests/test_attachment_coverage.py", ROOT / "tests/test_local_acceptance.py",
        ROOT / "tests/fixtures/local-acceptance-input.json",
        SKILL / "scripts/core_intake.py", SKILL / "references/core-intake.md",
        SKILL / "scripts/onboarding.py", SKILL / "scripts/native_index.py", SKILL / "scripts/guided_exploration.py",
        SKILL / "references/onboarding.md", SKILL / "references/guided-exploration.md",
        ROOT / "tests/test_onboarding.py", ROOT / "tests/test_guided_exploration.py",
        SKILL / "scripts/chatgpt_export.py", SKILL / "scripts/codex_appserver.py",
        ROOT / "tests/test_core_intake.py", ROOT / "tests/test_chatgpt_export.py",
        ROOT / "tests/test_codex_appserver.py", ROOT / "docs/CORE_INTAKE_ACCEPTANCE.md",
        ROOT / "docs/CORE_INTAKE_VALIDATION.md",
        SKILL / "references/chatgpt-export.md", ROOT / "docs/CODEX_RUNTIME_PROBE.md",
        ROOT / "scripts/check_large_export.py", ROOT / "tests/test_export_stream_contract.py",
    ]
    for p in required:
        if not p.is_file():
            errors.append("missing " + str(p.relative_to(ROOT)))
    front = (SKILL / "SKILL.md").read_text().split("---\n", 2)
    if len(front) != 3 or front[0]:
        errors.append("invalid frontmatter delimiters")
    else:
        # Only the simple scalar frontmatter used here; not a general YAML parser.
        fields = {}
        for line in front[1].strip().splitlines():
            key, colon, value = line.partition(":")
            if not colon or key in fields:
                errors.append("invalid/duplicate scalar frontmatter")
            fields[key] = value.strip()
        if set(fields) != {"name", "description", "license"}:
            errors.append("unexpected frontmatter keys")
        if fields.get("name") != "maintain-personal-context" or fields.get("license") != "MIT":
            errors.append("name/license mismatch")
        if not fields.get("description") or len(fields["description"]) > 1024:
            errors.append("invalid description")
    for p in sorted(ROOT.rglob("*")):
        if ".git" in p.parts or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        rel = str(p.relative_to(ROOT))
        if p.is_symlink():
            errors.append("symlink " + rel)
            continue
        if not p.is_file():
            continue
        count += 1
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            errors.append("unexpected binary " + rel)
            continue
        for label, regex in PRIVATE_PATTERNS.items():
            if regex.search(text):
                errors.append(label + " in " + rel)
        if p.suffix == ".py":
            try:
                compile(text, rel, "exec")
            except SyntaxError as e:
                errors.append("Python syntax: " + rel + ":" + str(e.lineno))
        if p.suffix == ".json":
            try:
                json.loads(text)
            except ValueError:
                errors.append("invalid JSON " + rel)
        if p.suffix == ".md":
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                if re.match(r"^(https?://|#)", target):
                    continue
                link = target.split("#")[0]
                if not (p.parent / link).is_file():
                    errors.append("missing local link " + rel + " -> " + link)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", "files": count,
                      "checks": ["simple scalar frontmatter", "required resources",
                                 "local links", "Python syntax", "JSON", "common private identifiers"],
                      "limitation": "not a complete privacy audit, full YAML validator, or Agent behavior evaluation"}))
    return 0


if __name__ == "__main__":
    sys.exit(check())
