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
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Annotated

import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from keycardai import temporal as kt
from keycardai.oauth.server import ClientSecret
from keycardai.temporal import KeycardInterceptor, Subject, access, grant

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
        return [f"{receipt.approved_by}:{receipt.token_length}", audit]


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
    client: Client = temporal_env.client
    task_queue = f"keycard-hygiene-{uuid.uuid4()}"
    interceptor = KeycardInterceptor(
        "https://zone.test",
        credential=ClientSecret(("worker-id", "worker-secret")),
        subject_token_provider=session_lookup,
    )
    order = Order(order_id="ord-42", approver_id="alice")

    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[ApprovalWorkflow],
            activities=[post_ledger_entry, audit_sync],
            activity_executor=executor,
            interceptors=[interceptor],
        ):
            handle = await client.start_workflow(
                ApprovalWorkflow.run,
                order,
                id=f"approval-{uuid.uuid4()}",
                task_queue=task_queue,
            )
            result = await handle.result()

    assert result == [f"alice:{len(TOKEN)}", "audited ord-42 for alice"]

    history = await handle.fetch_history()
    raw = history.to_json()
    strings = _strings(json.loads(raw))
    joined = "\n".join(strings)

    # Positive control: the scan reaches into payloads, because the identity
    # reference the workflow legitimately carries is found.
    assert "alice" in joined
    assert "ord-42" in joined
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
        from temporalio.common import RetryPolicy

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
        credential=ClientSecret(("worker-id", "worker-secret")),
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
