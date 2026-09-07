"""Milestone G: unit tests for score prediction & weekly extremes (PRD FR9, DESIGN §10
decision #7) -- the `picks` table migration for pre-existing databases, the promoted
strategy's (`single_analyst`) pass-through of predicted scores from `llm.predict` into a
`Pick`, and the weekly highest/lowest-score selection (including its tie-break rule)."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from random_sample.pickem import llm
from random_sample.pickem.db import repository
from random_sample.pickem.nodes.reporting import _team_score_extremes
from random_sample.pickem.nodes.ranking import ranking_node
from random_sample.pickem.strategies.single_analyst import SingleAnalystStrategy


# --- schema migration ------------------------------------------------------------------


def test_migrates_pre_milestone_g_picks_table(tmp_path):
    """A `pickem.db` created before Milestone G has a `picks` table with no
    predicted_home_score/predicted_away_score columns (sqlite3's ADD COLUMN has no IF NOT
    EXISTS form, so this must be a runtime migration, same pattern as Milestone C's
    `games` migration) -- `get_connection` must add them without touching existing rows."""
    db_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, run_type TEXT, season INTEGER, week INTEGER,
            agent_design TEXT, prompt_variant TEXT, model TEXT, context_level TEXT,
            created_at TEXT, status TEXT
        )
        """
    )
    legacy.execute(
        """
        CREATE TABLE picks (
            run_id TEXT NOT NULL, game_id TEXT NOT NULL, predicted_winner TEXT NOT NULL,
            win_probability REAL NOT NULL, confidence INTEGER NOT NULL,
            PRIMARY KEY (run_id, game_id)
        )
        """
    )
    legacy.execute(
        "INSERT INTO picks VALUES ('r1', 'g1', 'KC', 0.7, 1)"
    )
    legacy.commit()
    legacy.close()

    conn = repository.get_connection(db_path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(picks)")}
    assert {"predicted_home_score", "predicted_away_score"} <= columns

    # Pre-existing row survives the migration, with the new columns NULL.
    row = conn.execute("SELECT * FROM picks WHERE game_id = 'g1'").fetchone()
    assert row["predicted_winner"] == "KC"
    assert row["predicted_home_score"] is None
    assert row["predicted_away_score"] is None
    conn.close()


def test_insert_picks_persists_predicted_scores(tmp_path):
    conn = repository.get_connection(tmp_path / "test.db")
    repository.upsert_games(
        conn,
        [
            {
                "game_id": "g1", "season": 2025, "week": 3,
                "home_team": "KC", "away_team": "BUF",
                "kickoff_ts": "2025-09-21T13:00:00-04:00",
            }
        ],
    )
    repository.insert_run(
        conn, run_id="r1", run_type="live", season=2025, week=3,
        agent_design="single_analyst", prompt_variant="single_analyst_v1",
        model="test-model", context_level="rich",
    )
    repository.insert_picks(
        conn, "r1",
        [
            {
                "game_id": "g1", "predicted_winner": "KC", "win_probability": 0.7,
                "confidence": 1, "predicted_home_score": 27.0, "predicted_away_score": 20.0,
            }
        ],
    )

    row = conn.execute("SELECT * FROM picks WHERE game_id = 'g1'").fetchone()
    assert row["predicted_home_score"] == 27.0
    assert row["predicted_away_score"] == 20.0
    conn.close()


def test_insert_picks_without_scores_still_works(tmp_path):
    """A pick from a non-promoted strategy has no score fields at all (`NotRequired`) --
    `insert_picks` must not KeyError, and the row's score columns come back NULL."""
    conn = repository.get_connection(tmp_path / "test.db")
    repository.upsert_games(
        conn,
        [
            {
                "game_id": "g1", "season": 2025, "week": 3,
                "home_team": "KC", "away_team": "BUF",
                "kickoff_ts": "2025-09-21T13:00:00-04:00",
            }
        ],
    )
    repository.insert_run(
        conn, run_id="r1", run_type="backtest", season=2025, week=3,
        agent_design="debate_advocate", prompt_variant="debate_v1",
        model="test-model", context_level="rich",
    )
    repository.insert_picks(
        conn, "r1",
        [{"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.7, "confidence": 1}],
    )

    row = conn.execute("SELECT * FROM picks WHERE game_id = 'g1'").fetchone()
    assert row["predicted_home_score"] is None
    assert row["predicted_away_score"] is None
    conn.close()


# --- promoted strategy (single_analyst) score pass-through ------------------------------


def _game(game_id="g1", home="KC", away="BUF") -> dict:
    return {
        "game_id": game_id, "season": 2025, "week": 3,
        "home_team": home, "away_team": away,
        "kickoff_ts": "2025-09-21T13:00:00-04:00",
    }


def _context(game_id="g1") -> dict:
    return {game_id: {"game_id": game_id, "features": {}, "context_hash": "x"}}


def test_single_analyst_requests_and_populates_scores(monkeypatch):
    """`single_analyst` is the strategy promoted in Milestone F, so it's the one required
    to populate `predicted_home_score`/`predicted_away_score` -- verify it asks `llm.predict`
    for the score-carrying schema and passes the result straight through (a plain
    pass-through combination, since it's already one call per game)."""
    captured = {}

    def fake_predict(conn, **kwargs):
        captured.update(kwargs)
        return {
            "game_id": kwargs["game_id"],
            "agent_role": kwargs["agent_role"],
            "predicted_winner": "KC",
            "win_probability": 0.68,
            "rationale": "fake",
            "predicted_home_score": 27.0,
            "predicted_away_score": 20.0,
        }

    monkeypatch.setattr(llm, "predict", fake_predict)
    strategy = SingleAnalystStrategy(conn=MagicMock())
    prompts = {"id": "single_analyst_v1", "templates": {"analyst": "{home_team} {away_team} {features_json}"}}

    predictions = strategy.predict([_game()], _context(), config={"model": "m"}, prompts=prompts)

    assert captured["response_schema"] is llm.PredictionOutputWithScore
    assert predictions["g1"]["predicted_home_score"] == 27.0
    assert predictions["g1"]["predicted_away_score"] == 20.0


def test_llm_predict_omits_score_fields_for_plain_schema(monkeypatch):
    """Every other strategy still calls `llm.predict` with the default `PredictionOutput`
    schema (no score fields) -- confirms the returned `AgentPrediction` has no score keys
    at all, preserving `NotRequired` for callers that never asked for scores."""
    conn = repository.get_connection(":memory:")
    structured_llm = MagicMock()
    structured_llm.invoke.return_value = llm.PredictionOutput(
        predicted_winner="KC", win_probability=0.6, rationale="r"
    )
    monkeypatch.setattr(llm, "ChatOllama", lambda **_: MagicMock(with_structured_output=lambda *a, **k: structured_llm))

    prediction = llm.predict(
        conn, model="m", agent_role="stats", game_id="g1", context_hash="x",
        agent_design="specialist_synthesis", prompt_variant="specialist_v1", prompt_text="p",
    )

    assert "predicted_home_score" not in prediction
    assert "predicted_away_score" not in prediction


# --- ranking pass-through ----------------------------------------------------------------


def test_ranking_node_carries_scores_through_to_picks():
    games = [_game("g1"), _game("g2", home="SF", away="DAL")]
    predictions = {
        "g1": {
            "game_id": "g1", "agent_role": "analyst", "predicted_winner": "KC",
            "win_probability": 0.7, "rationale": "", "predicted_home_score": 27.0,
            "predicted_away_score": 20.0,
        },
        "g2": {
            "game_id": "g2", "agent_role": "analyst", "predicted_winner": "SF",
            "win_probability": 0.6, "rationale": "",
        },
    }

    result = ranking_node({"games": games, "predictions": predictions})
    picks_by_id = {p["game_id"]: p for p in result["picks"]}

    assert picks_by_id["g1"]["predicted_home_score"] == 27.0
    assert picks_by_id["g1"]["predicted_away_score"] == 20.0
    assert "predicted_home_score" not in picks_by_id["g2"]
    assert "predicted_away_score" not in picks_by_id["g2"]


# --- weekly extremes + tie-break -----------------------------------------------------------


def _pick(game_id, home_score, away_score):
    return {
        "game_id": game_id, "predicted_winner": "H", "win_probability": 0.6,
        "confidence": 1, "predicted_home_score": home_score, "predicted_away_score": away_score,
    }


def _state(games, picks):
    return {"games": games, "picks": picks}


def test_weekly_extremes_picks_single_highest_and_lowest_team():
    games = [
        {"game_id": "g1", "home_team": "KC", "away_team": "BUF", "kickoff_ts": "2025-09-21T13:00:00Z"},
        {"game_id": "g2", "home_team": "SF", "away_team": "DAL", "kickoff_ts": "2025-09-21T13:00:00Z"},
    ]
    picks = [_pick("g1", 31.0, 17.0), _pick("g2", 10.0, 6.0)]

    highest, lowest = _team_score_extremes(_state(games, picks))

    assert highest == {"team": "KC", "score": 31.0}
    assert lowest == {"team": "DAL", "score": 6.0}


def test_weekly_extremes_tie_break_earliest_kickoff_then_team():
    """Two teams predicted to score the same (week-high) value: the tie-break rule is
    earliest kickoff first, then team abbreviation -- documented in
    `nodes/reporting._team_score_extremes`."""
    games = [
        {"game_id": "g1", "home_team": "KC", "away_team": "BUF", "kickoff_ts": "2025-09-21T16:00:00Z"},
        {"game_id": "g2", "home_team": "SF", "away_team": "DAL", "kickoff_ts": "2025-09-21T13:00:00Z"},
    ]
    picks = [_pick("g1", 30.0, 10.0), _pick("g2", 30.0, 6.0)]

    highest, _lowest = _team_score_extremes(_state(games, picks))

    # SF (g2) kicks off earlier than KC (g1), so it wins the tie despite equal scores.
    assert highest == {"team": "SF", "score": 30.0}


def test_weekly_extremes_tie_break_same_kickoff_falls_back_to_team_name():
    games = [
        {"game_id": "g1", "home_team": "KC", "away_team": "BUF", "kickoff_ts": "2025-09-21T13:00:00Z"},
        {"game_id": "g2", "home_team": "SF", "away_team": "DAL", "kickoff_ts": "2025-09-21T13:00:00Z"},
    ]
    picks = [_pick("g1", 30.0, 10.0), _pick("g2", 30.0, 6.0)]

    highest, _lowest = _team_score_extremes(_state(games, picks))

    # Same score, same kickoff: alphabetically-first team abbreviation wins the tie.
    assert highest == {"team": "KC", "score": 30.0}


def test_weekly_extremes_none_when_no_pick_has_scores():
    games = [{"game_id": "g1", "home_team": "KC", "away_team": "BUF", "kickoff_ts": "2025-09-21T13:00:00Z"}]
    picks = [{"game_id": "g1", "predicted_winner": "KC", "win_probability": 0.6, "confidence": 1}]

    highest, lowest = _team_score_extremes(_state(games, picks))

    assert highest is None
    assert lowest is None
