"""History-hygiene proof: run a real workflow and scan its recorded history.

A Temporal dev server (``WorkflowEnvironment.start_local``) runs the workflow
below with ``KeycardInterceptor`` installed. The OAuth client is stubbed so no
zone is involved, but everything else is real: the interceptor chain, the
sandboxed workflow, the thread-pool executor for the sync activity, and the
history the server records. The test then walks every payload in that history
(including base64-decoded payload data) and asserts the minted token never
appears, while the identity reference the workflow legitimately carries does.

The dev server binary is downloaded by temporalio on first use and cached.
"""

from __future__ import annotations

import base64
import contextvars
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Annotated

import pytest
import temporalio.client
import temporalio.converter
import temporalio.worker
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from keycardai import temporal as kt
from keycardai.oauth.server import ClientSecret
from keycardai.temporal import Header, KeycardInterceptor, Subject, access, grant

RESOURCE = "https://ledger.test"
# JWT-shaped so the scan exercises the same pattern a real Keycard token has.
TOKEN = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJhbGljZSIsImF1ZCI6Imh0dHBzOi8vbGVkZ2VyLnRlc3QifQ."
    "c2VjcmV0LXNpZ25hdHVyZS1ieXRlcy1oZXJl"
)
JWT_SHAPE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}")


@dataclass
class Order:
    order_id: str
    approver_id: Annotated[str, Subject()]


@dataclass
class Receipt:
    order_id: str
    approved_by: str
    token_length: int


@grant(RESOURCE)
@activity.defn
async def post_ledger_entry(order: Order) -> Receipt:
    token = access().access_token  # the user's token; stays inside this call
    assert token == TOKEN
    return Receipt(order.order_id, order.approver_id, len(token))


@grant(RESOURCE, subject_from="approver_id", impersonate=True)
@activity.defn
def audit_sync(order_id: str, approver_id: str) -> str:
    # Sync activity: runs on the worker's thread-pool executor, which copies
    # the contextvars set by the interceptor into the worker thread.
    token = access().access_token
    assert token == TOKEN
    return f"audited {order_id} for {approver_id}"


@grant(RESOURCE, subject_from=Header("user-id"))
@activity.defn
async def notify_by_header(order_id: str) -> str:
    # No identity in the arguments: the propagation interceptor below set it
    # in the activity's headers at workflow start.
    token = access().access_token
    assert token == TOKEN
    return f"notified {order_id}"


@workflow.defn
class ApprovalWorkflow:
    @workflow.run
    async def run(self, order: Order) -> list[str]:
        receipt = await workflow.execute_activity(
            post_ledger_entry,
            order,
            start_to_close_timeout=timedelta(seconds=10),
        )
        audit = await workflow.execute_activity(
            audit_sync,
            args=[order.order_id, order.approver_id],
            start_to_close_timeout=timedelta(seconds=10),
        )
        notified = await workflow.execute_activity(
            notify_by_header,
            order.order_id,
            start_to_close_timeout=timedelta(seconds=10),
        )
        return [f"{receipt.approved_by}:{receipt.token_length}", audit, notified]


# --- a minimal context-propagation interceptor, after Temporal's sample -------
# https://github.com/temporalio/samples-python/tree/main/context_propagation

USER_ID_HEADER = "user-id"
current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_id", default=None
)


def _set_header_from_context(input, payload_converter) -> None:
    user_id = current_user_id.get()
    if user_id is not None:
        input.headers = {
            **input.headers,
            USER_ID_HEADER: payload_converter.to_payloads([user_id])[0],
        }


class UserIdPropagator(temporalio.client.Interceptor, temporalio.worker.Interceptor):
    """Client start_workflow puts the caller's id in a header; the workflow
    reads it back and copies it onto every activity it starts."""

    def intercept_client(self, next):
        return _ClientOutbound(next)

    def workflow_interceptor_class(self, input):
        return _WorkflowInbound


class _ClientOutbound(temporalio.client.OutboundInterceptor):
    async def start_workflow(self, input):
        _set_header_from_context(
            input, temporalio.converter.default().payload_converter
        )
        return await super().start_workflow(input)


class _WorkflowInbound(temporalio.worker.WorkflowInboundInterceptor):
    def init(self, outbound) -> None:
        super().init(_WorkflowOutbound(outbound))

    async def execute_workflow(self, input):
        payload = input.headers.get(USER_ID_HEADER)
        if payload is not None:
            user_id = workflow.payload_converter().from_payloads([payload], [str])[0]
            current_user_id.set(user_id)
        return await super().execute_workflow(input)


