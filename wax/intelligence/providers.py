"""
Provider-agnostic intelligence abstraction.

Primary + fallback. Retries. Error classification.
No cost accounting. No token budgets that reject learners.
Every meaningful learner message goes through intelligence.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Process-local cooldown after 429 so concurrent work items do not stampede
import time as _time
_provider_cooldown_until: dict[str, float] = {}


def _cooldown_key(*, name: str, base_url: str = "", model: str = "") -> str:
    # Prefer host endpoint — same Groq base URL shares one rate budget across models/roles
    host = (base_url or "").strip().rstrip("/").lower()
    if host:
        return f"host:{host}"
    return f"name:{(name or '').lower()}:{(model or '').lower()}"


async def _respect_provider_cooldown(name: str, model: str, base_url: str = "") -> None:
    import asyncio

    key = _cooldown_key(name=name, base_url=base_url, model=model)
    until = _provider_cooldown_until.get(key, 0.0)
    now = _time.monotonic()
    if until > now:
        delay = min(until - now, 90.0)
        logger.warning(
            "provider_cooldown_wait",
            provider=name,
            model=model,
            base_url=(base_url or "")[:80],
            wait_seconds=round(delay, 2),
        )
        await asyncio.sleep(delay)


def _arm_provider_cooldown(
    name: str, model: str, seconds: float, base_url: str = ""
) -> None:
    key = _cooldown_key(name=name, base_url=base_url, model=model)
    # Floor 10s so concurrent jobs do not re-stampede immediately
    until = _time.monotonic() + max(10.0, min(float(seconds), 120.0))
    prev = _provider_cooldown_until.get(key, 0.0)
    if until > prev:
        _provider_cooldown_until[key] = until


def provider_cooldown_remaining(name: str, base_url: str = "", model: str = "") -> float:
    key = _cooldown_key(name=name, base_url=base_url, model=model)
    return max(0.0, _provider_cooldown_until.get(key, 0.0) - _time.monotonic())


def any_provider_cooling_down(min_seconds: float = 1.0) -> bool:
    now = _time.monotonic()
    return any(v - now >= min_seconds for v in _provider_cooldown_until.values())


class ProviderErrorClass(str, Enum):
    TIMEOUT = "provider_timeout"
    UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "provider_rate_limited"
    AUTH_FAILURE = "provider_authentication_failure"
    INVALID_REQUEST = "provider_invalid_request"
    MALFORMED_RESPONSE = "provider_malformed_response"
    UNKNOWN = "provider_unknown"


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        error_class: ProviderErrorClass,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message)
        self.error_class = error_class
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass
class ChatMessage:
    """Text-only chat turn. No tool/function-calling roles."""

    role: str  # system | user | assistant
    content: str
    name: str | None = None


@dataclass
class CompletionRequest:
    """Text completion only — no tools catalogue; max_tokens is optional resource bound, not an intelligence budget."""

    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionResponse:
    content: str | None
    finish_reason: str | None = None
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None


class IntelligenceProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        ...


def _serialize_openai_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """OpenAI-compatible message list — text roles only."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.role if m.role in ("system", "user", "assistant") else "user"
        msg: dict[str, Any] = {
            "role": role,
            "content": m.content if m.content is not None else "",
        }
        if m.name and role == "user":
            msg["name"] = m.name
        out.append(msg)
    return out


