"""Confidence-Ranking Agent (FR1): rank by win probability, assign confidence N..1."""

from __future__ import annotations

from random_sample.pickem.state import PickemState


def ranking_node(state: PickemState) -> dict:
    predictions = state["predictions"]
    games = state["games"]
    n = len(games)

    ordered = sorted(games, key=lambda g: predictions[g["game_id"]]["win_probability"], reverse=True)
    picks = [
        {
            "game_id": game["game_id"],
            "predicted_winner": predictions[game["game_id"]]["predicted_winner"],
            "win_probability": predictions[game["game_id"]]["win_probability"],
            "confidence": n - i,
        }
        for i, game in enumerate(ordered)
    ]
    return {"picks": picks}
