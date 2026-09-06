"""Milestone F: unit tests for the Confidence-Ranking Agent (FR1). The core claim under test
is that sorting by win_probability descending and assigning confidence N..1 maximizes expected
points — a rearrangement-inequality result, checked here by brute-force comparison against every
other permutation for small N rather than taken on faith."""

from __future__ import annotations

from itertools import permutations

from random_sample.pickem.nodes.ranking import ranking_node


def _game(game_id: str, home: str = "AAA", away: str = "BBB") -> dict:
    return {
        "game_id": game_id,
        "season": 2026,
        "week": 1,
        "home_team": home,
        "away_team": away,
        "kickoff_ts": "2026-09-10T13:00:00+00:00",
    }


def _prediction(game_id: str, win_probability: float, winner: str = "AAA") -> dict:
    return {
        "game_id": game_id,
        "agent_role": "analyst",
        "predicted_winner": winner,
        "win_probability": win_probability,
        "rationale": "",
    }


def _expected_points(confidences: dict[str, int], probabilities: dict[str, float]) -> float:
    return sum(confidences[gid] * probabilities[gid] for gid in confidences)


def test_assigns_highest_confidence_to_highest_probability():
    games = [_game("g1"), _game("g2"), _game("g3")]
    predictions = {
        "g1": _prediction("g1", 0.55),
        "g2": _prediction("g2", 0.91),
        "g3": _prediction("g3", 0.70),
    }
    state = {"games": games, "predictions": predictions}

    result = ranking_node(state)
    confidence_by_game = {p["game_id"]: p["confidence"] for p in result["picks"]}

    assert confidence_by_game == {"g1": 1, "g2": 3, "g3": 2}


def test_confidences_are_a_permutation_of_1_to_n():
    games = [_game(f"g{i}") for i in range(5)]
    predictions = {g["game_id"]: _prediction(g["game_id"], 0.1 * i) for i, g in enumerate(games)}
    state = {"games": games, "predictions": predictions}

    result = ranking_node(state)

    assert sorted(p["confidence"] for p in result["picks"]) == [1, 2, 3, 4, 5]


def test_ranking_maximizes_expected_points_by_brute_force():
    """For every set of distinct probabilities, no permutation of 1..N confidences beats the
    one ranking_node produces — verified by trying all N! assignments for a small N."""
    game_ids = ["g1", "g2", "g3", "g4", "g5"]
    games = [_game(gid) for gid in game_ids]
    probabilities = {"g1": 0.83, "g2": 0.12, "g3": 0.47, "g4": 0.65, "g5": 0.30}
    predictions = {gid: _prediction(gid, probabilities[gid]) for gid in game_ids}
    state = {"games": games, "predictions": predictions}

    result = ranking_node(state)
    produced = {p["game_id"]: p["confidence"] for p in result["picks"]}
    produced_expected_points = _expected_points(produced, probabilities)

    for assignment in permutations(range(1, len(game_ids) + 1)):
        candidate = dict(zip(game_ids, assignment))
        assert _expected_points(candidate, probabilities) <= produced_expected_points + 1e-9


def test_single_game_gets_confidence_one():
    games = [_game("g1")]
    predictions = {"g1": _prediction("g1", 0.5)}
    state = {"games": games, "predictions": predictions}

    result = ranking_node(state)

    assert result["picks"] == [
        {"game_id": "g1", "predicted_winner": "AAA", "win_probability": 0.5, "confidence": 1}
    ]
