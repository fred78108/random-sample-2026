"""Milestone F: retry/backoff around cloud-routed Ollama calls (DESIGN §7). A backtest sweep
against a `:cloud` model can run for hours against a rate-limited remote service, so a
throttled (429) or transient (5xx/connection) response must be retried rather than aborting
the whole sweep; a non-retryable error (e.g. a bad request) must still raise immediately."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import ollama
import pydantic
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


def test_retries_on_none_result_then_succeeds():
    """`with_structured_output` can return `None` (a parse failure) instead of raising --
    hit for real during the Milestone F 2025 backtest against glm-5.3-flash:cloud, where it
    crashed 3 grid cells with `AttributeError: 'NoneType' object has no attribute
    'model_dump'` because nothing was retrying it."""
    structured_llm = MagicMock()
    structured_llm.invoke.side_effect = [None, "ok"]

    result = llm._invoke_with_retry(structured_llm, "prompt")

    assert result == "ok"
    assert structured_llm.invoke.call_count == 2


def test_none_result_exhausts_retries_and_raises_clear_error():
    structured_llm = MagicMock()
    structured_llm.invoke.return_value = None

    with pytest.raises(llm.StructuredOutputParseError):
        llm._invoke_with_retry(structured_llm, "prompt")

    assert structured_llm.invoke.call_count == llm.MAX_LLM_RETRIES + 1


def test_retries_on_unknown_status_code_then_succeeds():
    """`ollama.ResponseError` can carry `status_code=-1` when the client can't attach a real
    HTTP status to the failure -- hit for real during the Milestone H 2025 backtest against
    qwen3.5:397b-cloud ("Internal Server Error (ref: ...) (status code: -1)"), which went
    unretried before the fix since -1 satisfied neither the ==429 nor >=500 branch."""
    structured_llm = MagicMock()
    structured_llm.invoke.side_effect = [
        ollama.ResponseError("Internal Server Error", status_code=-1),
        "ok",
    ]

    result = llm._invoke_with_retry(structured_llm, "prompt")

    assert result == "ok"
    assert structured_llm.invoke.call_count == 2


def test_retries_on_validation_error_then_succeeds():
    """A malformed function-call response (e.g. a missing required field) raises
    `pydantic.ValidationError` from inside langchain's parser rather than returning `None`
    -- hit for real during the Milestone F rich-tier backtest, where glm-5.3-flash:cloud
    omitted `rationale` and this went unretried before the fix."""
    structured_llm = MagicMock()

    class _Model(pydantic.BaseModel):
        x: int

    try:
        _Model()
    except pydantic.ValidationError as exc:
        validation_error = exc

    structured_llm.invoke.side_effect = [validation_error, "ok"]

    result = llm._invoke_with_retry(structured_llm, "prompt")

    assert result == "ok"
    assert structured_llm.invoke.call_count == 2


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
