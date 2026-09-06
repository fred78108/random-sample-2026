from __future__ import annotations

import pytest

from random_sample.pickem import scoring
from random_sample.pickem.db import repository


@pytest.fixture
def conn(tmp_path, monkeypatch):
    connection = repository.get_connection(tmp_path / "test.db")
    # `score_week` calls through to nflverse for the latest results; tests seed the DB
    # directly and don't need (or want) a real network call.
    monkeypatch.setattr(scoring.nflverse_client, "ensure_week", lambda conn, season, week: [])
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
                "kickoff_ts": "2026-09-10T13:00:00-04:00",
                "home_score": home_score,
                "away_score": away_score,
            }
        ],
    )


def _insert_live_run_with_picks(conn, run_id, season, week, picks):
    repository.insert_run(
        conn, run_id, "live", season, week, "single_analyst", "single_analyst_v1", "test-model", "minimal"
    )
    repository.insert_picks(conn, run_id, picks)


def test_score_week_grades_final_games_and_leaves_pending_ungraded(conn):
    _insert_game(conn, "g1", 2026, 1, "KC", "BUF", home_score=27, away_score=20)
    _insert_game(conn, "g2", 2026, 1, "SF", "DAL", home_score=None, away_score=None)
    _insert_live_run_with_picks(
        conn,
        "run1",
        2026,
        1,
        [
            {"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.6, "confidence": 2},
            {"game_id": "g2", "predicted_winner": "SF", "win_probability": 0.55, "confidence": 1},
        ],
    )

    summary = scoring.score_week(conn, 2026, 1)

    assert summary.final_count == 1
    assert summary.pending_count == 1
    assert summary.week_points == 2
    assert summary.season_points == 2


def test_score_week_incorrect_pick_earns_zero(conn):
    _insert_game(conn, "g1", 2026, 1, "KC", "BUF", home_score=17, away_score=27)
    _insert_live_run_with_picks(
        conn,
        "run1",
        2026,
        1,
        [{"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.6, "confidence": 1}],
    )

    summary = scoring.score_week(conn, 2026, 1)

    assert summary.final_count == 1
    assert summary.week_points == 0


def test_score_week_is_idempotent(conn):
    _insert_game(conn, "g1", 2026, 1, "KC", "BUF", home_score=27, away_score=20)
    _insert_live_run_with_picks(
        conn,
        "run1",
        2026,
        1,
        [{"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.6, "confidence": 3}],
    )

    first = scoring.score_week(conn, 2026, 1)
    second = scoring.score_week(conn, 2026, 1)

    assert first.week_points == second.week_points == 3
    assert first.season_points == second.season_points == 3


def test_score_week_without_live_run_raises(conn):
    _insert_game(conn, "g1", 2026, 1, "KC", "BUF", home_score=27, away_score=20)

    with pytest.raises(scoring.NoLiveRunError):
        scoring.score_week(conn, 2026, 1)
