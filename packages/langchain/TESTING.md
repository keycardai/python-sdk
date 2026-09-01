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

## Integration

| Row | Status | Where |
|---|---|---|
| Keycard-protected MCP server via `langchain-mcp-adapters` interceptors | deferred | [ECO-219](https://linear.app/keycardlabs/issue/ECO-219) (the `[mcp]` extra does not exist yet) |
| Live-zone smoke for the exchange paths (gated, not per-PR) | out of this repo | templates eval ([keycardai/templates#28](https://github.com/keycardai/templates/pull/28), [ECO-313](https://linear.app/keycardlabs/issue/ECO-313)) plus the deployed `langchain-fly-demo` |
| Version matrix: langchain/langgraph floors, mcp 1.x vs 2.x resolution | deferred | [ECO-287](https://linear.app/keycardlabs/issue/ECO-287) (CI runs latest resolutions only today) |

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
| Checkpointer-less fallback auth tool | deferred | remaining scope of [ECO-220](https://linear.app/keycardlabs/issue/ECO-220) (interrupts shipped; the fallback tool did not) |

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
