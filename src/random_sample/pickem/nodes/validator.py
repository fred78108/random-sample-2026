"""Validator Agent: permutation/probability checks + retry-loop routing (DESIGN §3.2)."""

from __future__ import annotations

from typing import Literal

from random_sample.pickem.state import AgentPrediction, Game, Pick, PickemState

MAX_RETRIES = 3


def _prediction_errors(games: list[Game], predictions: dict[str, AgentPrediction]) -> list[str]:
    errors = []
    for game in games:
        prediction = predictions.get(game["game_id"])
        if prediction is None:
            errors.append(f"{game['game_id']}: missing prediction")
            continue
        if prediction["predicted_winner"] not in (game["home_team"], game["away_team"]):
            errors.append(f"{game['game_id']}: predicted_winner not in matchup")
        if not (0.0 <= prediction["win_probability"] <= 1.0):
            errors.append(f"{game['game_id']}: win_probability out of range")
    return errors


def _ranking_errors(games: list[Game], picks: list[Pick]) -> list[str]:
    errors = []
    n = len(games)
    game_ids = {g["game_id"] for g in games}
    pick_game_ids = {p["game_id"] for p in picks}
    if pick_game_ids != game_ids:
        errors.append("picks do not cover exactly the week's games")
    confidences = sorted(p["confidence"] for p in picks)
    if confidences != list(range(1, n + 1)):
        errors.append("confidence values are not a permutation of 1..N")
    return errors


def validator_node(state: PickemState) -> dict:
    errors = _prediction_errors(state["games"], state["predictions"]) + _ranking_errors(
        state["games"], state["picks"]
    )
    if not errors:
        return {"validation_errors": []}
    return {"validation_errors": errors, "retries": state["retries"] + 1}


def route_after_validate(state: PickemState) -> Literal["prediction", "rank", "report"]:
    if not state["validation_errors"]:
        return "report"
    if state["retries"] >= MAX_RETRIES:
        return "report"
    if _prediction_errors(state["games"], state["predictions"]):
        return "prediction"
    return "rank"
