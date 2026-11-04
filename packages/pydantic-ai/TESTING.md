# Test matrix

The test strategy for the Pydantic AI integration, tracked in
[SDK-48](https://linear.app/keycardlabs/issue/SDK-48). Each row of the
contract's unit table is either covered by a named test or explicitly deferred
with an issue reference. keycardai-langchain's `TESTING.md` is the first
instance of this matrix; shared rows keep its wording.

Run the suite with `just test-package pydantic-ai`, or directly:

```bash
cd packages/pydantic-ai && uv run --extra test pytest tests/ -v
```

Every test is hermetic: `models.ALLOW_MODEL_REQUESTS = False` in `conftest.py`,
agent-loop tests drive a real `Agent` with `FunctionModel`, and the OAuth client
is a stub. Rows in `tests/test_grant.py` unless noted.

## Unit

| Row | Status | Where |
|---|---|---|
| 1 on-behalf-of through a real agent loop, sync and async tools | covered | `test_on_behalf_of_exchanges_the_callers_token_for_sync_and_async_tools` |
| 2 impersonation run | covered | `test_impersonation_uses_the_substitute_user_path` |
| 3 as-itself run | covered | `test_as_self_uses_client_credentials_with_request_scopes` |
| 4 as-itself with `request_scopes` | covered | `test_as_self_uses_client_credentials_with_request_scopes` |
| 5 on-behalf-of with `request_scopes` | covered | `test_on_behalf_of_request_scopes_reach_the_exchange` |
| 6 no identity, no URL | covered | `test_missing_identity_is_recorded_not_raised` |
| 7 tool schema carries no Keycard fields | covered | `test_tool_schema_carries_no_keycard_fields` |
| 8 one granted and one denied resource | covered | `test_partial_grant_yields_token_and_resource_error_side_by_side` |
| 9 expired subject token, `sign_in_url` set | covered | `test_expired_subject_token_pauses_for_sign_in` |
| 10 expired subject token, no `sign_in_url` | covered | `test_expired_subject_token_without_sign_in_url_records_an_error` |
| 11 unexpired JWT exchanged unchanged | covered | `test_on_behalf_of_exchanges_the_callers_token_for_sync_and_async_tools` |
| 12 neither `zone_url` nor client | covered | `test_configuration_errors` |
| 13 `client_id` / `client_secret` shorthand | covered | `test_client_id_and_secret_are_client_secret_shorthand` |
| 14 `application_credential` with the shorthand | covered | `test_configuration_errors` |
| 15 assertion credential, on-behalf-of | covered | `test_assertion_credential_reaches_the_exchange` |
| 16 assertion credential, as-itself | covered | `test_assertion_credential_reaches_the_client_credentials_grant` |
| 17 per-loop client cache | covered | `test_two_runs_on_one_loop_share_one_client` (the loop and discovery behavior itself is keycardai-oauth's `test_agent_grant.py`) |
| 18 `grant()` on-behalf-of, inside and outside the block | covered | `test_grant_serves_tools_outside_the_agent_and_not_after` |
| 19 `agrant()` as-itself | covered | `test_agrant_as_self_on_the_running_loop` |
| 20 `grant()` with `fallback_identity` | covered | `test_grant_without_identity_uses_fallback_or_records_missing_identity` |
| 21 `grant()` with no identity and no fallback | covered | `test_grant_without_identity_uses_fallback_or_records_missing_identity` |
| 22 `grant(tool_name=...)` | covered | `test_grant_serves_tools_outside_the_agent_and_not_after` |
| 23 `grant(resources=[...])` | covered | `test_grant_serves_tools_outside_the_agent_and_not_after` |
| 24 `grant()` with both | covered | `test_grant_serves_tools_outside_the_agent_and_not_after` |
| 25 `Access` factories, not instantiable | covered | `test_access_factories` |
| 26 empty or whitespace input rejected | covered | `test_access_factories` |
| 27 `mock_access_context(resource_tokens=...)` around a test-model run | covered | `tests/test_testing_seam.py::test_resource_tokens_are_served_to_a_tool_run_by_the_test_model` |
| 28 `mock_access_context(access_token=...)` | covered | `tests/test_testing_seam.py::test_any_resource_token_is_a_convenience_with_a_tradeoff` |
| 29 `mock_access_context(error_message=...)` | covered | `tests/test_testing_seam.py::test_global_error_is_visible_to_the_tool` |
| 30 `mock_access_context(resource_errors=...)` | covered | `tests/test_testing_seam.py::test_resource_error_raises_only_on_access` |
| 31 tool outside any seam | covered | `tests/test_testing_seam.py::test_outside_the_seam_the_tool_reports_a_missing_capability`, `test_get_access_context_outside_any_seam_reports_missing_capability` |
| 32 deps as identity, deps with `keycard_identity`, deps with neither | covered | `test_identity_resolves_from_deps_itself_or_deps_keycard_identity`, `test_missing_identity_is_recorded_not_raised` |
| 33 `identity_from` callable | covered | `test_identity_from_callable_runs_per_tool_call_and_fallback_fills_none` |
| 34 identity on `RunContext.metadata` only | covered | `test_identity_on_run_metadata_only_is_not_read` |
| 35 sync tool reads the context from its worker thread | covered | `test_on_behalf_of_exchanges_the_callers_token_for_sync_and_async_tools` |
| 36 two parallel tool calls, two resources | covered | `test_parallel_tool_calls_each_see_their_own_access_context` |
| 37 two concurrent runs, two identities | covered | `test_concurrent_runs_exchange_their_own_identity` |
| 38 no identity, `sign_in_url`, `DeferredToolRequests` output | covered | `test_missing_identity_with_sign_in_url_pauses_with_reason_missing_identity` |
| 39 ungranted resource, `authorization_url` | covered | `test_ungranted_resource_pauses_with_authorization_payload` |
| 40 approved resume re-acquires | covered | `test_approved_resume_reacquires_and_runs_the_tool` |
| 41 approved resume still failing: `ToolFailed`, no second pause | covered | `test_approved_resume_that_still_fails_ends_with_tool_failed_not_a_second_pause` |
| 42 `ToolDenied` resume | covered | `test_denied_resume_never_runs_the_tool_or_acquires` |
| 43 `defer_on_auth=False` | covered | `test_defer_on_auth_false_fails_the_call_with_the_url_instead_of_pausing` |
| 44 as-itself denial never pauses | covered | `test_as_self_denial_never_pauses` |
| 45 `output_type` without `DeferredToolRequests`, with and without `HandleDeferredToolCalls` | covered | `test_pause_without_deferred_tool_requests_in_output_type_is_the_frameworks_error`, `test_handle_deferred_tool_calls_resolves_the_pause_in_run` |
| 46 durability capability | covered for the capability hook | `test_durability_capability_raises_before_acquiring`; the `mcp_auth` half lands with [SDK-65](https://linear.app/keycardlabs/issue/SDK-65) |
| 47 `mcp_auth` acquires once per run | deferred | [SDK-65](https://linear.app/keycardlabs/issue/SDK-65) (the MCP auth seam is a follow-up) |
| 48 `mcp_auth` refreshes within the expiry skew | deferred | [SDK-65](https://linear.app/keycardlabs/issue/SDK-65) |
| 49 `tool_resources` callable returning `[]` | covered | `test_tool_resources_callable_returning_empty_acquires_nothing` |
| 50 multi-zone credential selection | deferred | [ECO-286](https://linear.app/keycardlabs/issue/ECO-286) (single-zone, as keycardai-langchain) |

## Integration

| Row | Status | Where |
|---|---|---|
| 1 Keycard-protected MCP server through `MCPToolset(auth=keycard.mcp_auth(...))` | deferred | [SDK-65](https://linear.app/keycardlabs/issue/SDK-65) |
| 2 live-zone smoke for the exchange paths | out of this repo | the two examples under `examples/` are run by hand against a sandbox zone before each release |
| 3 version matrix across the floor and the pinned version | partial | the pinned version is this package's `uv.lock` and runs on every PR; the `pydantic-ai-slim` 2.60 floor passed by hand but has no CI leg. The `sibling floors (pydantic-ai)` leg pins only `keycardai-*` siblings |
| 4 end-to-end eval run | deferred | follows the templates eval harness once a Pydantic AI template exists |

## Beyond the spec

These rows cover behavior the spec doesn't describe yet; the spec reconcile (SDK-61) adds them.

| Behavior | Status | Where |
|---|---|---|
| approval-gated tool (`requires_approval=True`) gets one consent pause when the host resumes through `resume_results()` | covered | `test_approval_gated_tool_gets_one_consent_pause_through_resume_results` |
| the same tool resumed with plain `build_results()` ends in `ToolFailed`, no pause | covered | `test_approval_gated_tool_without_resume_results_fails_instead_of_pausing` |
| KeycardGrant's own pause resumed through `resume_results()` never pauses twice | covered | `test_own_pause_resumed_through_resume_results_never_pauses_twice` |
| `resume_results()` hands pause metadata back and merges the host's | covered | `test_resume_results_hands_metadata_back_and_merges_the_hosts` |
