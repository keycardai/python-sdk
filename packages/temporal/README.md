# keycardai-temporal

Per-call Keycard token minting for Temporal Python workers, built on `keycardai-oauth`.

An activity declares the resources it needs with `@grant(resource, ...)`, the worker's `KeycardInterceptor` mints a fresh token for each of them on every activity execution, and `access()` returns them inside the activity. Nothing is written to workflow history.

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
PRICING = "https://pricing.example.com"


@grant(LEDGER)
@activity.defn
async def post_entry(order_id: str) -> str:
    token = access().access_token  # fresh for this execution only
    ...  # call the ledger with the token
    return "posted"


@grant(LEDGER, PRICING)  # several resources, minted together
@activity.defn
async def reprice(order_id: str) -> str:
    ledger_token = access(LEDGER).access_token
    pricing_token = access(PRICING).access_token
    ...
    return "repriced"


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
        activities=[post_entry, reprice],
        interceptors=[interceptor],
    ):
        ...
```

### Credential discovery

With no `credential` argument, `KeycardInterceptor` calls `keycardai.oauth.server.discover_credential()`, the SDK-wide environment convention:

- `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET` together build a `ClientSecret`.
- A token file named by `KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE`, `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`, `AWS_WEB_IDENTITY_TOKEN_FILE`, or `AZURE_FEDERATED_TOKEN_FILE` builds a `WorkloadIdentity`.
- `KEYCARD_APPLICATION_CREDENTIAL_TYPE` (`client_secret` or `workload_identity`; `eks_workload_identity` is a legacy alias) names the type to use, and wins over everything else in the environment.

When the environment can build more than one credential and the type variable does not choose between them, the worker fails at startup with `GrantConfigurationError` instead of guessing. EKS IRSA injects `AWS_WEB_IDENTITY_TOKEN_FILE` into pods automatically, so a worker meant to use a client secret on EKS must set `KEYCARD_APPLICATION_CREDENTIAL_TYPE=client_secret`. Any `keycardai.oauth.server.ApplicationCredential` can also be passed explicitly, which skips discovery entirely.

## Keycard setup

In your Keycard zone, once:

1. Create an application for the worker with a credential (a `ClientSecret`, or any workload identity credential the worker's platform supports).
2. Create the resource the activities will call. For plain client-credentials issuance, the Zone Provider is enough as its credential provider: nothing exchanges from its tokens.
3. Add the resource to the application's dependencies. App-only issuance needs the dependency; with no user there is no consent step.
4. For on-behalf-of activities, one more piece of zone topology: the exchange rule requires the exchanging application to provide the resource the subject token is audienced to, so the worker's application needs a small anchor resource of its own, with user sessions audienced to it.

The resource identifier in `@grant(...)` must match the console registration byte for byte; a trailing character difference reads as a different resource and policy denies it.

## Several resources per activity

`@grant("res-a", "res-b")` takes any number of resources as positional arguments (at least one; duplicates are rejected at decoration time). Every declared resource is minted before the body runs, under the one identity the grant declares. Inside the body, `access()` with no argument returns the token of a single-resource grant; under a multi-resource grant it raises `GrantConfigurationError`, and `access(resource)` selects the token by name. `access(resource)` also works under a single-resource grant when the name matches.

Minting is all-or-nothing: the body never runs with partial credentials. If any declared resource fails permanently, the activity fails with the non-retryable `KeycardAccessDenied` error naming that resource, even when the others minted. Otherwise the first transient failure raises the retryable `KeycardMintFailed` naming its resource, and the activity retry policy governs what happens next.

## Identity modes

The identity mode is per activity: one subject applies to every resource in the grant.

- `@grant(resource, ...)`: the application acts as itself (client credentials). Requires a `ClientSecret` credential.
- `@grant(resource, ..., subject_from=...)`: the application acts on behalf of a user. The activity input carries an identity reference (a user id, never a token). The interceptor's `subject_token_provider`, an application-supplied session lookup, returns that user's current session token, and an RFC 8693 exchange turns it into a token for each resource.
- `@grant(resource, ..., subject_from=..., impersonate=True)`: impersonation, for workflows that outlive the user's session. The located value is a stable user identifier (email or oid) sent directly to the zone, which mints a short-lived substitute-user token for each resource. No session lookup runs and no `subject_token_provider` is needed. This is a different trust model from delegation: the worker asserts who the user is, and zone policy is the control. It requires a confidential client, application consent set to implicit, each resource declared as a dependency of the application, a prior delegated grant established by the user for each resource, and zone policy that explicitly permits the application to impersonate (forbidden by default). Prefer live delegation whenever the user's session is still expected to exist.

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

## The OpenAI Agents plugin

`temporalio.contrib.openai_agents.OpenAIAgentsPlugin` runs every model call as an activity of its own, registered by the plugin before any of your activities exist, so neither `@grant` nor the interceptor can hand it a credential. `keycardai.temporal.openai_agents.KeycardOpenAIProvider` fills that seat: a `ModelProvider` whose OpenAI client resolves its key through Keycard on each request instead of reading `OPENAI_API_KEY` once at startup.

```bash
pip install "keycardai-temporal[openai-agents]" "temporalio[openai-agents]"
```

The intended end state for a worker: the interceptor once, `@grant` on each activity, and the plugin configured through Keycard for the model.

```python
from datetime import timedelta

