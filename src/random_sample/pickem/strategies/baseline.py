"""Non-LLM baselines for DESIGN §9.3 / PRD §14's "must beat a naive baseline" risk. Run
through the exact same walk-forward harness as every LLM-based `agent_design` (DESIGN §9.3:
"runnable through the same walk-forward harness so it appears in the same `season_summary`
rows as real configs") rather than computed out-of-band, so `aggregation.py` can compare
margins with plain SQL instead of a bespoke code path.

Both declare `required_roles = ()` — they never touch `prompts` — so they're compatible with
any registered `prompt_variant`; `prompts/baseline_v1.py`'s empty prompt set exists so a run
still has a self-describing `prompt_variant` value on file rather than borrowing an unrelated
real one. `model` is likewise unused; pass a placeholder like `--model none`.
"""

from __future__ import annotations

from random_sample.pickem.state import AgentPrediction, Game, GameContext, RunConfig
from random_sample.pickem.strategies import register_strategy


def _win_pct(record: dict) -> float:
    games_played = record["wins"] + record["losses"] + record["ties"]
    if games_played == 0:
        return 0.5
    return (record["wins"] + 0.5 * record["ties"]) / games_played


@register_strategy("naive_favorite")
class NaiveFavoriteStrategy:
    """"Always favor the team with the better record" (PRD §14) — the floor any real
    config must beat. Uses only the `minimal`-tier record/point-differential features
    (present at every context level), so it's cheap to run across the whole grid."""

    name = "naive_favorite"
    required_roles: tuple[str, ...] = ()

    def __init__(self, conn) -> None:
        pass

    def predict(
        self,
        games: list[Game],
        context: dict[str, GameContext],
        config: RunConfig,
        prompts,
    ) -> dict[str, AgentPrediction]:
        predictions: dict[str, AgentPrediction] = {}
        for game in games:
            features = context[game["game_id"]]["features"]
            home_pct = _win_pct(features["home_record"])
            away_pct = _win_pct(features["away_record"])
            prob_home = 0.5 + (home_pct - away_pct) / 2

            if prob_home == 0.5:
                # Records dead even (e.g. week 1, or identical W-L-T) -- break the tie by
                # point differential, then default to the home team as the naive
                # assumption of last resort (home-field advantage).
                diff = features["home_point_differential"] - features["away_point_differential"]
                winner = game["away_team"] if diff < 0 else game["home_team"]
            else:
                winner = game["home_team"] if prob_home > 0.5 else game["away_team"]
            win_probability = prob_home if winner == game["home_team"] else 1.0 - prob_home

            predictions[game["game_id"]] = {
                "game_id": game["game_id"],
                "agent_role": "naive_favorite",
                "predicted_winner": winner,
                "win_probability": win_probability,
                "rationale": f"record: home {features['home_record']} vs away {features['away_record']}",
            }
        return predictions


def _implied_prob(moneyline: float) -> float:
    """American odds -> implied win probability (vig included; de-vigged by the caller)."""
    if moneyline < 0:
        return -moneyline / (-moneyline + 100)
    return 100 / (moneyline + 100)


@register_strategy("market_favorite")
class MarketFavoriteStrategy:
    """DESIGN §9.3's market baseline: the `market` specialist role's own signal (nflverse
    moneylines, de-vigged; the point spread as a fallback) used directly as the pick, with
    no LLM call. The `market` feature slice only exists at `context_level="rich"` (DESIGN
    §3.3), so this baseline is only meaningful there; at a lower tier it degrades to a
    home-field default pick -- the same "legitimate grid cell, not a bug" degrade Milestone
    C's specialist roles already accept for a context level that doesn't unlock their slice.
    """

    name = "market_favorite"
    required_roles: tuple[str, ...] = ()

    def __init__(self, conn) -> None:
        pass

    def predict(
        self,
        games: list[Game],
        context: dict[str, GameContext],
        config: RunConfig,
        prompts,
    ) -> dict[str, AgentPrediction]:
        predictions: dict[str, AgentPrediction] = {}
        for game in games:
            features = context[game["game_id"]]["features"]
            market = features.get("market", {})
            home_ml, away_ml = market.get("home_moneyline"), market.get("away_moneyline")
            spread = market.get("spread_line")

            if home_ml is not None and away_ml is not None:
                implied_home = _implied_prob(home_ml)
                implied_away = _implied_prob(away_ml)
                prob_home = implied_home / (implied_home + implied_away)
            elif spread is not None:
                # No moneyline on file -- approximate from the point spread. Positive
                # spread_line means the home team is favored (confirmed empirically
                # against cached 2026 schedule rows, where it always paired with a
                # negative home_moneyline); 3 points of margin maps to roughly a 59% win
                # probability, capped at 95%, in line with common NFL spread-to-win-prob
                # tables.
                prob_home = 0.5 + max(-0.45, min(0.45, spread * 0.03))
            else:
                prob_home = 0.5

            winner = game["home_team"] if prob_home >= 0.5 else game["away_team"]
            win_probability = prob_home if winner == game["home_team"] else 1.0 - prob_home

            predictions[game["game_id"]] = {
                "game_id": game["game_id"],
                "agent_role": "market_favorite",
                "predicted_winner": winner,
                "win_probability": win_probability,
                "rationale": f"market: home_ml={home_ml} away_ml={away_ml} spread={spread}",
            }
        return predictions
