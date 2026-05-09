"""Provider-agnostic LLM wrapper with Groq primary + Ollama fallback.

Two roles routed via `model_role` parameter:
  - "primary"  → Groq Llama 3.3 70B   (planning, synthesis)
  - "cheap"    → Groq Llama 3.1 8B    (Tier-1 single-fact lookups)

On Groq 429 / quota errors, transparently retries via Ollama
(Qwen 2.5 7B Q5_K_M) running on the host. The fallback is logged so the
caller can decide whether to surface it.

Output modes
------------
* `chat()`          . plain text completion
* `chat_json()`     . request JSON, parse, validate against a Pydantic schema

Both modes accept the same messages format (a list of `{role, content}`
dicts). The wrapper is pure logic, no streaming, no tool-use APIs. Tool
selection and structured output are driven by JSON schemas, not provider-
specific function-calling features, so swapping models is cheap.

Mocking strategy
----------------
This module exposes a singleton accessor `get_llm()` that test code can
monkeypatch with a fake implementation. Every node in the agent graph
imports the LLM via `get_llm()`, never via direct provider clients.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from backend.config import get_settings
from backend.logging_config import get_logger

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str           # 'groq' or 'ollama'
    used_fallback: bool     # True if fallback path was used
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Base error raised when both primary and fallback fail."""


class LLMRateLimited(Exception):
    """Internal signal: Groq returned 429; trigger fallback to Ollama."""


# ---------------------------------------------------------------------------
# Provider clients
# ---------------------------------------------------------------------------


