"""Tests for docdrift.llm_client (HTTP mocked)."""

import pytest
import requests

from docdrift import llm_client
from docdrift.llm_client import ChatMessage, LLMClient, clean_reply


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text or str(data)

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def _ok(content, finish="stop"):
    return _Resp(data={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


@pytest.fixture
def post(monkeypatch):
    calls = []

    def install(*responses):
        it = iter(responses)

        def fake(url, json, timeout):
            calls.append(json)
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr(llm_client.requests, "post", fake)
        monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
        return calls

    return install


def _chat():
    return LLMClient("http://x/v1", "m").chat([ChatMessage("user", "hi")])


def test_think_blocks_are_removed():
    assert clean_reply("<think>hmm\nlong</think>\n## Purpose\nX") == "## Purpose\nX"
    assert clean_reply("<think>never finished") == ""


def test_normal_reply(post):
    post(_ok("## Purpose\nok"))
    assert _chat() == "## Purpose\nok"


def test_empty_reply_is_an_error(post):
    post(_ok("<think>only thinking</think>"))
    with pytest.raises(RuntimeError, match="empty"):
        _chat()


def test_truncated_reply_is_an_error(post):
    post(_ok("## Purpose\nhalf a sen", finish="length"))
    with pytest.raises(RuntimeError, match="cut off"):
        _chat()


def test_server_error_message_is_shown_with_context_hint(post):
    post(_Resp(400, text='{"error":"context length exceeded: 9000 > 4096 tokens"}'))
    with pytest.raises(RuntimeError, match="400.*context length exceeded.*max_prompt_chars"):
        _chat()


def test_unexpected_format_is_reported(post):
    post(_Resp(200, data={"error": "model not loaded"}))
    with pytest.raises(RuntimeError, match="Unexpected answer"):
        _chat()


def test_transient_errors_are_retried_once(post):
    calls = post(requests.exceptions.ConnectionError(), _ok("fine"))
    assert _chat() == "fine" and len(calls) == 2
    calls = post(_Resp(503, text="loading"), _ok("fine"))
    assert _chat() == "fine"


def test_persistent_connection_error_is_explained(post):
    post(requests.exceptions.ConnectionError(), requests.exceptions.ConnectionError())
    with pytest.raises(RuntimeError, match="Is Ollama/LM Studio running"):
        _chat()