class OpenAICompatibleProvider(IntelligenceProvider):
    """OpenAI, Grok (xAI), OpenRouter, and any OpenAI-compatible endpoint."""

    def __init__(
        self,
        name: str,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
    ):
        self.name = name
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _serialize_openai_messages(request.messages),
            "temperature": request.temperature if request.temperature is not None else 0.7,
        }
        # max_tokens only when the caller sets it. Default is provider/model natural limit —
        # not an application intelligence budget. When set, treat as infrastructure resource bound.
        if request.max_tokens is not None:
            body["max_tokens"] = int(request.max_tokens)
        # Ensure body is JSON-serializable (Upstage rejects malformed bodies)
        try:
            import json as _json
            body = _json.loads(_json.dumps(body, ensure_ascii=False, default=str))
        except Exception:
            pass

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await _respect_provider_cooldown(self.name, self.model, self.base_url)
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
        except httpx.TimeoutException as e:
            raise ProviderError(str(e), ProviderErrorClass.TIMEOUT, retryable=True) from e
        except httpx.ConnectError as e:
            raise ProviderError(str(e), ProviderErrorClass.UNAVAILABLE, retryable=True) from e

        if resp.status_code == 429:
            ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
            try:
                wait_s = float(ra) if ra else 8.0
            except ValueError:
                wait_s = 8.0
            wait_s = max(5.0, min(wait_s, 60.0))
            _arm_provider_cooldown(self.name, self.model, wait_s, self.base_url)
            logger.warning(
                "provider_rate_limited",
                provider=self.name,
                model=self.model,
                retry_after=wait_s,
            )
            raise ProviderError(
                f"Rate limited (retry_after={wait_s}s)",
                ProviderErrorClass.RATE_LIMITED,
                retryable=True,
                retry_after_seconds=wait_s,
            )
        if resp.status_code in (401, 403):
            body_preview = (resp.text or "")[:200].replace("\n", " ")
            logger.warning(
                "provider_auth_failure",
                provider=self.name,
                status_code=resp.status_code,
                base_url=self.base_url,
                model=self.model,
                body_preview=body_preview,
                api_key_configured=bool(self.api_key),
            )
            raise ProviderError(
                f"Auth failure status={resp.status_code}",
                ProviderErrorClass.AUTH_FAILURE,
                retryable=False,
            )
        if resp.status_code >= 500:
            raise ProviderError(
                f"Server error {resp.status_code}", ProviderErrorClass.UNAVAILABLE, retryable=True
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Invalid request: {resp.text[:500]}",
                ProviderErrorClass.INVALID_REQUEST,
                retryable=False,
            )

        data = resp.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
            # Text-first path. Tool-call payloads are ignored by the agent runtime
            # (directives are free-form text, not provider function calling).
            content = message.get("content")
            if isinstance(content, list):
                # Some providers return content parts
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(p.get("text") or "")
                    elif isinstance(p, str):
                        parts.append(p)
                content = "".join(parts)
            return CompletionResponse(
                content=content if isinstance(content, str) else (str(content) if content else None),
                finish_reason=choice.get("finish_reason"),
                model=data.get("model"),
                raw=data,
                provider=self.name,
            )
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(
                f"Malformed response: {e}", ProviderErrorClass.MALFORMED_RESPONSE, retryable=False
            ) from e


class AnthropicProvider(IntelligenceProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 120.0,
    ):
        self.name = "anthropic"
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        system = None
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "system":
                system = (system + "\n" + m.content) if system else m.content
            else:
                messages.append(
                    {
                        "role": m.role if m.role in ("user", "assistant") else "user",
                        "content": m.content or "",
                    }
                )

        # Anthropic API requires max_tokens. Use caller value or a high resource safety floor
        # (not an application intelligence budget on how much the model may think).
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages or [{"role": "user", "content": "Hello"}],
            "max_tokens": int(request.max_tokens) if request.max_tokens is not None else 8192,
            "temperature": request.temperature if request.temperature is not None else 0.7,
        }
        if system:
            body["system"] = system

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await _respect_provider_cooldown(self.name, self.model, self.base_url)
                resp = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers=headers,
                    json=body,
                )
        except httpx.TimeoutException as e:
            raise ProviderError(str(e), ProviderErrorClass.TIMEOUT, retryable=True) from e
        except httpx.ConnectError as e:
            raise ProviderError(str(e), ProviderErrorClass.UNAVAILABLE, retryable=True) from e

        if resp.status_code == 429:
            ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
            try:
                wait_s = float(ra) if ra else 15.0
            except ValueError:
                wait_s = 15.0
            wait_s = max(10.0, min(wait_s, 120.0))
            _arm_provider_cooldown(self.name, self.model, wait_s, self.base_url)
            raise ProviderError(
                f"Rate limited (retry_after={wait_s}s)",
                ProviderErrorClass.RATE_LIMITED,
                retryable=True,
                retry_after_seconds=wait_s,
            )
        if resp.status_code in (401, 403):
            body_preview = (resp.text or "")[:200].replace("\n", " ")
            logger.warning(
                "provider_auth_failure",
                provider=self.name,
                status_code=resp.status_code,
                base_url=self.base_url,
                model=self.model,
                body_preview=body_preview,
                api_key_configured=bool(self.api_key),
            )
            raise ProviderError(
                f"Auth failure status={resp.status_code}",
                ProviderErrorClass.AUTH_FAILURE,
                retryable=False,
            )
        if resp.status_code >= 500:
            raise ProviderError(
                f"Server error {resp.status_code}", ProviderErrorClass.UNAVAILABLE, retryable=True
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Invalid request: {resp.text[:500]}",
                ProviderErrorClass.INVALID_REQUEST,
                retryable=False,
            )

        data = resp.json()
        try:
            content_blocks = data.get("content", [])
            text_out = "".join(
                block.get("text", "") for block in content_blocks if block.get("type") == "text"
            )
            return CompletionResponse(
                content=text_out or None,
                finish_reason=data.get("stop_reason"),
                model=data.get("model"),
                raw=data,
                provider=self.name,
            )
        except (KeyError, TypeError) as e:
            raise ProviderError(
                f"Malformed response: {e}", ProviderErrorClass.MALFORMED_RESPONSE, retryable=False
            ) from e


