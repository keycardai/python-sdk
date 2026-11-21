"""Row 8: the secret source acquires once per name, in the first hydration only."""

from __future__ import annotations

from .conftest import OTHER, RESOURCE, base_settings


def test_row_8_fetch_acquires_each_name_once(make_harness) -> None:
    h = make_harness(base_settings(secrets={"CAL_KEY": RESOURCE, "OTHER_KEY": OTHER}))
    source = h.ctx.secret_sources[0]
    assert source.is_enabled({}) is True
    first = source.fetch({}, h.home)
    assert first.secrets == {"CAL_KEY": f"self-for-{RESOURCE}", "OTHER_KEY": f"self-for-{OTHER}"}
    assert first.error is None
    second = source.fetch({}, h.home)
    assert second.secrets == first.secrets
    assert len(h.client.self_calls) == 2
    assert h.client.exchange_calls == []


def test_secret_source_is_disabled_without_names(harness) -> None:
    source = harness.ctx.secret_sources[0]
    assert source.is_enabled({}) is False
    assert source.fetch({}, harness.home).secrets == {}


def test_secret_source_skips_a_denied_resource(make_harness) -> None:
    h = make_harness(base_settings(secrets={"CAL_KEY": RESOURCE}))
    h.client.self_granted = False
    assert h.ctx.secret_sources[0].fetch({}, h.home).secrets == {}