class GroqClient:
    """Thin async wrapper around the Groq SDK."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._client: Any = None

    def _ensure(self) -> Any:
        if self._client is None:
            try:
                from groq import AsyncGroq
            except ImportError as exc:
                raise LLMError("groq SDK not installed") from exc
            self._client = AsyncGroq(api_key=self.api_key)
        return self._client

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> tuple[str, dict[str, Any]]:
        """Returns (text, raw_response_metadata)."""
        client = self._ensure()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Groq SDK raises various exceptions for 429s; sniff status code.
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            if status == 429:
                raise LLMRateLimited(str(exc)) from exc
            raise

        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        meta = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            "model": resp.model,
        }
        return text, meta


class OllamaClient:
    """Thin async wrapper around the Ollama SDK (host-side)."""

    def __init__(self, host: str) -> None:
        self.host = host
        self._client: Any = None

    def _ensure(self) -> Any:
        if self._client is None:
            try:
                from ollama import AsyncClient
            except ImportError as exc:
                raise LLMError("ollama SDK not installed") from exc
            self._client = AsyncClient(host=self.host)
        return self._client

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> tuple[str, dict[str, Any]]:
        client = self._ensure()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            kwargs["format"] = "json"

        resp = await client.chat(**kwargs)
        # ollama-python returns a dict-like with 'message': {'content': ...}
        content = resp.get("message", {}).get("content", "")
        meta = {
            "prompt_tokens": resp.get("prompt_eval_count", 0),
            "completion_tokens": resp.get("eval_count", 0),
            "model": resp.get("model", model),
        }
        return content, meta


# ---------------------------------------------------------------------------
# Unified wrapper
# ---------------------------------------------------------------------------


class LLM:
    """Unified Groq-primary, Ollama-fallback LLM client."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._groq: GroqClient | None = None
        self._ollama: OllamaClient | None = None

    # -- lazy provider construction ---

    def _get_groq(self) -> GroqClient:
        if self._groq is None:
            self._groq = GroqClient(self.settings.groq_api_key.get_secret_value())
        return self._groq

    def _get_ollama(self) -> OllamaClient:
        if self._ollama is None:
            self._ollama = OllamaClient(self.settings.ollama_host)
        return self._ollama

    # -- model selection by role ---

    def _model_for_role(self, role: str, provider: str) -> str:
        if provider == "groq":
            if role == "cheap":
                return self.settings.groq_model_cheap
            return self.settings.groq_model_primary  # 'primary' or 'fallback'
        # ollama
        return self.settings.ollama_model

    # -- public API ---

    async def chat(
        self,
        messages: list[dict[str, str]],
        model_role: str = "primary",
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        max_retries: int = 2,
    ) -> LLMResponse:
        """Send a chat request, returning the first successful response.

        Tries Groq first (with `max_retries` exponential-backoff retries on
        429). On exhausted retries OR on `LLMError`, falls back to Ollama.
        """
        loop = asyncio.get_event_loop()
        start = loop.time()

        # --- Primary: Groq ---
        try:
            text, meta = await self._chat_with_retries(
                provider="groq",
                role=model_role,
                messages=messages,
                json_mode=json_mode,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
            return LLMResponse(
                text=text,
                model=meta.get("model", ""),
                provider="groq",
                used_fallback=False,
                input_tokens=meta.get("prompt_tokens", 0),
                output_tokens=meta.get("completion_tokens", 0),
                latency_ms=int((loop.time() - start) * 1000),
                metadata=meta,
            )
        except (LLMRateLimited, LLMError) as exc:
            log.warning("llm.groq_failed_falling_back", error=str(exc))

        # --- Fallback: Ollama (only if configured) ---
        if self.settings.llm_fallback_provider.lower() == "none":
            raise LLMError("Groq failed and fallback provider is disabled (LLM_FALLBACK_PROVIDER=none)")

        try:
            text, meta = await self._get_ollama().chat(
                model=self._model_for_role(model_role, "ollama"),
                messages=messages,
                json_mode=json_mode,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return LLMResponse(
                text=text,
                model=meta.get("model", ""),
                provider="ollama",
                used_fallback=True,
                input_tokens=meta.get("prompt_tokens", 0),
                output_tokens=meta.get("completion_tokens", 0),
                latency_ms=int((loop.time() - start) * 1000),
                metadata=meta,
            )
        except Exception as exc:
            raise LLMError(f"both providers failed: {exc}") from exc

    async def _chat_with_retries(
        self,
        provider: str,
        role: str,
        messages: list[dict[str, str]],
        json_mode: bool,
        temperature: float,
        max_tokens: int,
        max_retries: int,
    ) -> tuple[str, dict[str, Any]]:
        client = self._get_groq() if provider == "groq" else self._get_ollama()
        model = self._model_for_role(role, provider)

        backoff = 1.0
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await client.chat(
                    model=model,
                    messages=messages,
                    json_mode=json_mode,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except LLMRateLimited as exc:
                last_exc = exc
                if attempt >= max_retries:
                    break
                log.info("llm.rate_limited_retry", attempt=attempt + 1, backoff=backoff)
                await asyncio.sleep(backoff)
                backoff *= 2
        raise (last_exc or LLMError("retries exhausted"))

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: Type[T],
        model_role: str = "primary",
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> tuple[T, LLMResponse]:
        """Chat with `json_mode=True` and validate response against a Pydantic schema.

        Returns: (validated_model_instance, raw_response_metadata)

        On JSON parse failure, retries once with an explicit "you returned
        invalid JSON; emit valid JSON only" repair message.
        """
        # First attempt, direct
        resp = await self.chat(
            messages=messages,
            model_role=model_role,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        try:
            obj = schema.model_validate_json(resp.text)
            return obj, resp
        except (json.JSONDecodeError, ValidationError) as exc:
            log.warning("llm.json_parse_failed_retry", error=str(exc)[:200])

        # Second attempt, show the model its mistake
        repair_messages = list(messages) + [
            {"role": "assistant", "content": resp.text},
            {
                "role": "user",
                "content": (
                    "Your previous response was not valid JSON conforming to the "
                    "required schema. Emit ONLY a valid JSON object, no prose, "
                    "no markdown fences, no explanation."
                ),
            },
        ]
        resp2 = await self.chat(
            messages=repair_messages,
            model_role=model_role,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # If this also fails, raise, let the caller decide.
        obj = schema.model_validate_json(resp2.text)
        return obj, resp2


# ---------------------------------------------------------------------------
# Singleton accessor (testable via monkeypatch)
# ---------------------------------------------------------------------------


_llm_instance: LLM | None = None


def get_llm() -> LLM:
    """Process-wide LLM singleton. Tests should monkeypatch this with a stub."""
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLM()
    return _llm_instance


def set_llm(instance: LLM | Any) -> None:
    """Test hook: replace the singleton with a stub (e.g, a Mock)."""
    global _llm_instance
    _llm_instance = instance


def reset_llm() -> None:
    """Test hook: clear the singleton, force re-construction on next access."""
    global _llm_instance
    _llm_instance = None
