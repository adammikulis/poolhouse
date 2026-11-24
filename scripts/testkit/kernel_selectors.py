"""Explicit reviewed temporary zero-network test nodes."""
from __future__ import annotations

TEMP_ONLY = frozenset({

    'tests/test_notification_performance.py::test_urgent_action_survives_host_context_limit',
    'tests/test_notification_reader.py::test_nudge_subprocess_is_bounded_and_session_explicit',
    'tests/test_notification_reader.py::test_unbound_notification_does_not_block_completed_tools',
    'tests/test_notification_reader.py::test_explicit_parent_hook_preserves_launcher_root',
    'tests/test_harness_hook_outages.py::test_post_outage_does_not_reverse_a_completed_tool',
    'tests/test_harness_hook_outages.py::test_post_checkpoint_outage_preserves_pending_message_alert',
    'tests/test_harness_hook_outages.py::test_post_lifecycle_failure_preserves_stop_message_alert',
    'tests/test_hook_failure_records.py::test_nudge_failure_keeps_reference_visible_in_post_context',

    'tests/test_kernel_role_home.py::test_loader_omits_only_the_verified_first_self_install_name',
    'tests/test_kernel_role_home.py::test_loader_refuses_unverified_or_changed_install_name',
    'tests/test_workspace_device_metadata.py::test_malformed_or_unproved_device_metadata_is_rejected',
    'tests/test_kernel_role_home.py::test_smoke_initial_capture_failure_never_releases_gate',
    'tests/test_kernel_role_home.py::test_smoke_changed_generation_never_signals_group',
    'tests/test_kernel_role_home.py::test_smoke_unknown_membership_never_signals_or_claims_cleanup',
    'tests/test_source_snapshot_guards.py::test_actual_git_refuses_hostile_asset',
    'tests/test_source_snapshot_guards.py::test_actual_git_uses_owned_fixed_asset_without_inherited_path',

    'tests/test_serve_no_bypass.py::test_a_backend_is_only_started_by_the_manager',
    'tests/test_serve_no_bypass.py::test_a_broker_is_only_built_where_the_machines_is_chosen',
    'tests/test_serve_no_bypass.py::test_a_connection_to_a_server_goes_through_the_request_queue',
    'tests/test_serve_no_bypass.py::test_a_manager_that_keeps_the_machines_records_uses_the_machines_broker',
    'tests/test_serve_no_bypass.py::test_a_server_process_is_only_launched_by_a_backend_holding_a_lease',
    'tests/test_serve_no_bypass.py::test_every_public_entry_of_the_manager_reaches_the_broker',
    'tests/test_serve_no_bypass.py::test_no_flag_to_lease_skips_the_broker',
    'tests/test_serve_no_bypass.py::test_only_the_known_modules_start_a_process_and_name_the_server_binary',
    'tests/test_serve_no_bypass.py::test_the_grant_is_only_opened_by_the_broker',
    'tests/test_serve_no_bypass.py::test_the_private_starts_are_only_reached_by_the_manager_and_the_broker',
    'tests/test_serve_no_bypass.py::test_the_proof_a_backend_needs_is_only_made_by_the_manager',
    'tests/test_serve_no_bypass.py::test_the_up_command_reaches_a_server_only_through_a_lease',
    'tests/test_wiring.py::test_piece_is_wired_in',
    'tests/test_wiring.py::test_standalone_names_only_real_pieces',



    'tests/test_source_snapshot_guards.py::test_spoofed_home_preserves_actual_account_roots_and_explicit_overrides',
    'tests/test_source_snapshot_guards.py::test_partial_snapshot_construction_removes_its_private_metadata',
    'tests/test_source_snapshot_guards.py::test_git_inventory_launch_excludes_inherited_configuration_and_input',




    'tests/test_kernel_selector_admission.py::test_reviewed_nodes_preserve_complete_invocation_validation',
    'tests/test_kernel_selector_admission.py::test_unreviewed_files_and_resource_fixtures_remain_refused',
})

RESOURCE_NODES = frozenset({
    'tests/test_kernel_role_home.py::test_fixed_normal_role_imports_and_runtime_verification',
    'tests/test_kernel_role_home.py::test_owned_smoke_reaps_exited_leader_and_known_stdout_child',
    'tests/test_kernel_role_home.py::test_role_home_atomic_state_and_immutable_fences',
})


def admitted_node(selector: str) -> bool:
    if selector in TEMP_ONLY | RESOURCE_NODES:
        return True
    base, separator, suffix = selector.partition("[")
    return bool(separator and suffix.endswith("]") and "::" not in suffix and base in TEMP_ONLY)