class _WorkflowOutbound(temporalio.worker.WorkflowOutboundInterceptor):
    def start_activity(self, input):
        _set_header_from_context(input, workflow.payload_converter())
        return super().start_activity(input)


class StubOAuthClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def exchange_token(self, request):
        assert request.subject_token == "session-for-alice"
        assert request.resource == RESOURCE
        return SimpleNamespace(access_token=TOKEN)

    async def impersonate(self, *, user_identifier, resource, scope=None):
        return SimpleNamespace(access_token=TOKEN)


async def session_lookup(approver_id: str) -> str:
    return f"session-for-{approver_id}"


def _strings(node) -> list[str]:
    """Every string in a JSON tree, plus decoded forms of base64 strings."""
    out: list[str] = []
    if isinstance(node, dict):
        for v in node.values():
            out.extend(_strings(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_strings(v))
    elif isinstance(node, str):
        out.append(node)
        try:
            decoded = base64.b64decode(node, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return out
        out.append(decoded)
        try:
            out.extend(_strings(json.loads(decoded)))
        except ValueError:
            pass
    return out


@pytest.fixture
async def temporal_env():
    async with await WorkflowEnvironment.start_local() as env:
        yield env


async def test_no_token_anywhere_in_history(temporal_env, monkeypatch):
    monkeypatch.setattr(kt, "AsyncClient", StubOAuthClient)
    propagator = UserIdPropagator()
    client = Client(**{**temporal_env.client.config(), "interceptors": [propagator]})
    task_queue = f"keycard-hygiene-{uuid.uuid4()}"
    interceptor = KeycardInterceptor(
        "https://zone.test",
        application_credential=ClientSecret(("worker-id", "worker-secret")),
        subject_token_provider=session_lookup,
    )
    order = Order(order_id="ord-42", approver_id="alice")

    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[ApprovalWorkflow],
            activities=[post_ledger_entry, audit_sync, notify_by_header],
            activity_executor=executor,
            interceptors=[propagator, interceptor],
        ):
            # What a request handler does once: name the caller for this
            # workflow start. The header, not the arguments, carries it.
            current_user_id.set("alice")
            handle = await client.start_workflow(
                ApprovalWorkflow.run,
                order,
                id=f"approval-{uuid.uuid4()}",
                task_queue=task_queue,
            )
            result = await handle.result()

    assert result == [
        f"alice:{len(TOKEN)}",
        "audited ord-42 for alice",
        "notified ord-42",
    ]

    history = await handle.fetch_history()
    raw = history.to_json()
    strings = _strings(json.loads(raw))
    joined = "\n".join(strings)

    # Positive control: the scan reaches into payloads, because the identity
    # reference the workflow legitimately carries is found.
    assert "alice" in joined
    assert "ord-42" in joined
    # The header-located reference is in history too, under the header key:
    # headers are recorded like arguments, which is why only a reference may
    # travel there.
    header_payloads = [
        event.activity_task_scheduled_event_attributes.header.fields[USER_ID_HEADER]
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and USER_ID_HEADER
        in event.activity_task_scheduled_event_attributes.header.fields
    ]
    assert len(header_payloads) == 3
    assert all(p.data == b'"alice"' for p in header_payloads)
    # The proof: the token is nowhere in the recorded history, in any form.
    assert TOKEN not in raw
    assert TOKEN not in joined
    assert not JWT_SHAPE.search(joined)
    for piece in TOKEN.split("."):
        assert piece not in joined
    assert "session-for-alice" not in joined
    assert "worker-secret" not in joined


@workflow.defn
class MisconfiguredWorkflow:
    @workflow.run
    async def run(self, order: Order) -> str:

        receipt = await workflow.execute_activity(
            post_ledger_entry_obo,
            order,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(
                non_retryable_error_types=["GrantConfigurationError"],
            ),
        )
        return receipt.approved_by


@grant(RESOURCE)  # Order.approver_id carries the Subject() marker: on-behalf-of
@activity.defn
async def post_ledger_entry_obo(order: Order) -> Receipt:
    token = access().access_token
    return Receipt(order.order_id, order.approver_id, len(token))


async def test_grant_configuration_error_fails_fast_when_listed_non_retryable(
    temporal_env, monkeypatch
):
    """The class NAME is the retry-policy contract: a worker configured
    without a subject_token_provider raises GrantConfigurationError before the
    activity body, and listing that name in non_retryable_error_types must
    stop retries after one attempt, through temporalio's real name mapping."""
    monkeypatch.setattr(kt, "AsyncClient", StubOAuthClient)
    client: Client = temporal_env.client
    task_queue = f"keycard-misconfig-{uuid.uuid4()}"
    # The declaration is valid; the worker configuration disagrees: no
    # subject_token_provider, so the on-behalf-of grant cannot be honored.
    interceptor = KeycardInterceptor(
        "https://zone.test",
        application_credential=ClientSecret(("worker-id", "worker-secret")),
    )
    order = Order(order_id="ord-43", approver_id="alice")

    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[MisconfiguredWorkflow],
        activities=[post_ledger_entry_obo],
        interceptors=[interceptor],
    ):
        handle = await client.start_workflow(
            MisconfiguredWorkflow.run,
            order,
            id=f"misconfig-{uuid.uuid4()}",
            task_queue=task_queue,
        )
        from temporalio.client import WorkflowFailureError

        with pytest.raises(WorkflowFailureError) as failure:
            await handle.result()

    from temporalio.exceptions import ActivityError, ApplicationError

    cause = failure.value.cause
    assert isinstance(cause, ActivityError)
    assert isinstance(cause.cause, ApplicationError)
    assert cause.cause.type == "GrantConfigurationError"

    history = await handle.fetch_history()
    attempts = sum(
        1
        for event in history.events
        if event.HasField("activity_task_started_event_attributes")
    )
    assert attempts == 1


