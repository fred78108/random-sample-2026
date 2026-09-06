"""Milestone D: dedicated regression test for point-in-time correctness (DESIGN §9.1, §6).
Rolling features (record, recent form) must be computed only from games completed strictly
before the target week — this test fails if a later week's result ever leaks into an
earlier (or that same) week's context, which would silently invalidate a backtest."""

from __future__ import annotations

import pytest

from random_sample.pickem.db import repository
from random_sample.pickem.nodes import context as context_module
from random_sample.pickem.nodes.context import build_context_node
from random_sample.pickem.state import PickemState


@pytest.fixture
def conn(tmp_path, monkeypatch):
    connection = repository.get_connection(tmp_path / "test.db")
    # Standard tier also pulls season EPA via nflreadpy; stub it out so the test never
    # touches the network — record/recent-form leakage is what's under test here, not EPA.
    monkeypatch.setattr(context_module, "_season_team_stats", lambda season: None)
    yield connection
    connection.close()


def _insert_game(conn, game_id, season, week, home, away, home_score=None, away_score=None):
    repository.upsert_games(
        conn,
        [
            {
                "game_id": game_id,
                "season": season,
                "week": week,
                "home_team": home,
                "away_team": away,
                "kickoff_ts": f"2025-09-{6 + week:02d}T13:00:00-04:00",
                "home_score": home_score,
                "away_score": away_score,
            }
        ],
    )


def _state_for(season: int, week: int, games: list[dict]) -> PickemState:
    return {
        "run_id": "r1",
        "run_type": "backtest",
        "season": season,
        "week": week,
        "config": {
            "agent_design": "single_analyst",
            "prompt_variant": "single_analyst_v1",
            "model": "test-model",
            "context_level": "standard",
        },
        "games": games,
        "context": {},
        "raw_predictions": {},
        "predictions": {},
        "picks": [],
        "validation_errors": [],
        "retries": 0,
    }


def test_week1_context_ignores_already_synced_week2_results(conn):
    # A fully-synced historical season: week 2's result is already in the DB (as it would
    # be for any real backtest replay), but week 1's context must not reflect it.
    _insert_game(conn, "g_w1", 2025, 1, "KC", "BUF", home_score=27, away_score=20)
    _insert_game(conn, "g_w2", 2025, 2, "BUF", "KC", home_score=30, away_score=10)

    node = build_context_node(conn)
    week1_games = [
        {
            "game_id": "g_w1", "season": 2025, "week": 1,
            "home_team": "KC", "away_team": "BUF",
            "kickoff_ts": "2025-09-07T13:00:00-04:00",
        }
    ]
    result = node(_state_for(2025, 1, week1_games))

    features = result["context"]["g_w1"]["features"]
    assert features["home_record"] == {"wins": 0, "losses": 0, "ties": 0}
    assert features["away_record"] == {"wins": 0, "losses": 0, "ties": 0}
    assert features["home_point_differential"] == 0
    assert features["away_point_differential"] == 0
    assert features["home_recent_form"]["games_played"] == 0
    assert features["away_recent_form"]["games_played"] == 0


def test_week2_context_reflects_week1_but_not_its_own_result(conn):
    _insert_game(conn, "g_w1", 2025, 1, "KC", "BUF", home_score=27, away_score=20)
    _insert_game(conn, "g_w2", 2025, 2, "BUF", "KC", home_score=30, away_score=10)

    node = build_context_node(conn)
    week2_games = [
        {
            "game_id": "g_w2", "season": 2025, "week": 2,
            "home_team": "BUF", "away_team": "KC",
            "kickoff_ts": "2025-09-14T13:00:00-04:00",
        }
    ]
    result = node(_state_for(2025, 2, week2_games))

    features = result["context"]["g_w2"]["features"]
    # BUF (home in week 2) lost as away in week 1; KC (away in week 2) won as home in week 1.
    assert features["home_record"] == {"wins": 0, "losses": 1, "ties": 0}
    assert features["away_record"] == {"wins": 1, "losses": 0, "ties": 0}
    # Exactly one prior game each — week 2's own (not-yet-happened, point-in-time) result
    # must not be counted, even though it's already sitting in the DB.
    assert features["home_recent_form"]["games_played"] == 1
    assert features["away_recent_form"]["games_played"] == 1
