"""Tests for the LLM wrapper, provider routing, fallback, JSON repair."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from backend.agent.llm import (
    LLM,
    LLMError,
    LLMRateLimited,
    LLMResponse,
    get_llm,
    reset_llm,
    set_llm,
)


class _SchemaForTest(BaseModel):
    foo: str
    bar: int


# ---------------------------------------------------------------------------
# Singleton hooks
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_llm_returns_same_instance(self):
        reset_llm()
        a = get_llm()
        b = get_llm()
        assert a is b

    def test_set_llm_replaces(self):
        reset_llm()
        sentinel = object()
        set_llm(sentinel)  # type: ignore[arg-type]
        assert get_llm() is sentinel
        reset_llm()  # cleanup

    def test_reset_llm_clears(self):
        get_llm()
        reset_llm()
        a = get_llm()
        assert a is not None


# ---------------------------------------------------------------------------
# chat(). primary success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChatPrimary:
    async def test_groq_success(self, monkeypatch):
        llm = LLM()

        class _FakeGroq:
            async def chat(self, **kwargs):
                return "hello", {"prompt_tokens": 10, "completion_tokens": 5, "model": "llama-3.1-8b-instant"}

        monkeypatch.setattr(llm, "_get_groq", lambda: _FakeGroq())

        resp = await llm.chat([{"role": "user", "content": "hi"}])
        assert resp.text == "hello"
        assert resp.provider == "groq"
        assert resp.used_fallback is False
        assert resp.input_tokens == 10
        assert resp.output_tokens == 5


# ---------------------------------------------------------------------------
# Fallback on 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFallback:
    async def test_falls_back_to_ollama_on_persistent_429(self, monkeypatch):
        llm = LLM()

        class _AlwaysRateLimited:
            async def chat(self, **kwargs):
                raise LLMRateLimited("429")

        class _OkOllama:
            async def chat(self, **kwargs):
                return "ollama text", {"prompt_tokens": 1, "completion_tokens": 2, "model": "qwen2.5:7b"}

        monkeypatch.setattr(llm, "_get_groq", lambda: _AlwaysRateLimited())
        monkeypatch.setattr(llm, "_get_ollama", lambda: _OkOllama())

        # Disable retry sleeps
        async def no_sleep(_t):
            return None

        import asyncio
        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        resp = await llm.chat(
            [{"role": "user", "content": "x"}],
            max_retries=0,  # fail fast
        )
        assert resp.provider == "ollama"
        assert resp.used_fallback is True
        assert resp.text == "ollama text"

    async def test_both_fail_raises(self, monkeypatch):
        llm = LLM()

        class _Fail:
            async def chat(self, **kwargs):
                raise RuntimeError("nope")

        class _AlsoFail:
            async def chat(self, **kwargs):
                raise RuntimeError("also nope")

        # Make groq raise an LLMError so we move on to fallback
        class _RaiseLLMError:
            async def chat(self, **kwargs):
                raise LLMError("groq down")

        monkeypatch.setattr(llm, "_get_groq", lambda: _RaiseLLMError())
        monkeypatch.setattr(llm, "_get_ollama", lambda: _AlsoFail())

        with pytest.raises(LLMError):
            await llm.chat([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# JSON parsing + repair retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChatJson:
    async def test_valid_json_parses(self, monkeypatch):
        llm = LLM()

        class _OkGroq:
            async def chat(self, **kwargs):
                return '{"foo": "hello", "bar": 7}', {"model": "stub"}

        monkeypatch.setattr(llm, "_get_groq", lambda: _OkGroq())

        obj, resp = await llm.chat_json(
            messages=[{"role": "user", "content": "x"}],
            schema=_SchemaForTest,
        )
        assert obj.foo == "hello"
        assert obj.bar == 7

    async def test_repair_on_invalid_json(self, monkeypatch):
        llm = LLM()

        attempts = {"n": 0}

        class _RepairableGroq:
            async def chat(self, **kwargs):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    return "not json", {"model": "stub"}
                return '{"foo": "fixed", "bar": 42}', {"model": "stub"}

        monkeypatch.setattr(llm, "_get_groq", lambda: _RepairableGroq())

        obj, resp = await llm.chat_json(
            messages=[{"role": "user", "content": "x"}],
            schema=_SchemaForTest,
        )
        assert obj.foo == "fixed"
        assert obj.bar == 42
        assert attempts["n"] == 2

    async def test_validation_error_on_missing_field(self, monkeypatch):
        llm = LLM()

        class _ValidJsonWrongShape:
            async def chat(self, **kwargs):
                return '{"foo": "x"}', {"model": "stub"}  # missing 'bar'

        monkeypatch.setattr(llm, "_get_groq", lambda: _ValidJsonWrongShape())

        with pytest.raises(Exception):
            await llm.chat_json(
                messages=[{"role": "user", "content": "x"}],
                schema=_SchemaForTest,
            )


# ---------------------------------------------------------------------------
# Model role routing
# ---------------------------------------------------------------------------


class TestModelRoleSelection:
    def test_groq_primary(self):
        llm = LLM()
        assert llm._model_for_role("primary", "groq") == llm.settings.groq_model_primary

    def test_groq_cheap(self):
        llm = LLM()
        assert llm._model_for_role("cheap", "groq") == llm.settings.groq_model_cheap

    def test_groq_fallback_uses_primary_model(self):
        llm = LLM()
        # 'fallback' role on groq should use primary model
        assert llm._model_for_role("fallback", "groq") == llm.settings.groq_model_primary

    def test_ollama_uses_default_model(self):
        llm = LLM()
        assert llm._model_for_role("primary", "ollama") == llm.settings.ollama_model
        assert llm._model_for_role("cheap", "ollama") == llm.settings.ollama_model
