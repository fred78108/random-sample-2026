"""PredictionStrategy protocol and registry. See specs/pickem-agent/DESIGN.md §3.3."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from random_sample.pickem.prompts import PromptSet
    from random_sample.pickem.state import AgentPrediction, Game, GameContext, RunConfig


class PredictionStrategy(Protocol):
    name: str
    required_roles: tuple[str, ...]

    def predict(
        self,
        games: list["Game"],
        context: dict[str, "GameContext"],
        config: "RunConfig",
        prompts: "PromptSet",
    ) -> dict[str, "AgentPrediction"]: ...


STRATEGY_REGISTRY: dict[str, type[PredictionStrategy]] = {}


def register_strategy(name: str):
    def decorator(cls: type[PredictionStrategy]) -> type[PredictionStrategy]:
        STRATEGY_REGISTRY[name] = cls
        return cls

    return decorator


def prob_home_wins(prediction: "AgentPrediction", home_team: str) -> float:
    """A prediction's win probability, reframed onto a single "P(home wins)" scale
    regardless of which team it actually predicted — the common currency
    `combine_by_average` needs to average predictions that may disagree on the winner."""
    p = prediction["win_probability"]
    return p if prediction["predicted_winner"] == home_team else 1.0 - p


def combine_by_average(
    *,
    game_id: str,
    home_team: str,
    away_team: str,
    predictions: list["AgentPrediction"],
    weights: list[float] | None = None,
    agent_role: str,
    rationale: str,
) -> "AgentPrediction":
    """Combine several predictions for the same game into one, by weighted-averaging
    each onto a P(home wins) scale and picking the side above 0.5 — the deterministic
    combiner shared by `specialist_deterministic` and `ensemble_self_consistency`."""
    weights = weights or [1.0] * len(predictions)
    total_weight = sum(weights)
    weighted_home_prob = (
        sum(w * prob_home_wins(p, home_team) for w, p in zip(weights, predictions)) / total_weight
    )
    winner = home_team if weighted_home_prob >= 0.5 else away_team
    win_probability = weighted_home_prob if winner == home_team else 1.0 - weighted_home_prob
    return {
        "game_id": game_id,
        "agent_role": agent_role,
        "predicted_winner": winner,
        "win_probability": win_probability,
        "rationale": rationale,
    }


def load_all() -> None:
    """Import every strategy module so its @register_strategy decorator runs."""
    from random_sample.pickem.strategies import (  # noqa: F401
        baseline,
        debate_advocate,
        ensemble_self_consistency,
        single_analyst,
        specialist_deterministic,
        specialist_synthesis,
    )
