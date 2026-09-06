"""Milestone F: unit tests for the Validator Agent's edge cases (DESIGN §3.2) — missing game,
duplicate confidence, out-of-range probability — plus all four `route_after_validate` branches."""

from __future__ import annotations

from random_sample.pickem.nodes.validator import MAX_RETRIES, route_after_validate, validator_node


def _game(game_id: str, home: str = "AAA", away: str = "BBB") -> dict:
    return {
        "game_id": game_id,
        "season": 2026,
        "week": 1,
        "home_team": home,
        "away_team": away,
        "kickoff_ts": "2026-09-10T13:00:00+00:00",
    }


def _prediction(game_id: str, winner: str = "AAA", win_probability: float = 0.6) -> dict:
    return {
        "game_id": game_id,
        "agent_role": "analyst",
        "predicted_winner": winner,
        "win_probability": win_probability,
        "rationale": "",
    }


def _pick(game_id: str, confidence: int, winner: str = "AAA", win_probability: float = 0.6) -> dict:
    return {
        "game_id": game_id,
        "predicted_winner": winner,
        "win_probability": win_probability,
        "confidence": confidence,
    }


def _state(games, predictions, picks, retries=0) -> dict:
    return {
        "games": games,
        "predictions": predictions,
        "picks": picks,
        "validation_errors": [],
        "retries": retries,
    }


def test_valid_state_has_no_errors():
    games = [_game("g1"), _game("g2")]
    predictions = {"g1": _prediction("g1"), "g2": _prediction("g2")}
    picks = [_pick("g1", 2), _pick("g2", 1)]
    state = _state(games, predictions, picks)

    result = validator_node(state)

    assert result["validation_errors"] == []
    assert "retries" not in result


def test_missing_prediction_is_an_error_and_increments_retries():
    games = [_game("g1"), _game("g2")]
    predictions = {"g1": _prediction("g1")}
    picks = [_pick("g1", 2), _pick("g2", 1)]
    state = _state(games, predictions, picks, retries=0)

    result = validator_node(state)

    assert any("missing prediction" in e for e in result["validation_errors"])
    assert result["retries"] == 1


def test_missing_game_in_picks_is_an_error():
    games = [_game("g1"), _game("g2")]
    predictions = {"g1": _prediction("g1"), "g2": _prediction("g2")}
    picks = [_pick("g1", 1)]  # g2 missing from picks entirely
    state = _state(games, predictions, picks)

    result = validator_node(state)

    assert any("do not cover exactly" in e for e in result["validation_errors"])


def test_duplicate_confidence_is_an_error():
    games = [_game("g1"), _game("g2")]
    predictions = {"g1": _prediction("g1"), "g2": _prediction("g2")}
    picks = [_pick("g1", 1), _pick("g2", 1)]  # duplicate confidence value
    state = _state(games, predictions, picks)

    result = validator_node(state)

    assert any("permutation of 1..N" in e for e in result["validation_errors"])


def test_out_of_range_probability_is_an_error():
    games = [_game("g1")]
    predictions = {"g1": _prediction("g1", win_probability=1.5)}
    picks = [_pick("g1", 1)]
    state = _state(games, predictions, picks)

    result = validator_node(state)

    assert any("win_probability out of range" in e for e in result["validation_errors"])


def test_negative_probability_is_an_error():
    games = [_game("g1")]
    predictions = {"g1": _prediction("g1", win_probability=-0.1)}
    picks = [_pick("g1", 1)]
    state = _state(games, predictions, picks)

    result = validator_node(state)

    assert any("win_probability out of range" in e for e in result["validation_errors"])


def test_predicted_winner_not_in_matchup_is_an_error():
    games = [_game("g1", home="AAA", away="BBB")]
    predictions = {"g1": _prediction("g1", winner="ZZZ")}
    picks = [_pick("g1", 1, winner="ZZZ")]
    state = _state(games, predictions, picks)

    result = validator_node(state)

    assert any("predicted_winner not in matchup" in e for e in result["validation_errors"])


def test_route_valid_goes_to_report():
    state = _state([], {}, [])
    state["validation_errors"] = []
    assert route_after_validate(state) == "report"


def test_route_missing_prediction_goes_to_prediction():
    games = [_game("g1")]
    state = _state(games, {}, [_pick("g1", 1)], retries=1)
    state["validation_errors"] = ["g1: missing prediction"]
    assert route_after_validate(state) == "prediction"


def test_route_ranking_failure_goes_to_rank():
    games = [_game("g1"), _game("g2")]
    predictions = {"g1": _prediction("g1"), "g2": _prediction("g2")}
    state = _state(games, predictions, [_pick("g1", 1), _pick("g2", 1)], retries=1)
    state["validation_errors"] = ["confidence values are not a permutation of 1..N"]
    assert route_after_validate(state) == "rank"


def test_route_exhausted_retries_goes_to_report_even_with_errors():
    games = [_game("g1")]
    state = _state(games, {}, [_pick("g1", 1)], retries=MAX_RETRIES)
    state["validation_errors"] = ["g1: missing prediction"]
    assert route_after_validate(state) == "report"
