"""Confidence-Ranking Agent (FR1): rank by win probability, assign confidence N..1."""

from __future__ import annotations

from random_sample.pickem.state import PickemState


def ranking_node(state: PickemState) -> dict:
    predictions = state["predictions"]
    games = state["games"]
    n = len(games)

    ordered = sorted(games, key=lambda g: predictions[g["game_id"]]["win_probability"], reverse=True)
    picks = []
    for i, game in enumerate(ordered):
        prediction = predictions[game["game_id"]]
        pick = {
            "game_id": game["game_id"],
            "predicted_winner": prediction["predicted_winner"],
            "win_probability": prediction["win_probability"],
            "confidence": n - i,
        }
        # PRD FR9 / Milestone G: only present when the strategy populated them (the
        # promoted strategy) -- display-only, never affects FR1's ranking above.
        if "predicted_home_score" in prediction and "predicted_away_score" in prediction:
            pick["predicted_home_score"] = prediction["predicted_home_score"]
            pick["predicted_away_score"] = prediction["predicted_away_score"]
        picks.append(pick)
    return {"picks": picks}
