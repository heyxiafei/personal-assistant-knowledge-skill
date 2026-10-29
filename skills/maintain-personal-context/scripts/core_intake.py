#!/usr/bin/env python3
"""Plan the three required core sources. Never opens accounts or starts collection.

This release has no complete Memory enumerator, so it cannot certify the user's
three-source goal. Source-local acquisition is checked by the individual adapters;
neither their manifests nor old annotation receipts are global completion proof.
"""
from __future__ import annotations

import argparse
import json


REQUIRED = ("chatgpt_history", "codex_history", "chatgpt_memory")


def plan():
    return {
        "schema_version": 1,
        "core_status": "incomplete",
        "required_sources": list(REQUIRED),
        "acquisition_priority": [
            "discover_and_read_authorized_sources_with_existing_supported_tools",
            "ask_for_existing_material_location_or_specific_missing_authorization",
            "offer_user_initiated_export_only_after_supported_routes_are_blocked_and_user_accepts",
        ],
        "scope_contract": {
            "account": "current_account_must_be_identified_by_user",
            "codex_hosts": "explicitly_agreed_hosts_only",
            "time_range": "all_retained_history_through_recorded_cutoff",
            "deleted_or_temporary_chats": "not_recoverable_or_claimed",
            "private_destination": "explicit_outside_repositories_and_installed_skills",
        },
        "sources": {
            "chatgpt_history": {
                "status": "discover_supported_host_history_first_completeness_unknown",
                "adapter": "chatgpt_export.py",
                "entry": "existing_supported_host_history_then_user_initiated_export_as_last_resort",
                "complete_automatic_account_enumeration_verified": False,
                "export_reconciliation_if_used": "reopen_original_export_and_compare_all_records",
                "account_completeness": "unknown",
                "attachments": "inventory_and_preservation_are_separate_from_content_reading",
                "next_action": "discover_read_and_report_host_accessible_history_then_resolve_specific_gaps",
            },
            "codex_history": {
                "status": "discover_existing_host_tools_first_complete_enumeration_unverified",
                "adapter": "codex_appserver.py",
                "entry": "existing_supported_host_tools_then_official_app_server_if_safely_available",
                "host_enumeration": "discover_recent_and_archived_retain_pagination_limits_and_unknown_total",
                "app_server_candidate_enumeration": "active_and_archived_all_documented_source_kinds_all_pages",
                "app_server_candidate_list_read_mode": "useStateDbOnly_true",
                "content": "page_host_thread_text_check_truncation_and_report_unavailable_content",
                "app_server_candidate_content": "turns_full_and_items_pagination_with_reconciliation",
                "account_completeness": "unknown",
                "limitation": "database_scope_is_not_all_hosts_or_unindexed_retained_files",
                "next_action": "enumerate_and_page_discovered_authorized_threads_record_limits_and_failures",
            },
            "chatgpt_memory": {
                "status": "blocked_no_complete_official_enumeration_entry",
                "adapter": None,
                "search_and_summary": "supplementary_only",
                "codex_consumption": "not_verified",
                "next_action": "identify_supported_complete_entry_and_verify_codex_readback",
            },
        },
        "acceptance_requirements": [
            "real_supported_entry_and_recorded_authorized_scope",
            "all_pages_terminal_and_independent_inventory_reconciled",
            "raw_content_and_attachment_reading_coverage_reported_separately",
            "failed_and_resumable_units_retained_without_silent_skips",
            "memory_complete_enumeration_and_actual_codex_consumption_verified",
        ],
        "synthetic_tests_satisfy_core_acceptance": False,
        "installation_satisfies_core_acceptance": False,
        "writes_or_account_reads_performed": False,
        "background_tasks_created": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan",))
    parser.parse_args(argv)
    print(json.dumps(plan(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