from temporalio.client import Client
from temporalio.contrib.openai_agents import ModelActivityParameters, OpenAIAgentsPlugin
from temporalio.worker import Worker

from keycardai.temporal import KeycardInterceptor, access, grant
from keycardai.temporal.openai_agents import KeycardOpenAIProvider

ZONE = "https://your-zone.keycard.cloud"


@grant("urn:mongodb:atlas:orders")
async def load_orders(customer_id: str) -> list[dict]:
    return await atlas_client(access().access_token).find(customer_id)


async def main() -> None:
    client = await Client.connect(
        "localhost:7233",
        plugins=[
            OpenAIAgentsPlugin(
                model_params=ModelActivityParameters(
                    start_to_close_timeout=timedelta(seconds=60)
                ),
                model_provider=KeycardOpenAIProvider(
                    ZONE, "urn:vault:openai-api-key", refresh=timedelta(minutes=5)
                ),
            )
        ],
    )
    worker = Worker(
        client,
        task_queue="orders",
        workflows=[OrderWorkflow],
        activities=[load_orders],
        interceptors=[KeycardInterceptor(ZONE)],
    )
    await worker.run()
```

`KeycardOpenAIProvider(zone_url, resource, credential=None, *, refresh=timedelta(minutes=5), base_url=None, use_responses=None)` takes the zone URL and the identifier of the resource whose vault holds the OpenAI key. The credential is the same as the interceptor's: an explicit `ApplicationCredential`, or the one `discover_credential()` finds in the environment when omitted. It must be a `ClientSecret`, as for any client-credentials `@grant`; other credential types raise `GrantConfigurationError` at construction.

Refresh: the key is minted with a client-credentials grant on the first model call, not at worker startup, and reused for `refresh` (or the grant's `expires_in`, whichever is shorter). The next model call after the window mints again, so a key rotated in Keycard reaches the worker within one refresh window, with no restart. Concurrent model calls on an expired cache share one mint. A permanent grant failure fails the model activity with the same non-retryable `KeycardAccessDenied` a `@grant` activity would raise; a transient one is left to the model activity's retry policy. The plugin requires an explicit `start_to_close_timeout` or `schedule_to_close_timeout` whenever a custom provider is set.

The optional dependencies (`openai-agents`, `openai`) are imported only inside `keycardai.temporal.openai_agents`, and only when a provider is built; `import keycardai.temporal` stays free of them. Without the extra installed, building a provider raises an `ImportError` naming it. `temporalio[openai-agents]` itself stays yours to install, since its version is pinned by your worker, not by this package.

## Design notes

- Tokens never touch durable state. Workflow history is replayable and permanent, so unlike header-based context-propagation interceptors, nothing is written to activity headers, arguments, or return values. The token exists only inside one execution's context. This is also why on-behalf-of activities receive an identity reference instead of a token: the session lookup and exchange happen at the edge, inside the execution, so a session revoked mid-workflow is never replayed from state.
- A mint failure for any declared resource raises before the activity body runs, and there is no fallback path. Tokens live in the SDK's shared `AccessContext` (the same container `keycardai-mcp` uses), but where that idiom is non-throwing, the interceptor converts recorded errors into raises on purpose: in Temporal, raising is the error channel.
- Transient mint failures are retryable; permanent failures are not. The classification is `keycardai-oauth`'s: every typed error carries a `retryable` property, and `exchange_tokens_for_resources` records it in the error dict it stores on the `AccessContext`. Network failures, 5xx and 429 responses, and unclassified errors let the activity retry policy govern what happens next, while permanent failures (`access_denied`, `insufficient_authorization`, `invalid_client`, other 4xx responses, configuration and authentication errors) raise `ApplicationError(type="KeycardAccessDenied", non_retryable=True)` immediately. Misdeclarations surface as `GrantConfigurationError`, retryable by default so a worker redeploy with the fix lets the next retry succeed; list `"GrantConfigurationError"` in the retry policy's `non_retryable_error_types` to give up sooner.
- No token caching, per Keycard's credential rules; per-call mint is the contract. One OAuth client is created per worker and reused; only the tokens are fresh. The one exception is the OpenAI Agents provider's model key, which is a vaulted third-party secret rather than a Keycard token: it is reused for a short `refresh` window rather than minted per request, and re-minted after it, so rotation still propagates without a restart.
- The package wraps its own `keycardai` imports in `workflow.unsafe.imports_passed_through()`, the idiom from Temporal's sentry sample, so consumers import it normally even in files that define workflows.
- Works with async activities and with sync activities on the thread-pool executor (the Temporal SDK copies contextvars into the thread). Sync activities on a process-pool executor are not supported because contextvars do not cross processes.

## Tests

```bash
cd packages/temporal && uv run --extra test pytest tests/ -v
```

`tests/test_interceptor.py` drives the interceptor chain directly with the OAuth client stubbed. `tests/test_openai_agents.py` covers the OpenAI Agents provider with the OAuth client stubbed and the clock faked; two tests that need the Agents SDK and the real plugin skip unless the `openai-agents` extra (and `temporalio[openai-agents]`) is installed. `tests/test_history_hygiene.py` runs a real workflow against a local Temporal dev server (downloaded by `temporalio` on first use), then scans the recorded history, including base64-decoded payloads, and asserts the minted token appears nowhere. Neither needs a Keycard zone.
