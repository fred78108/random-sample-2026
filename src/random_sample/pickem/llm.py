"""Ollama structured-output call wrapper with llm_cache read-through. See
specs/pickem-agent/DESIGN.md §4, §7."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import time

import httpx
import ollama
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

from random_sample.pickem.db import repository
from random_sample.pickem.state import AgentPrediction

logger = logging.getLogger(__name__)

TEMPERATURE = 0.1

# Retry/backoff for cloud-routed Ollama models (`:cloud` suffix, DESIGN §7): these proxy to a
# rate-limited remote service, unlike a plain local model, so a throttled/transient response
# must not abort an hours-long backtest sweep — it should back off and retry the one call.
MAX_LLM_RETRIES = 6
BASE_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, ollama.ResponseError):
        # 429 = throttled; 5xx = transient upstream failure. 4xx other than 429 (bad
        # request, model not found, etc.) is not something a retry will fix.
        return exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, (httpx.TransportError, ollama.RequestError, TimeoutError, ConnectionError))


def _invoke_with_retry(structured_llm, prompt_text: str) -> "PredictionOutput":
    for attempt in range(MAX_LLM_RETRIES + 1):
        try:
            return structured_llm.invoke(prompt_text)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, filtered by _is_retryable
            if attempt >= MAX_LLM_RETRIES or not _is_retryable(exc):
                raise
            backoff = min(BASE_BACKOFF_SECONDS * (2**attempt), MAX_BACKOFF_SECONDS)
            backoff *= 1 + random.random()  # full jitter to avoid a thundering herd on retry
            logger.warning(
                "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1, MAX_LLM_RETRIES + 1, exc, backoff,
            )
            time.sleep(backoff)
    raise AssertionError("unreachable")


class PredictionOutput(BaseModel):
    predicted_winner: str = Field(description="Team abbreviation of the predicted winner")
    win_probability: float = Field(description="Win probability for the predicted winner, in [0.5, 1.0]")
    rationale: str = Field(description="One- or two-sentence rationale for the pick")


def cache_key(
    game_id: str,
    context_hash: str,
    agent_design: str,
    prompt_variant: str,
    model: str,
    agent_role: str,
) -> str:
    raw = "|".join([game_id, context_hash, agent_design, prompt_variant, model, agent_role])
    return hashlib.sha256(raw.encode()).hexdigest()


def predict(
    conn: sqlite3.Connection,
    *,
    model: str,
    agent_role: str,
    game_id: str,
    context_hash: str,
    agent_design: str,
    prompt_variant: str,
    prompt_text: str,
) -> AgentPrediction:
    key = cache_key(game_id, context_hash, agent_design, prompt_variant, model, agent_role)
    cached = repository.get_cached_response(conn, key)
    if cached is not None:
        data = json.loads(cached)
    else:
        llm = ChatOllama(model=model, temperature=TEMPERATURE)
        structured_llm = llm.with_structured_output(PredictionOutput, method="function_calling")
        result: PredictionOutput = _invoke_with_retry(structured_llm, prompt_text)
        data = result.model_dump()
        repository.set_cached_response(conn, key, game_id, json.dumps(data, sort_keys=True))

    return {
        "game_id": game_id,
        "agent_role": agent_role,
        "predicted_winner": data["predicted_winner"],
        "win_probability": float(data["win_probability"]),
        "rationale": data["rationale"],
    }
