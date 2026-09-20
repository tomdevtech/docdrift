"""OpenAI-compatible client for local LLM servers.

Both Ollama (default: http://localhost:11434/v1) and LM Studio
(default: http://localhost:1234/v1) provide a /v1/chat/completions API, so a
single client configured through api_base_url is sufficient.
"""

from dataclasses import dataclass

import requests


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMClient:
    """Small wrapper around /v1/chat/completions."""

    def __init__(self, base_url: str, model: str, timeout: int = 300) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[ChatMessage], temperature: float = 0.2) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }

        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"Could not reach a local LLM server at {self.base_url}. "
                "Is Ollama/LM Studio running, and is the model loaded?"
            ) from exc

        data = response.json()
        return data["choices"][0]["message"]["content"]