# --- spec row 59: the provider's context matches the running activity ---

seen_context: list[tuple[kt.SubjectTokenContext, dict]] = []


async def context_lookup(ref: str, context: kt.SubjectTokenContext) -> str:
    # Called inside the activity execution, so activity.info() is the one the
    # body will see; record both for the comparison below.
    info = activity.info()
    seen_context.append(
        (
            context,
            {
                "workflow_id": info.workflow_id,
                "workflow_run_id": info.workflow_run_id,
                "activity_type": info.activity_type,
                "attempt": info.attempt,
            },
        )
    )
    return f"session-for-{ref}"


class TwoResourceStub(StubOAuthClient):
    async def exchange_token(self, request):
        assert request.subject_token == "session-for-alice"
        assert request.resource in (RESOURCE, SECOND_RESOURCE)
        return SimpleNamespace(access_token=TOKEN)


SECOND_RESOURCE = "https://second.test"


@grant(RESOURCE, SECOND_RESOURCE, subject_from="approver_id")
@activity.defn
async def two_resource_obo(order_id: str, approver_id: str) -> str:
    assert access(RESOURCE).access_token == TOKEN
    assert access(SECOND_RESOURCE).access_token == TOKEN
    return f"{order_id}:{approver_id}"


@workflow.defn
class ContextWorkflow:
    @workflow.run
    async def run(self, order_id: str) -> str:
        return await workflow.execute_activity(
            two_resource_obo,
            args=[order_id, "alice"],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


async def test_provider_context_matches_the_running_activity(temporal_env, monkeypatch):
    monkeypatch.setattr(kt, "AsyncClient", TwoResourceStub)
    seen_context.clear()
    task_queue = f"keycard-context-{uuid.uuid4()}"
    interceptor = KeycardInterceptor(
        "https://zone.test",
        application_credential=ClientSecret(("worker-id", "worker-secret")),
        subject_token_provider=context_lookup,
    )
    workflow_id = f"context-{uuid.uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=task_queue,
        workflows=[ContextWorkflow],
        activities=[two_resource_obo],
        interceptors=[interceptor],
    ):
        handle = await temporal_env.client.start_workflow(
            ContextWorkflow.run, "ord-7", id=workflow_id, task_queue=task_queue
        )
        assert await handle.result() == "ord-7:alice"

    assert len(seen_context) == 1
    context, info = seen_context[0]
    assert context.workflow_id == workflow_id == info["workflow_id"]
    assert context.workflow_run_id == handle.result_run_id == info["workflow_run_id"]
    assert context.activity_type == "two_resource_obo" == info["activity_type"]
    assert context.attempt == 1 == info["attempt"]
    assert context.resources == (RESOURCE, SECOND_RESOURCE)
