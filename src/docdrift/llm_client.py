"""Client for local OpenAI-compatible LLM servers.

Ollama (default ``http://localhost:11434/v1``) and LM Studio (default
``http://localhost:1234/v1``) both expose ``/v1/chat/completions``, so a single
client configured through ``api_base_url`` covers both.

Replies are cleaned before they are returned: reasoning blocks of "thinking"
models (``<think>...</think>``) are removed, and an empty reply is an error -
it must never overwrite a good note.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests

_THINK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>\s*", re.DOTALL | re.IGNORECASE)
_RETRY_STATUS = {502, 503, 504}


class LLMUnavailable(RuntimeError):
    """The server cannot be reached at all - retrying other files is pointless."""


@dataclass
class ChatMessage:
    """One message of a chat request."""

    role: str  # "system" | "user" | "assistant"
    content: str


def clean_reply(text: str) -> str:
    """Remove reasoning blocks; an unterminated ``<think>`` drops everything up to its end."""
    text = _THINK.sub("", text or "")
    if re.match(r"\s*<think>", text, re.IGNORECASE):  # cut off mid-thought
        return ""
    return text.strip()


class LLMClient:
    """Small wrapper around ``POST /v1/chat/completions``."""

    def __init__(self, base_url: str, model: str, timeout: int = 600, retries: int = 1) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retries = retries

    def _post(self, payload: dict) -> requests.Response:
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.retries + 1):
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
            except requests.exceptions.ConnectionError as exc:
                if attempt < self.retries:
                    time.sleep(2)
                    continue
                raise LLMUnavailable(
                    f"Could not reach a local LLM server at {self.base_url}. "
                    "Is Ollama/LM Studio running, and is the model loaded?"
                ) from exc
            except requests.exceptions.Timeout as exc:
                raise RuntimeError(
                    f"The LLM did not answer within {self.timeout}s "
                    "(model too slow or prompt too large)."
                ) from exc
            if response.status_code in _RETRY_STATUS and attempt < self.retries:
                time.sleep(2)
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def chat(self, messages: list[ChatMessage], temperature: float = 0.2) -> str:
        """Send ``messages`` and return the cleaned reply text.

        Raises:
            RuntimeError: server unreachable, timeout, an error status (the server's
                own message is included - e.g. "context length exceeded"), an
                unexpected response format, or an empty reply.
        """
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        response = self._post(payload)
        if response.status_code >= 400:
            detail = " ".join(response.text.split())[:300]
            hint = (
                " - the prompt is probably larger than the model's context: raise the "
                "context length or lower max_prompt_chars"
                if re.search(r"context|token|too long|exceed", detail, re.IGNORECASE)
                else ""
            )
            raise RuntimeError(f"LLM server error {response.status_code}: {detail}{hint}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Unexpected answer from {self.base_url}: {response.text[:300]!r}"
            ) from exc
        if data["choices"][0].get("finish_reason") == "length":
            raise RuntimeError(
                "The reply was cut off (finish_reason=length) - the model's context is too "
                "small for prompt + answer: raise the context length or lower max_prompt_chars."
            )
        reply = clean_reply(content)
        if not reply:
            raise RuntimeError("The LLM returned an empty reply.")
        return reply
