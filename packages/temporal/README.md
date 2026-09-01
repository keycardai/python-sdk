# keycardai-temporal

Per-call Keycard token minting for Temporal Python workers, built on `keycardai-oauth`.

An activity declares the resource it needs with `@grant(resource)`, the worker's `KeycardInterceptor` mints a fresh token for every activity execution, and `access()` returns it inside the activity. Nothing is written to workflow history.

```bash
pip install keycardai-temporal
```

## Quick start

```python
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.worker import Worker

from keycardai.temporal import KeycardInterceptor, access, grant

LEDGER = "https://ledger.example.com"


@grant(LEDGER)
@activity.defn
async def post_entry(order_id: str) -> str:
    token = access().access_token  # fresh for this execution only
    ...  # call the ledger with the token
    return "posted"


@workflow.defn
class SettlementWorkflow:
    @workflow.run
    async def run(self, order_id: str) -> str:
        return await workflow.execute_activity(
            post_entry, order_id, start_to_close_timeout=timedelta(seconds=30)
        )


async def main(client):
    interceptor = KeycardInterceptor("https://<zone-id>.keycard.cloud")
    async with Worker(
        client,
        task_queue="settlement",
        workflows=[SettlementWorkflow],
        activities=[post_entry],
        interceptors=[interceptor],
    ):
        ...
```

With no `credential` argument, `KeycardInterceptor` discovers one from the environment: `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET`, or `KEYCARD_APPLICATION_CREDENTIAL_TYPE=eks_workload_identity` with the token file. Any `keycardai.oauth.server.ApplicationCredential` can also be passed explicitly.

## Identity modes

- `@grant(resource)`: the application acts as itself (client credentials). Requires a `ClientSecret` credential.
- `@grant(resource, subject_from=...)`: the application acts on behalf of a user. The activity input carries an identity reference (a user id, never a token). The interceptor's `subject_token_provider`, an application-supplied session lookup, returns that user's current session token, and an RFC 8693 exchange turns it into a token for the resource.
- `@grant(resource, subject_from=..., impersonate=True)`: impersonation, for workflows that outlive the user's session. The located value is a stable user identifier (email or oid) sent directly to the zone, which mints a short-lived substitute-user token. No session lookup runs and no `subject_token_provider` is needed. This is a different trust model from delegation: the worker asserts who the user is, and zone policy is the control. It requires a confidential client, application consent set to implicit, the resource declared as a dependency of the application, a prior delegated grant established by the user for the resource, and zone policy that explicitly permits the application to impersonate (forbidden by default). Prefer live delegation whenever the user's session is still expected to exist.

### Locating the identity reference

```python
from typing import Annotated
from dataclasses import dataclass

from keycardai.temporal import Subject, grant


@dataclass
class Order:
    order_id: str
    approver_id: Annotated[str, Subject()]


@grant(LEDGER)                                       # Subject() marker, validated at decoration time
async def approve(order: Order) -> None: ...

@grant(LEDGER, subject_from="approver_id")           # parameter name ...
async def approve(order_id: str, approver_id: str) -> None: ...

@grant(LEDGER, subject_from="order.approver_id")     # ... or a dotted path into one
async def approve(order: dict) -> None: ...

@grant(LEDGER, subject_from=lambda order: order["approver_id"])  # sync callable escape hatch
async def approve(order: dict) -> None: ...
```

Use one strategy per activity: a `Subject()` marker together with `subject_from` is rejected at decoration time.

The worker supplies the session lookup:

```python
async def session_token_for(approver_id: str) -> str:
    return await sessions.current_token(approver_id)

interceptor = KeycardInterceptor(
    "https://<zone-id>.keycard.cloud",
    subject_token_provider=session_token_for,
)
```

## Design notes

- Tokens never touch durable state. Workflow history is replayable and permanent, so unlike header-based context-propagation interceptors, nothing is written to activity headers, arguments, or return values. The token exists only inside one execution's context. This is also why on-behalf-of activities receive an identity reference instead of a token: the session lookup and exchange happen at the edge, inside the execution, so a session revoked mid-workflow is never replayed from state.
- A mint failure raises before the activity body runs, and there is no fallback path. Tokens live in the SDK's shared `AccessContext` (the same container `keycardai-mcp` uses), but where that idiom is non-throwing, the interceptor converts recorded errors into raises on purpose: in Temporal, raising is the error channel.
- Transient mint failures are retryable; permanent denials are not. Network failures and unclassified errors let the activity retry policy govern what happens next, while `access_denied`, `insufficient_authorization`, and `invalid_client` raise `ApplicationError(type="KeycardAccessDenied", non_retryable=True)` immediately. Misdeclarations surface as `GrantConfigurationError`, retryable by default so a worker redeploy with the fix lets the next retry succeed; list `"GrantConfigurationError"` in the retry policy's `non_retryable_error_types` to give up sooner.
- No token caching, per Keycard's credential rules; per-call mint is the contract. One OAuth client is created per worker and reused; only the tokens are fresh.
- The package wraps its own `keycardai` imports in `workflow.unsafe.imports_passed_through()`, the idiom from Temporal's sentry sample, so consumers import it normally even in files that define workflows.
- Works with async activities and with sync activities on the thread-pool executor (the Temporal SDK copies contextvars into the thread). Sync activities on a process-pool executor are not supported because contextvars do not cross processes.

## Tests

```bash
cd packages/temporal && uv run --extra test pytest tests/ -v
```

`tests/test_interceptor.py` drives the interceptor chain directly with the OAuth client stubbed. `tests/test_history_hygiene.py` runs a real workflow against a local Temporal dev server (downloaded by `temporalio` on first use), then scans the recorded history, including base64-decoded payloads, and asserts the minted token appears nowhere. Neither needs a Keycard zone.