def _build_provider(
    provider_name: str,
    api_key: str,
    model: str,
    base_url: str = "",
    timeout: float = 120.0,
) -> IntelligenceProvider | None:
    """
    Instantiate a chat provider from configuration.

    Provider *name* is an identifier for logging/config only.
    Any name with an API key + model uses the OpenAI-compatible client
    (/chat/completions) unless the name is a known non-compatible native API.

    Optional base_url defaults exist only as convenience when the operator
    omits PRIMARY_BASE_URL for a few well-known hosts — they are not a
    closed allowlist. Unknown names work when BASE_URL is set.
    """
    name = (provider_name or "").strip().lower()
    key = (api_key or "").strip()
    model_id = (model or "").strip()
    url = (base_url or "").strip()

    if not key or name in ("none", "", "null"):
        return None
    if not model_id:
        logger.warning(
            "provider_missing_model",
            provider=name or "(empty)",
            hint="Set PRIMARY_MODEL / FALLBACK_MODEL / matching *_MODEL",
        )
        return None

    # Native protocol (Messages API) — not OpenAI-compatible
    if name == "anthropic":
        return AnthropicProvider(
            api_key=key,
            model=model_id,
            base_url=url or "https://api.anthropic.com",
            timeout=timeout,
        )

    # Optional convenience defaults when BASE_URL is omitted (not an allowlist)
    convenience_base = {
        "openai": "https://api.openai.com/v1",
        "grok": "https://api.x.ai/v1",
        "xai": "https://api.x.ai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "groq": "https://api.groq.com/openai/v1",
        "together": "https://api.together.xyz/v1",
        "fireworks": "https://api.fireworks.ai/inference/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "mistral": "https://api.mistral.ai/v1",
    }
    resolved = url or convenience_base.get(name, "")
    if not resolved:
        logger.warning(
            "provider_missing_base_url",
            provider=name,
            hint=(
                "Set PRIMARY_BASE_URL (or FALLBACK_BASE_URL) to the OpenAI-compatible "
                "API root, e.g. https://api.groq.com/openai/v1"
            ),
        )
        return None

    return OpenAICompatibleProvider(
        name=name or "openai_compatible",
        api_key=key,
        model=model_id,
        base_url=resolved,
        timeout=timeout,
    )


