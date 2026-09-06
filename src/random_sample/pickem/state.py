"""PickemState and related TypedDicts. See specs/pickem-agent/DESIGN.md §3.1."""

from __future__ import annotations

from typing import Literal, TypedDict


class RunConfig(TypedDict):
    agent_design: str          # key into STRATEGY_REGISTRY, e.g. "single_analyst"
    prompt_variant: str        # key into PROMPT_SET_REGISTRY; must cover the strategy's required_roles
    model: str                 # Ollama model name, e.g. "llama3.1:8b"
    context_level: Literal["minimal", "standard", "rich"]


class Game(TypedDict):
    game_id: str
    season: int
    week: int
    home_team: str
    away_team: str
    kickoff_ts: str


class GameContext(TypedDict):
    game_id: str
    features: dict
    context_hash: str          # sha256 of sorted-key JSON; drives cache + reproducibility


class AgentPrediction(TypedDict):
    game_id: str
    agent_role: str            # "analyst" | "stats" | "situational" | "market" | "synthesis" | ...
    predicted_winner: str
    win_probability: float
    rationale: str


class Pick(TypedDict):
    game_id: str
    predicted_winner: str
    win_probability: float
    confidence: int             # 1..N


class PickemState(TypedDict):
    run_id: str
    run_type: Literal["live", "backtest"]   # which `runs.run_type` this graph run persists as
    season: int
    week: int
    config: RunConfig
    games: list[Game]
    context: dict[str, GameContext]                    # game_id -> context
    raw_predictions: dict[str, list[AgentPrediction]]   # game_id -> per-agent-role outputs
    predictions: dict[str, AgentPrediction]             # game_id -> post-synthesis, one per game
    picks: list[Pick]
    validation_errors: list[str]
    retries: int
