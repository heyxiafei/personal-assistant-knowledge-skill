#!/usr/bin/env python3
"""Opt-in capacity check using generated data in a temporary private directory.

No account/export discovery occurs. A default run creates >512 MiB of synthetic
message text and needs several times that amount of temporary disk space. The
worker reports its measured peak RSS; this is not a real-account acceptance test.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time


SCRIPT = Path(__file__).resolve().parents[1] / "skills/maintain-personal-context/scripts/chatgpt_export.py"
MIB = 1024 * 1024


def adapter():
    spec = importlib.util.spec_from_file_location("capacity_export_adapter", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(source, output, records):
    implementation_hash = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    module = adapter()
    started = time.monotonic()
    result = module.import_export(source, output, accepted=True,
                                  account_scope="synthetic-capacity-probe")
    verified = module.verify_import(source, output, accepted=True)
    expected = {"conversations": records, "mapping_nodes": records, "messages": records}
    if (not result["coverage"]["supplied_file_import_complete"]
            or not verified["supplied_file_import_complete"]
            or verified["persisted_output_counts"] != expected
            or result["coverage"]["account_enumeration_complete"] != "unknown"
            or result["coverage"]["attachment_content_complete"] != "not_assessed"):
        raise RuntimeError("synthetic capacity reconciliation failed")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        peak *= 1024
    if hashlib.sha256(SCRIPT.read_bytes()).hexdigest() != implementation_hash:
        raise RuntimeError("adapter changed during capacity check; rerun after edits")
    return {"status": "passed", "adapter_source_sha256": implementation_hash,
            "input_bytes": source.stat().st_size,
            "records": records, "independent_reconciliation": verified["status"],
            "peak_rss_bytes": peak, "elapsed_seconds": round(time.monotonic() - started, 3),
            "peak_rss_to_input_ratio": round(peak / source.stat().st_size, 4),
            "account_enumeration_complete": "unknown",
            "scope": "generated_synthetic_input_only"}


def generate(source, records):
    body = "Synthetic capacity data. " * ((MIB // 25) + 1)
    body = (body + "x" * MIB)[:MIB]
    fd = os.open(str(source), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("[")
        for index in range(records):
            identity = "synthetic-capacity-" + str(index)
            record = {"id": identity, "title": "Synthetic capacity test",
                      "current_node": "node", "mapping": {
                          "node": {"id": "node", "parent": None, "children": [],
                                   "message": {"id": identity + "-message",
                                               "author": {"role": "user"},
                                               "content": {"content_type": "text", "parts": [body]},
                                               "metadata": {}}}}}
            if index:
                stream.write(",")
            json.dump(record, stream, separators=(",", ":"))
        stream.write("]\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--total-mib", type=int, default=513,
                        help="Generated message payload MiB, one MiB per record (default 513)")
    parser.add_argument("--worker-source", help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.total_mib < 1:
        parser.error("--total-mib must be positive")
    if args.worker_source:
        print(json.dumps(worker(Path(args.worker_source), Path(args.worker_output), args.total_mib)))
        return 0
    with tempfile.TemporaryDirectory(prefix="synthetic-export-capacity-") as temporary:
        base = Path(temporary).resolve()
        if shutil.disk_usage(base).free < args.total_mib * MIB * 6:
            raise SystemExit("capacity check: insufficient free temporary space; no input generated")
        source = base / "conversations.json"
        generate(source, args.total_mib)
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                 "--total-mib", str(args.total_mib), "--worker-source", str(source),
                                 "--worker-output", str(base / "private-import")],
                                text=True, capture_output=True)
        if result.returncode:
            # Tracebacks can include local paths. Keep the terminal summary bounded.
            raise SystemExit("capacity check: synthetic worker failed; inspect adapter tests")
        summary = json.loads(result.stdout)
        if args.total_mib >= 513 and summary["input_bytes"] <= 512 * MIB:
            raise SystemExit("capacity check: generated source did not exceed the former limit")
        print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
