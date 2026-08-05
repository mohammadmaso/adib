"""Cover agent tests: prompt content, response decoding, and retry-on-failure —
against a stubbed httpx transport, never a live endpoint.

The cover agent talks to `/chat/completions` with `modalities: ["image",
"text"]` (the shape aggregators like OpenRouter actually serve image-editing
models through), not OpenAI's dedicated images API — see `agents/cover.py`'s
module docstring for why.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from adib_engine.agents.cover import build_cover_prompt, translate_cover
from adib_engine.models.project import ImageProviderSettings

_B64_IMAGE = "aGVsbG8="  # "hello"


def _chat_completion_body(b64: str | None) -> dict:
    images = []
    if b64:
        images = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]
    return {"choices": [{"message": {"role": "assistant", "images": images}}]}


def _client_with(responses: list[httpx.Response]) -> tuple[httpx.AsyncClient, list[dict]]:
    calls: list[dict] = []
    responses = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return responses.pop(0)

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://example.test/api"), calls


def test_prompt_names_the_target_language_tag():
    prompt = build_cover_prompt("fa")
    assert "fa" in prompt
    assert "translat" in prompt.lower()


async def test_translate_cover_decodes_the_returned_image():
    client, calls = _client_with([httpx.Response(200, json=_chat_completion_body(_B64_IMAGE))])
    provider = ImageProviderSettings(model="my-image-model", price_per_image_usd=0.02)

    result = await translate_cover(
        b"source-bytes", "image/png", target_lang="fa", provider=provider, client=client
    )

    assert result.image_bytes == base64.b64decode(_B64_IMAGE)
    assert result.cost_usd == 0.02
    assert calls[0]["model"] == "my-image-model"
    assert calls[0]["modalities"] == ["image", "text"]
    content = calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_translate_cover_retries_then_succeeds():
    client, calls = _client_with(
        [
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(200, json=_chat_completion_body(_B64_IMAGE)),
        ]
    )
    provider = ImageProviderSettings(max_retries=3)

    result = await translate_cover(
        b"source-bytes", "image/png", target_lang="es", provider=provider, client=client
    )

    assert result.image_bytes == base64.b64decode(_B64_IMAGE)
    assert len(calls) == 3


async def test_translate_cover_gives_up_after_max_retries():
    client, calls = _client_with([httpx.Response(500, json={"error": "boom"})] * 10)
    provider = ImageProviderSettings(max_retries=1)

    with pytest.raises(httpx.HTTPStatusError):
        await translate_cover(
            b"source-bytes", "image/png", target_lang="es", provider=provider, client=client
        )
    assert len(calls) == 2  # initial attempt + 1 retry


async def test_translate_cover_raises_on_empty_image_data():
    client, _ = _client_with([httpx.Response(200, json=_chat_completion_body(None))])
    provider = ImageProviderSettings(max_retries=0)

    with pytest.raises(RuntimeError, match="no image data"):
        await translate_cover(
            b"source-bytes", "image/png", target_lang="es", provider=provider, client=client
        )


async def test_translate_cover_sends_the_configured_api_key():
    client, calls = _client_with([httpx.Response(200, json=_chat_completion_body(_B64_IMAGE))])
    provider = ImageProviderSettings()
    seen_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        return httpx.Response(200, json=_chat_completion_body(_B64_IMAGE))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")

    await translate_cover(
        b"x", "image/png", target_lang="en", provider=provider, api_key="sk-test", client=client
    )
    assert seen_auth == ["Bearer sk-test"]
