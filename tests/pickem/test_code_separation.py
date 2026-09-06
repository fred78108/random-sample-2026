"""Milestone F: `score` and `recommend` must stay cleanly separated (DESIGN §5.2) — `recommend`
never touches `scores`, and `score` never touches `runs`/`picks`. Checked here by spying on the
actual `repository` write functions each path calls, rather than just trusting the docstring."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from random_sample.pickem import scoring
from random_sample.pickem.db import repository
from random_sample.pickem.nodes import reporting as reporting_module
from random_sample.pickem.nodes.reporting import build_reporting_node


@pytest.fixture
def conn(tmp_path, monkeypatch):
    connection = repository.get_connection(tmp_path / "test.db")
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


def test_score_week_never_writes_runs_or_picks(conn):
    """`score` grades existing picks — it must never call the run/pick-creation functions
    that only `recommend` (via the reporting node) should ever call."""
    _insert_game(conn, "g1", 2026, 1, "KC", "BUF", home_score=27, away_score=20)
    repository.insert_run(
        conn, "run1", "live", 2026, 1, "single_analyst", "single_analyst_v1", "test-model", "minimal"
    )
    repository.insert_picks(
        conn,
        "run1",
        [{"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.6, "confidence": 1}],
    )

    with patch.object(repository, "insert_run", wraps=repository.insert_run) as spy_insert_run, patch.object(
        repository, "insert_picks", wraps=repository.insert_picks
    ) as spy_insert_picks:
        scoring.score_week(conn, 2026, 1)

    spy_insert_run.assert_not_called()
    spy_insert_picks.assert_not_called()


def test_reporting_node_never_writes_scores(conn, tmp_path, monkeypatch):
    """The live/backtest reporting node persists `runs`/`picks` only — it must never call
    the scoring write path (`upsert_score`), which only `score`/the backtest harness own."""
    monkeypatch.setattr(reporting_module, "REPORTS_ROOT", tmp_path / "reports")
    _insert_game(conn, "g1", 2026, 1, "KC", "BUF")
    state = {
        "run_id": "run1",
        "run_type": "live",
        "season": 2026,
        "week": 1,
        "config": {
            "agent_design": "single_analyst",
            "prompt_variant": "single_analyst_v1",
            "model": "test-model",
            "context_level": "minimal",
        },
        "games": [
            {
                "game_id": "g1",
                "season": 2026,
                "week": 1,
                "home_team": "KC",
                "away_team": "BUF",
                "kickoff_ts": "2026-09-10T13:00:00-04:00",
            }
        ],
        "context": {},
        "raw_predictions": {},
        "predictions": {},
        "picks": [{"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.6, "confidence": 1}],
        "validation_errors": [],
        "retries": 0,
    }
    reporting_node = build_reporting_node(conn)

    with patch.object(repository, "upsert_score") as spy_upsert_score:
        reporting_node(state)

    spy_upsert_score.assert_not_called()