class IntelligenceService:
    """
    High-level intelligence with primary + fallback.
    No cost gates. Every learner message is eligible for intelligence.
    """

    def __init__(self) -> None:
        self.primary = _build_provider(
            settings.primary_provider,
            settings.primary_api_key,
            settings.primary_model,
            settings.primary_base_url,
            settings.primary_timeout_seconds,
        )
        self.fallback = _build_provider(
            settings.fallback_provider,
            settings.fallback_api_key,
            settings.fallback_model,
            settings.fallback_base_url,
            settings.fallback_timeout_seconds,
        )
        # Smaller model for memory operations
        mem_key = settings.memory_api_key or settings.primary_api_key
        mem_provider = settings.memory_provider if settings.memory_provider != "none" else settings.primary_provider
        self.memory_model = _build_provider(
            mem_provider,
            mem_key,
            settings.memory_model,
            getattr(settings, "memory_base_url", None) or settings.primary_base_url,
            60.0,
        )
        self.context_model = None  # CI removed; AI uses primary only

    async def complete(
        self,
        request: CompletionRequest,
        *,
        allow_fallback: bool = True,
        use_memory_model: bool = False,
        role: str = "primary",
    ) -> CompletionResponse:
        """
        role:
          - primary: main tutor path
          - memory: memory extraction model
          - context: Context Intelligence only (independent config)
        """
        providers: list[IntelligenceProvider] = []
        if role == "context":
            if self.context_model:
                providers.append(self.context_model)
            # Context role never silently chains to primary unless context_model IS primary
            # CI removed
        elif use_memory_model and self.memory_model:
            providers.append(self.memory_model)
            # allow_fallback=False means memory model only — no silent primary/fallback chain
            if allow_fallback:
                if self.primary and self.primary is not self.memory_model:
                    providers.append(self.primary)
                if self.fallback and self.fallback not in providers:
                    providers.append(self.fallback)
        else:
            if self.primary:
                providers.append(self.primary)
            if allow_fallback and self.fallback and self.fallback is not self.primary:
                providers.append(self.fallback)

        if not providers:
            raise ProviderError(
                "No intelligence providers configured for role="
                + role
                + ". Set the matching API key / model.",
                ProviderErrorClass.AUTH_FAILURE,
                retryable=False,
            )

        last_error: Exception | None = None
        for provider in providers:
            try:
                if use_memory_model and provider is self.memory_model:
                    retries = getattr(settings, "memory_max_retries", 1)
                elif provider is self.fallback:
                    retries = getattr(settings, "fallback_max_retries", 2)
                else:
                    retries = getattr(settings, "primary_max_retries", 2)
                return await self._with_retry(provider, request, retries=retries)
            except ProviderError as e:
                last_error = e
                logger.warning(
                    "provider_failed",
                    provider=provider.name,
                    error_class=e.error_class.value,
                    retryable=e.retryable,
                    retry_after=getattr(e, "retry_after_seconds", None),
                )
                # Rate limited: do not burn through the whole provider list immediately
                if e.error_class == ProviderErrorClass.RATE_LIMITED:
                    ra = float(getattr(e, "retry_after_seconds", None) or 15.0)
                    base = getattr(provider, "base_url", "") or ""
                    _arm_provider_cooldown(provider.name, getattr(provider, "model", "") or "", ra, base)
                    # Only try a different host for fallback; same host shares budget
                    if allow_fallback and provider is not providers[-1]:
                        next_p = providers[providers.index(provider) + 1] if provider in providers else None
                        next_base = getattr(next_p, "base_url", "") or "" if next_p else ""
                        if next_p and next_base.rstrip("/") == base.rstrip("/") and base:
                            raise  # same quota pool — fail fast to caller
                    if not allow_fallback:
                        raise
                if not e.retryable and provider is providers[-1]:
                    raise
                continue
        raise last_error or ProviderError(
            "All providers failed", ProviderErrorClass.UNAVAILABLE, retryable=False
        )

    async def _with_retry(
        self, provider: IntelligenceProvider, request: CompletionRequest, retries: int | None = None
    ) -> CompletionResponse:
        def _should_retry(exc: BaseException) -> bool:
            # Only retry ProviderError instances that are explicitly retryable
            # (429, 5xx, timeout). Auth / invalid request / malformed must not loop.
            return isinstance(exc, ProviderError) and bool(getattr(exc, "retryable", False))

        @retry(
            retry=retry_if_exception(_should_retry),
            stop=stop_after_attempt(
                max(1, retries if retries is not None else getattr(settings, "primary_max_retries", 2))
            ),
            wait=wait_exponential_jitter(initial=2, max=60),
            reraise=True,
        )
        async def _inner() -> CompletionResponse:
            return await provider.complete(request)

        return await _inner()


_intelligence: IntelligenceService | None = None


def get_intelligence() -> IntelligenceService:
    global _intelligence
    if _intelligence is None:
        _intelligence = IntelligenceService()
    return _intelligence
