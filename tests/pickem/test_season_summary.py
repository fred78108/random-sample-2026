"""Milestone E: hand-checked fixture confirming the `season_summary` view (DESIGN §9.2)
ranks configs correctly within a season, including a tie -- built by inserting `runs`/
`scores` rows directly rather than running the graph, since only the view's SQL is under
test here."""

from __future__ import annotations

import pytest

from random_sample.pickem.db import repository

SEASON = 2025


@pytest.fixture
def conn(tmp_path):
    connection = repository.get_connection(tmp_path / "test.db")
    yield connection
    connection.close()


def _seed_config(conn, run_id: str, agent_design: str, week: int, points: int) -> None:
    game_id = f"{run_id}_g"
    repository.upsert_games(
        conn,
        [
            {
                "game_id": game_id, "season": SEASON, "week": week,
                "home_team": "KC", "away_team": "BUF",
                "kickoff_ts": f"2025-09-{6 + week:02d}T13:00:00-04:00",
            }
        ],
    )
    repository.insert_run(
        conn, run_id=run_id, run_type="backtest", season=SEASON, week=week,
        agent_design=agent_design, prompt_variant="p", model="m", context_level="minimal",
    )
    repository.upsert_score(conn, run_id, game_id, correct=True, points_earned=points)


def test_season_summary_ranks_by_total_points_with_ties(conn):
    # config_a and config_b both total 15 across two weeks (a genuine tie); config_c
    # totals 8. RANK() must place both ties at 1 and skip straight to 3 for config_c.
    _seed_config(conn, "a_w1", "config_a", week=1, points=10)
    _seed_config(conn, "a_w2", "config_a", week=2, points=5)
    _seed_config(conn, "b_w1", "config_b", week=1, points=9)
    _seed_config(conn, "b_w2", "config_b", week=2, points=6)
    _seed_config(conn, "c_w1", "config_c", week=1, points=8)

    rows = {
        row["agent_design"]: dict(row)
        for row in conn.execute(
            "SELECT * FROM season_summary WHERE season = ?", (SEASON,)
        ).fetchall()
    }

    assert rows["config_a"]["season_points"] == 15
    assert rows["config_b"]["season_points"] == 15
    assert rows["config_c"]["season_points"] == 8
    assert rows["config_a"]["season_rank"] == 1
    assert rows["config_b"]["season_rank"] == 1
    assert rows["config_c"]["season_rank"] == 3


def test_season_summary_excludes_live_runs(conn):
    _seed_config(conn, "backtest_1", "config_a", week=1, points=10)
    repository.upsert_games(
        conn,
        [
            {
                "game_id": "live_g", "season": SEASON, "week": 1,
                "home_team": "SF", "away_team": "DAL",
                "kickoff_ts": "2025-09-07T13:00:00-04:00",
            }
        ],
    )
    repository.insert_run(
        conn, run_id="live_1", run_type="live", season=SEASON, week=1,
        agent_design="config_a", prompt_variant="p", model="m", context_level="minimal",
    )
    repository.upsert_score(conn, "live_1", "live_g", correct=True, points_earned=999)

    row = conn.execute(
        "SELECT * FROM season_summary WHERE season = ? AND agent_design = 'config_a'", (SEASON,)
    ).fetchone()
    assert row["season_points"] == 10
