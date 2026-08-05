"""Cover translation: one multimodal chat-completions call that redraws a book
cover with its text in the target language.

Image-editing models are overwhelmingly served through the *chat completions*
endpoint rather than OpenAI's own dedicated `/images/edits` — that's how
aggregators (OpenRouter and others) expose them: an image in the message
content, `modalities: ["image", "text"]` to ask for image output, and the
generated image comes back on the response message rather than as a separate
images-API payload. The `openai` SDK's typed images API doesn't model this
shape at all, so this module talks to `{base_url}/chat/completions` directly
via `httpx`, matching the same "OpenAI-compatible endpoint" contract
`ProviderSettings` already uses for text.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass

import httpx

from adib_engine.models.project import ImageProviderSettings


@dataclass
class CoverResult:
    image_bytes: bytes
    cost_usd: float


def build_cover_prompt(target_lang: str) -> str:
    return (
        "This image is the cover of a book. Redraw it, translating every piece "
        f"of visible text into the language with BCP-47 tag '{target_lang}'. "
        "Preserve the exact composition, layout, art style, colors, and "
        "typography style — change only the language of the text."
    )


def _build_payload(image_bytes: bytes, mime: str, *, target_lang: str, model: str) -> dict:
    b64_in = base64.b64encode(image_bytes).decode()
    return {
        "model": model,
        # Requests image output alongside/instead of text — without this, an
        # image-editing model on a chat-completions endpoint just answers in
        # prose describing the cover instead of returning a new one.
        "modalities": ["image", "text"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_cover_prompt(target_lang)},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64_in}"},
                    },
                ],
            }
        ],
    }


def _extract_image_b64(body: dict) -> str:
    """Pull the generated image's base64 payload out of a chat-completion
    response. Providers return it as a data URL on `message.images[0]`
    (OpenRouter's convention, also followed by other aggregators) rather than
    anywhere the `openai` SDK's typed response models know about."""
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("image provider returned no choices")
    message = choices[0].get("message") or {}
    images = message.get("images") or []
    if not images:
        raise RuntimeError("image provider returned no image data")
    data_url = images[0].get("image_url", {}).get("url", "")
    _, _, b64_out = data_url.partition(",")
    if not b64_out:
        raise RuntimeError("image provider returned an empty image payload")
    return b64_out


async def translate_cover(
    image_bytes: bytes,
    mime: str,
    *,
    target_lang: str,
    provider: ImageProviderSettings,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> CoverResult:
    """Redraw `image_bytes` with its text translated into `target_lang`.

    `client` lets tests inject a transport stub; production builds a real
    `httpx.AsyncClient` bound to the configured endpoint.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(
        base_url=provider.base_url, timeout=provider.request_timeout_s
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = _build_payload(image_bytes, mime, target_lang=target_lang, model=provider.model)

    async def call() -> CoverResult:
        response = await http.post("/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        b64_out = _extract_image_b64(response.json())
        return CoverResult(
            image_bytes=base64.b64decode(b64_out), cost_usd=provider.price_per_image_usd
        )

    try:
        return await _run_with_retries(call, retries=provider.max_retries)
    finally:
        if owns_client:
            await http.aclose()


async def _run_with_retries(
    fn,
    *,
    retries: int,
    base_delay_s: float = 1.0,
) -> CoverResult:
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


__all__ = ["CoverResult", "build_cover_prompt", "translate_cover"]
