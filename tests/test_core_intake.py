"""The installed entry must lead to core acquisition, never a false ready state."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoreIntakeTests(unittest.TestCase):
    def test_installed_next_step_runs_offline_and_preserves_core_blockers(self):
        installer = load("core_test_installer", ROOT / "scripts/install.py")
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary).resolve() / "skills"
            result = installer.install(dest)
            self.assertEqual(result["status"], "installed")
            self.assertEqual(result["core_status"], "incomplete")
            self.assertFalse(result["history_collected"])
            self.assertFalse(result["memory_transferred_to_codex"])
            self.assertTrue(Path(result["next_step"]["guide"]).is_file())
            before = installer.digest_tree(dest / installer.NAME)
            completed = subprocess.run(result["next_step"]["argv"], cwd=temporary,
                                       capture_output=True, text=True, check=True)
            plan = json.loads(completed.stdout)
            self.assertEqual(set(plan["sources"]),
                             {"chatgpt_history", "codex_history", "chatgpt_memory"})
            self.assertEqual(plan["core_status"], "incomplete")
            self.assertEqual(plan["acquisition_priority"], [
                "discover_and_read_authorized_sources_with_existing_supported_tools",
                "ask_for_existing_material_location_or_specific_missing_authorization",
                "offer_user_initiated_export_only_after_supported_routes_are_blocked_and_user_accepts",
            ])
            self.assertEqual(plan["sources"]["codex_history"]["next_action"],
                             "enumerate_and_page_discovered_authorized_threads_record_limits_and_failures")
            self.assertFalse(plan["writes_or_account_reads_performed"])
            self.assertFalse(plan["synthetic_tests_satisfy_core_acceptance"])
            self.assertEqual(before, installer.digest_tree(dest / installer.NAME))
            self.assertEqual(sorted(p.name for p in Path(temporary).iterdir()), ["skills"])


if __name__ == "__main__":
    unittest.main()
