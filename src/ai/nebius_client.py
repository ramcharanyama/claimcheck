"""Nebius Token Factory client for ClaimCheck 2.0.

Token Factory exposes an OpenAI-compatible API, so we use the openai package
with Nebius's base URL. All NVIDIA Nemotron calls go through this module.
"""

from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

from src import settings

DEFAULT_BASE_URL = settings.DEFAULT_NEBIUS_BASE_URL
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class NebiusConfigError(Exception):
    """Raised when the API key or model ID is missing from the environment."""


class NebiusResponseError(Exception):
    """Raised when the model's reply cannot be parsed as JSON."""


def get_client(api_key: str | None = None, base_url: str | None = None) -> OpenAI:
    key = api_key or settings.get_nebius_api_key()
    if not key:
        raise NebiusConfigError("NEBIUS_API_KEY is not set. Add it to your .env file.")
    return OpenAI(api_key=key, base_url=base_url or settings.get_nebius_base_url())


def get_model(kind: str = "reasoning") -> str:
    var = "NEBIUS_MODEL_REASONING" if kind == "reasoning" else "NEBIUS_MODEL_FAST"
    model = settings.get_nebius_model_reasoning() if kind == "reasoning" else settings.get_nebius_model_fast()
    if not model:
        raise NebiusConfigError(f"{var} is not set. Copy a Nemotron model ID from Token Factory into .env.")
    return model


def parse_json_response(content: str | None) -> dict:
    """Pull the JSON object out of a reply, ignoring <think> blocks and code fences."""
    if not content:
        raise NebiusResponseError("Model returned an empty response.")
    text = _THINK_BLOCK.sub("", content)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise NebiusResponseError(f"No JSON object found in model response: {text[:200]!r}")
    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        repaired = _repair_json(candidate)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            raise NebiusResponseError(f"Model returned invalid JSON: {exc}") from exc


def _repair_json(text: str) -> str:
    """Fix the small slips models make in long JSON replies (only used after a parse error):
    trailing commas, and a missing comma between two values on separate lines."""
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # value end ("..." / number / true / false / null / } / ]) followed by a new line that starts a new item
    text = re.sub(r'(["}\]\d]|true|false|null)(\s*\n\s*)(["{\[])', r"\1,\2\3", text)
    return text


def chat_json(client, model: str, system: str, user: str,
              temperature: float = 0.2, max_tokens: int = 8192,
              retry_max_tokens: int = 16384) -> dict:
    """Call the model and parse its JSON answer.

    Nemotron 3 models reason before answering. If the reasoning uses up the output
    budget, the answer comes back empty with finish_reason 'length'; in that case we
    retry once with a larger budget before giving up with a clear error.
    """
    budget = max_tokens
    attempts = 0
    while True:
        attempts += 1
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=budget,
        )
        choice = response.choices[0]
        content = choice.message.content
        finish = getattr(choice, "finish_reason", None)

        if content and content.strip():
            try:
                return parse_json_response(content)
            except NebiusResponseError:
                if attempts >= 2:
                    raise
                temperature = 0.0  # ask once more, as deterministically as possible
                continue
        if finish == "length" and budget < retry_max_tokens:
            budget = retry_max_tokens
            continue
        raise NebiusResponseError(
            f"Model returned an empty response (finish_reason={finish!r}, max_tokens={budget}). "
            "If finish_reason is 'length', the model ran out of output tokens while reasoning."
        )
