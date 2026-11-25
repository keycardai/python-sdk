# Test matrix

Each row of the spec's unit table
([hermes.md](https://github.com/keycardai/keycard-sdk-spec/blob/main/specs/framework-integrations/hermes.md),
tracked in [SDK-133](https://linear.app/keycardlabs/issue/SDK-133)) is covered
by a test named or commented with its row number. Run the suite with
`just test-package hermes`, or directly:

```bash
cd packages/hermes && uv run --extra test pytest tests/ -v
```

Every test is hermetic. `hermes-agent` is not on PyPI, so `tests/hermes_fakes.py`
holds fakes mirroring the v0.23.2 signatures (commit `ae2a81cc`): `PluginContext`
(`register_middleware`, `register_secret_source`, `register_cli_command`,
`register_hook`, `get_config`), the `tool_execution` middleware chain with its
skip-and-continue on a raising callback, and `get_session_env` over per-turn
ContextVars. The OAuth client is a stub; the relay runs over pipes against
`httpx.MockTransport`. Re-verify the fakes whenever the pinned Hermes version
moves.

## Unit

| Row | Where |
|---|---|
| 1 | `test_middleware.py::test_row_1_missing_sign_in_blocks_with_the_login_command` |
| 2 | `test_row_2_engine_failure_returns_a_tool_result_and_never_raises` |
| 3 | `test_row_3_actor_id_is_used_and_user_id_never_read` |
| 4 | `test_row_4_cli_with_no_session_vars_uses_owner_actor` |
| 5 | `test_row_5_no_session_vars_and_no_owner_actor` |
| 6 | `test_row_6_mcp_toolset_acquires_nothing` |
| 7 | `test_row_7_host_isolation_refuses_to_load` |
| 8 | `test_secret_source.py::test_row_8_fetch_acquires_each_name_once` |
| 9 | `test_cli.py::test_row_9_login_registers_once_runs_the_device_flow_and_stores_0600` |
| 10 | `test_cli.py::test_row_10_verify_sign_in_mismatch_stores_nothing_and_names_neither_account` |
| 11 | `test_row_11_refresh_rejected_with_invalid_grant_routes_to_sign_in` |
| 12 | `test_row_12_concurrent_turns_exchange_their_own_actors_token` |
| 13 | `test_row_13_sync_threadpool_and_async_tools_see_the_context` |
| 14 | `test_row_14_denied_grant_with_authorization_url_blocks` |
| 15 | `test_row_15_as_self_with_denied_grant_records_and_runs` |
| 16 | `test_row_16_grant_and_agrant_outside_a_run` |
| 17 | `test_row_17_post_gateway_admission_asks_for_sign_in` (with `sign_in_on_admission: true`) |
| 18 | `test_row_18_near_expiry_refreshes_as_the_public_client_then_exchanges` |
| 19 | `test_row_19_rotated_refresh_token_is_stored_by_rename_at_0600` |
| 20 | `test_row_20_two_threads_share_one_refresh`, `test_row_20_two_processes_share_one_refresh` |
| 21 | `test_row_21_other_refresh_failures_are_per_resource_and_leave_the_store` (network error and 5xx) |
| 22 | `test_cli.py::test_row_22_login_without_a_refresh_token_stores_nothing` |
| 23 | `test_cli.py::test_row_23_login_defaults_to_owner_actor_and_reuses_the_client` |
| 24 | `test_row_24_impersonate_without_an_actors_entry` |
| 25 | `test_cli.py::test_row_25_token_writes_only_the_token_then_fails_cleanly` |
| 26 | `test_row_26_misconfiguration_names_the_setting_and_registers_nothing`, `test_row_26_missing_secret_names_the_setting` |
| 27 | `test_row_27_empty_actor_is_owner_actor` |
| 28 | `test_row_28_actor_aliases_map_an_a2a_peer` |
| 29 | `test_row_29_tool_with_no_resources_runs_with_an_empty_context` |
| 30 | `test_relay.py::test_row_30_401_acquires_a_new_token_and_retries_once` |
| 31 | `test_relay.py::test_row_31_404_replays_initialize_and_retries_on_the_new_session` |
| 32 | `test_relay.py::test_row_32_batch_notification_and_sse` |
| 33 | `test_cli.py::test_row_33_token_as_self_uses_client_credentials_and_reads_no_sign_in` |
| 34 | `test_relay.py::test_row_34_missing_secret_env_fails_every_request_without_a_token_request` |
| 35 | `test_row_35_resources_cover_built_in_tools` |

Rows in `tests/test_middleware.py` unless noted. `tests/test_settings.py` covers
settings validation and the Hermes default seams; `tests/test_testing_seam.py`
covers `keycardai.hermes.testing`.

## Integration

None against a live zone, as the spec says: the framework is not on PyPI and
the sign-in is interactive.

Beyond the spec rows, `tests/test_hardening.py` covers: distinct actors never
sharing a sign-in file, private store directories, the plugin host child
counting as host isolation, `grant()` resolving its profile by Hermes home, the
relay surviving an unexpected error and answering a parse error, `login`
saving under the sign-in lock, UserInfo email matching, secret source
warnings, and the relay's SSE line splitting, session replay and inline
notifications. Rows 44 (an API server turn with an empty actor uses
`owner_actor`) and 45 (an unknown surface with an empty actor is
`missing_identity`) are in `tests/test_middleware.py`.

`tests/test_middleware.py` also covers `agrant()` on the caller's loop
([SDK-142](https://linear.app/keycardlabs/issue/SDK-142)): another task keeps
running while it refreshes and while it waits on another holder's sign-in
lock, two `agrant()` calls on one loop share one refresh, refresh failures
route as on the sync path, and a cancelled `alock` waiter leaves the lock
free. A cancelled or timed out `agrant()` still saves a rotated refresh
token before the cancellation reaches it, a sync `grant()` on the loop
thread of an in-flight `agrant()` refresh returns a retryable
`refresh_failed` instead of hanging the loop, and a caller loop's refresh
client is closed and does not keep the loop alive.
