"""Milestone E: `recommend`'s own weekly charts (DESIGN §8, deferred from Milestone A) --
the reporting node must write a calibration scatter + season cumulative-points line under
reports/<season>/<week>/ for a live run, and must not do so for a backtest run (Milestone
D's harness already sweeps thousands of grid cells; a chart per cell would be unusable)."""

from __future__ import annotations

import pytest

from random_sample.pickem.db import repository
from random_sample.pickem.nodes import reporting as reporting_module
from random_sample.pickem.nodes.reporting import build_reporting_node
from random_sample.pickem.state import PickemState


@pytest.fixture
def conn(tmp_path):
    connection = repository.get_connection(tmp_path / "test.db")
    yield connection
    connection.close()


def _state(run_type: str, picks: list[dict]) -> PickemState:
    game_ids = [p["game_id"] for p in picks]
    return {
        "run_id": "r1",
        "run_type": run_type,
        "season": 2025,
        "week": 3,
        "config": {
            "agent_design": "single_analyst", "prompt_variant": "single_analyst_v1",
            "model": "test-model", "context_level": "minimal",
        },
        "games": [
            {
                "game_id": gid, "season": 2025, "week": 3,
                "home_team": "KC", "away_team": "BUF",
                "kickoff_ts": "2025-09-21T13:00:00-04:00",
            }
            for gid in game_ids
        ],
        "context": {},
        "raw_predictions": {},
        "predictions": {},
        "picks": picks,
        "validation_errors": [],
        "retries": 0,
    }


def _seed_game(conn, game_id):
    repository.upsert_games(
        conn,
        [
            {
                "game_id": game_id, "season": 2025, "week": 3,
                "home_team": "KC", "away_team": "BUF",
                "kickoff_ts": "2025-09-21T13:00:00-04:00",
            }
        ],
    )


def test_live_run_writes_weekly_charts(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(reporting_module, "REPORTS_ROOT", tmp_path / "reports")
    _seed_game(conn, "g1")
    picks = [{"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.7, "confidence": 1}]

    node = build_reporting_node(conn)
    node(_state("live", picks))

    week_dir = tmp_path / "reports" / "2025" / "3"
    assert (week_dir / "calibration_scatter.svg").exists()
    assert (week_dir / "season_cumulative_points.svg").exists()


def test_backtest_run_writes_no_charts(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(reporting_module, "REPORTS_ROOT", tmp_path / "reports")
    _seed_game(conn, "g1")
    picks = [{"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.7, "confidence": 1}]

    node = build_reporting_node(conn)
    node(_state("backtest", picks))

    assert not (tmp_path / "reports").exists()
