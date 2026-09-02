# Test matrix

The test strategy for the LangChain integration, tracked in
[ECO-224](https://linear.app/keycardlabs/issue/ECO-224). Each row is either
covered by a named test or explicitly deferred with an issue reference.
The TypeScript package (`@keycardai/langchain`,
[ECO-221](https://linear.app/keycardlabs/issue/ECO-221)) mirrors this matrix
when it exists; parity rows apply to it from day one.

Run the suite with `just test-package langchain`, or directly:

```bash
cd packages/langchain && uv run --extra test pytest tests/ -v
```

## Unit

| Row | Status | Where |
|---|---|---|
| Grant runs before the tool; AccessContext reachable from inside it | covered | `test_on_behalf_of_exchanges_the_callers_token`, `test_grant_serves_tools_outside_the_agent` |
| Keycard params never leak into the model-facing tool schema | covered | `test_tool_schema_carries_no_keycard_plumbing` |
| Non-throwing error model: failures recorded, `access()` raises `ResourceAccessError` | covered | `test_missing_identity_is_recorded_not_raised`, `test_resource_error_raises_only_on_access` (seam suite) |
| Partial behavior locked: one denied resource does not poison a granted one | covered | `test_partial_grant_yields_token_and_resource_error_side_by_side` |
| Impersonation mode routes to the substitute-user path | covered | `test_impersonation_uses_the_substitute_user_path` (wire format of the substitute-user token is owned by keycardai-oauth's suite) |
| As-itself mode: client credentials, no exchange, scopes honored, denial never interrupts | covered | `test_as_self_uses_client_credentials_not_exchange`, `test_as_self_request_scopes_reach_the_grant`, `test_as_self_denial_is_an_error_never_an_interrupt` |
| Expired subject token routes to sign-in, not consent; opaque tokens pass through | covered | `test_expired_subject_token_pauses_for_sign_in_not_consent`, `test_expired_subject_token_without_sign_in_url_is_an_error`, `test_unexpired_jwt_subject_token_exchanges_normally` |
| Sync path runs on one persistent loop (client cache effective) | covered | `test_sync_path_runs_on_one_persistent_loop` |
| Multi-zone credential selection: issuer-keyed, fail-closed | deferred | [ECO-286](https://linear.app/keycardlabs/issue/ECO-286) (not implemented; middleware is single-zone today) |
| Testing seams themselves, including the resource-pinned form | covered | `tests/test_testing_seam.py` (all five) |

## Inbound authentication (served agents)

Hermetic: verification runs through the injectable `verify` seam, so no zone and
no network. Authorization rows dispatch through the server's own handler
resolution, user normalization and owner filter matcher
(`langgraph_api.auth.custom`, `langgraph_runtime_inmem.ops`), so a row passes
only if a deployment would behave that way. All rows live in
`tests/test_served_auth.py`.

| Row | Status | Where |
|---|---|---|
| Valid bearer yields the verified identity and the raw bearer as `subject_token` | covered | `test_valid_bearer_yields_identity_and_raw_token` |
| Missing, malformed and invalid bearers are 401 with a `WWW-Authenticate` challenge naming the zone metadata URL | covered | `test_missing_bearer_is_challenged`, `test_invalid_bearer_is_challenged`, `test_non_bearer_authorization_is_challenged` |
| Rejection is starlette's `HTTPException`, not the SDK's, which drops headers and coerces statuses | covered | `test_rejection_is_the_starlette_exception_that_keeps_headers` |
| An unexpected verifier failure is still a challenge, never a 500 | covered | `test_an_exploding_verifier_is_a_challenge_not_a_500` |
| Owner stamped on thread creation, run creation and thread updates from the verified identity, unspoofable from the request body | covered | `test_thread_creation_stamps_the_owner`, `test_body_supplied_owner_cannot_be_forged`, `test_thread_update_stamps_the_owner`, `test_cross_owner_thread_update_is_filtered` |
| Cross-owner thread read and cross-owner resume filtered out | covered | `test_cross_owner_thread_read_is_filtered`, `test_cross_owner_resume_is_filtered` |
| Store namespaces owner-prefixed, dot-free, distinct per caller, and scoped even on prefix-less listing | covered | `test_store_items_are_scoped_to_the_caller`, `test_store_owner_segments_are_distinct_per_caller`, `test_prefixless_namespace_listing_is_scoped_to_the_caller` |
| Unmatched resource and action pairs denied (the framework fails open), assistant reads still open | covered | `test_unmatched_resource_action_pairs_are_denied`, `test_assistant_reads_stay_open_to_authenticated_callers` |
| Studio user denied | covered | `test_studio_user_is_denied` |
| `identity_source="auth_user"` grants under the verified caller end to end, two callers never cross, caller-supplied context ignored | covered | `test_auth_user_mode_grants_under_the_verified_caller`, `test_auth_user_mode_keeps_two_callers_apart`, `test_auth_user_mode_ignores_caller_supplied_context` |
| Configuration guards: unknown identity source, and a second identity source alongside `auth_user` | covered | `test_unknown_identity_source_is_rejected`, `test_auth_user_mode_rejects_a_second_identity_source`, `test_caller_from_config_needs_both_identity_and_token` |
| Live two-user acceptance on a real zone and a real deployment | out of this repo | `langchain-fly-demo` ([ECO-238](https://linear.app/keycardlabs/issue/ECO-238)), which swaps to this surface once it releases |

## Integration

| Row | Status | Where |
|---|---|---|
| Keycard-protected MCP server via `langchain-mcp-adapters` interceptors | deferred | [ECO-219](https://linear.app/keycardlabs/issue/ECO-219) (the `[mcp]` extra does not exist yet) |
| Live-zone smoke for the exchange paths (gated, not per-PR) | out of this repo | templates eval ([keycardai/templates#28](https://github.com/keycardai/templates/pull/28), [ECO-313](https://linear.app/keycardlabs/issue/ECO-313)) plus the deployed `langchain-fly-demo` |
| Version matrix: langchain/langgraph floors, mcp 1.x vs 2.x resolution | covered | `langchain-version-matrix` job in `.github/workflows/pr.yml`: floors leg (`uv pip install --resolution lowest-direct`), customer pin leg (langchain 1.3.13 / langgraph 1.2.11 on Python 3.13), and the mcp resolution clash check against `langchain-mcp-adapters` ([ECO-287](https://linear.app/keycardlabs/issue/ECO-287)) |

Live coverage is deliberately not carried by this repo, so ECO-288 is canceled: the
templates eval drives the on-behalf-of exchange through this package weekly inside the
`agent-python-langchain` template against a real zone. The deployed `langchain-fly-demo`
remains the standing production evidence for as-itself and the audit actor difference.

## Interrupt flow

| Row | Status | Where |
|---|---|---|
| Interrupt payload shape (`authorization_required`, `sign_in_required` + `reason`) | covered | `test_authorization_interrupt_pauses_then_resumes`, `test_sign_in_interrupt_picks_up_identity_without_a_restart`, `test_expired_subject_token_pauses_for_sign_in_not_consent` |
| Resume retries the grant and the tool proceeds | covered | same two resume tests |
| Nothing side-effectful runs before the interrupt resolves | covered | `test_no_tool_executes_before_an_interrupt_resolves` |
| Checkpointer-less fallback auth tool | covered | `test_sign_in_falls_back_to_tool_output_without_a_checkpointer`, `test_consent_falls_back_to_tool_output_without_a_checkpointer`, `test_expired_subject_token_falls_back_with_the_expiry_reason`, `test_fallback_output_never_runs_the_wrapped_tool`, `test_fallback_tells_the_model_to_relay_the_url_verbatim`, `test_fallback_output_is_identical_on_the_async_path`, `test_sign_in_fallback_is_identical_on_the_async_path`, `test_fallback_output_carries_the_interrupt_payload_fields` (payload parity, all three failures) |

## E2E

| Row | Status | Where |
|---|---|---|
| Templates `eval/` run: ephemeral zone, provision from SPEC.md, consent, authenticated call | deferred | [ECO-223](https://linear.app/keycardlabs/issue/ECO-223) (template branch exists, gated on the package release) |

## Process guards

| Row | Status | Where |
|---|---|---|
| Suite wired into CI targets from day one | covered | justfile `test` / `test-coverage` targets (the fastmcp omission was [ECO-172](https://linear.app/keycardlabs/issue/ECO-172)) |
| Coverage gate | covered | `--cov-fail-under=85` in the justfile target |
| Cross-language parity claims tested on both halves | deferred | applies when [ECO-221](https://linear.app/keycardlabs/issue/ECO-221) lands; no cross-language claims in this README until then |
