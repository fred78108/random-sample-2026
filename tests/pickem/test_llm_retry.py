"""Milestone F: retry/backoff around cloud-routed Ollama calls (DESIGN §7). A backtest sweep
against a `:cloud` model can run for hours against a rate-limited remote service, so a
throttled (429) or transient (5xx/connection) response must be retried rather than aborting
the whole sweep; a non-retryable error (e.g. a bad request) must still raise immediately."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import ollama
import pytest

from random_sample.pickem import llm


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", MagicMock())


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def test_retries_on_429_then_succeeds():
    structured_llm = MagicMock()
    structured_llm.invoke.side_effect = [ollama.ResponseError("throttled", status_code=429), "ok"]

    result = llm._invoke_with_retry(structured_llm, "prompt")

    assert result == "ok"
    assert structured_llm.invoke.call_count == 2


def test_retries_on_5xx_then_succeeds():
    structured_llm = MagicMock()
    structured_llm.invoke.side_effect = [_http_status_error(503), "ok"]

    result = llm._invoke_with_retry(structured_llm, "prompt")

    assert result == "ok"


def test_retries_on_connection_error_then_succeeds():
    structured_llm = MagicMock()
    structured_llm.invoke.side_effect = [httpx.ConnectError("connection refused"), "ok"]

    result = llm._invoke_with_retry(structured_llm, "prompt")

    assert result == "ok"


def test_non_retryable_error_raises_immediately():
    structured_llm = MagicMock()
    structured_llm.invoke.side_effect = ollama.ResponseError("bad request", status_code=400)

    with pytest.raises(ollama.ResponseError):
        llm._invoke_with_retry(structured_llm, "prompt")

    assert structured_llm.invoke.call_count == 1


def test_gives_up_after_max_retries():
    structured_llm = MagicMock()
    structured_llm.invoke.side_effect = ollama.ResponseError("throttled", status_code=429)

    with pytest.raises(ollama.ResponseError):
        llm._invoke_with_retry(structured_llm, "prompt")

    assert structured_llm.invoke.call_count == llm.MAX_LLM_RETRIES + 1


def test_backoff_grows_and_sleeps_between_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(llm.random, "random", lambda: 0.0)  # pin jitter for a deterministic check
    structured_llm = MagicMock()
    structured_llm.invoke.side_effect = [
        ollama.ResponseError("throttled", status_code=429),
        ollama.ResponseError("throttled", status_code=429),
        "ok",
    ]

    llm._invoke_with_retry(structured_llm, "prompt")

    assert sleeps == [llm.BASE_BACKOFF_SECONDS, llm.BASE_BACKOFF_SECONDS * 2]
