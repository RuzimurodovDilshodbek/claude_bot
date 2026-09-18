import asyncio

import pytest

import ai
import config


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeModels:
    """behaviour: model -> javob matni | istisno | [ketma-ket javoblar]."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    async def generate_content(self, model, contents, config=None):
        self.calls.append(model)
        item = self.behaviour[model]
        if isinstance(item, list):
            item = item.pop(0)
        if isinstance(item, BaseException):
            raise item
        return FakeResp(item)


class FakeClient:
    def __init__(self, behaviour):
        self.aio = type("Aio", (), {})()
        self.aio.models = FakeModels(behaviour)


@pytest.fixture
def sleeps(monkeypatch):
    calls = []

    async def fake_sleep(sec):
        calls.append(sec)

    monkeypatch.setattr(ai.asyncio, "sleep", fake_sleep)
    return calls


def _busy():
    return RuntimeError("503 UNAVAILABLE: The model is overloaded (high demand)")


def _run(client, models, **kw):
    ai._client = client
    try:
        return asyncio.run(ai._generate("salom", models=models, **kw))
    finally:
        ai._client = None


def test_busy_model_falls_over_to_next_without_waiting(sleeps):
    client = FakeClient({"A": _busy(), "B": "ok"})
    assert _run(client, ["A", "B"]).text == "ok"
    assert client.aio.models.calls == ["A", "B"]
    assert sleeps == []


def test_all_busy_then_second_round_after_a_pause(sleeps):
    client = FakeClient({"A": [_busy(), "ok"], "B": _busy()})
    assert _run(client, ["A", "B"]).text == "ok"
    assert client.aio.models.calls == ["A", "B", "A"]
    assert len(sleeps) == 1


def test_all_busy_raises_gemini_busy_after_attempts(sleeps):
    client = FakeClient({"A": _busy(), "B": _busy()})
    with pytest.raises(ai.GeminiBusy):
        _run(client, ["A", "B"], attempts=3)
    assert client.aio.models.calls == ["A", "B"] * 3
    assert len(sleeps) == 2


def test_non_retryable_error_skips_model_and_raises_last(sleeps):
    client = FakeClient({"A": ValueError("bad request"), "B": ValueError("bad too")})
    with pytest.raises(ValueError, match="bad too"):
        _run(client, ["A", "B"])
    assert client.aio.models.calls == ["A", "B"]
    assert sleeps == []


def test_broken_model_is_not_retried_in_later_rounds(sleeps):
    client = FakeClient({"A": ValueError("bad"), "B": [_busy(), "ok"]})
    assert _run(client, ["A", "B"]).text == "ok"
    assert client.aio.models.calls == ["A", "B", "B"]


def test_transcribe_uses_stt_models_first(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_STT_MODELS", ["lite", "main"])
    client = FakeClient({"lite": "matn", "main": "matn"})
    ai._client = client
    try:
        text = asyncio.run(ai.transcribe(b"\x00" * 10, "audio/ogg"))
    finally:
        ai._client = None
    assert text == "matn"
    assert client.aio.models.calls[0] == "lite"


def test_stt_models_default_puts_flash_lite_first():
    assert config.GEMINI_STT_MODELS[0] == "gemini-3.1-flash-lite"
    assert len(config.GEMINI_STT_MODELS) == len(set(config.GEMINI_STT_MODELS))
