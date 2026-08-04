"""Model wiring shared by every agent.

The plan pins the model contract: a single OpenAI-compatible endpoint configured
by the user (base_url + model name + prices). The API key lives in the OS
keychain and is passed in at call time — never stored in the project file or the
engine's settings. Each agent builds its pydantic-ai `Agent` from this one
factory so model wiring is decided in exactly one place, and both the analyser
and the translator share it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic_ai import AgentRunResult
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from adib_engine.models.project import ProviderSettings


def build_model(provider: ProviderSettings, api_key: str | None = None) -> OpenAIChatModel:
    """An OpenAI-compatible chat model bound to the configured endpoint.

    `api_key` is resolved by the caller from the OS keychain; an OpenAI-compatible
    server with no key (e.g. a local one) passes None.
    """
    key = api_key or None
    if key is None:
        key = ""
    return OpenAIChatModel(
        provider.model,
        provider=OpenAIProvider(base_url=provider.base_url, api_key=key),
    )


def model_settings(provider: ProviderSettings) -> ModelSettings:
    """Per-call sampling settings, kept in one place so agents stay identical."""
    return ModelSettings(
        temperature=provider.temperature,
        timeout=provider.request_timeout_s,
    )


def cost_for(provider: ProviderSettings, prompt_tokens: int, completion_tokens: int) -> float:
    """Dollar cost of a call under the provider's per-million-token prices."""
    return (
        prompt_tokens / 1_000_000 * provider.price_per_mtok_in
        + completion_tokens / 1_000_000 * provider.price_per_mtok_out
    )


class AgentResult:
    """Structured output + usage + timing from one agent run."""

    __slots__ = ("output", "prompt_tokens", "completion_tokens", "requests", "completed_at")

    def __init__(
        self,
        *,
        output: object,
        prompt_tokens: int,
        completion_tokens: int,
        requests: int,
    ) -> None:
        self.output = output
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.requests = requests
        self.completed_at = datetime.now(UTC)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def result_from_run(run: AgentRunResult) -> AgentResult:
    """Adapt a pydantic-ai run result into our own `AgentResult`.

    `run.usage` is a *field* in pydantic-ai 2.23 (a `RunUsage`), not a method —
    this is the single place that assumption lives, so a pydantic-ai upgrade
    only touches here.
    """
    usage = run.usage
    return AgentResult(
        output=run.output,
        prompt_tokens=usage.input_tokens,
        completion_tokens=usage.output_tokens,
        requests=usage.requests,
    )


AsyncFn = Callable[[str], Awaitable]


async def run_with_retries(
    fn: Callable[[], Awaitable[AgentResult]],
    *,
    retries: int,
    base_delay_s: float = 1.0,
) -> AgentResult:
    """Run `fn`, retrying on the retryable model errors with exponential backoff.

    pydantic-ai raises `ModelRetry` from inside a tool when the model asks for a
    retry, but HTTP 429/5xx surfaces as a runtime exception from the provider.
    We catch the broad retryable family and back off between attempts.
    """
    delay = base_delay_s
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: PERF203 - genuine retry loop
            last_exc = exc
            if attempt >= retries:
                break
            await asyncio.sleep(delay)
            delay *= 2.0
    assert last_exc is not None
    raise last_exc
