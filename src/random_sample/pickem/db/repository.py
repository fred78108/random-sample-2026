"""Thin sqlite3 data-access layer. See specs/pickem-agent/DESIGN.md §4."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from random_sample.pickem.state import Game, Pick

DEFAULT_DB_PATH = Path("pickem.db")

# Columns added to `games` after its initial CREATE TABLE (Milestone C's situational/
# market fields). sqlite3's ADD COLUMN has no IF NOT EXISTS form, so a pre-existing
# `games` table is migrated at runtime instead — see _migrate_games_table below.
_NEW_GAMES_COLUMNS = {
    "home_rest": "INTEGER",
    "away_rest": "INTEGER",
    "div_game": "INTEGER",
    "roof": "TEXT",
    "surface": "TEXT",
    "spread_line": "REAL",
    "home_moneyline": "INTEGER",
    "away_moneyline": "INTEGER",
    "total_line": "REAL",
}


def _migrate_games_table(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(games)")}
    for column, sql_type in _NEW_GAMES_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE games ADD COLUMN {column} {sql_type}")
    conn.commit()


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the sqlite3 database and ensure the schema exists."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema_sql = resources.files("random_sample.pickem.db").joinpath("schema.sql").read_text()
    conn.executescript(schema_sql)
    _migrate_games_table(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_OPTIONAL_GAME_FIELDS = (
    "home_score", "away_score", "home_rest", "away_rest", "div_game", "roof",
    "surface", "spread_line", "home_moneyline", "away_moneyline", "total_line",
)


def upsert_games(conn: sqlite3.Connection, games: list[dict]) -> None:
    """Insert or refresh game rows. `games` dicts may include home_score/away_score and
    the situational/market fields (nullable); a NULL in the incoming row never overwrites
    an existing non-NULL value already stored."""
    conn.executemany(
        """
        INSERT INTO games (game_id, season, week, home_team, away_team, kickoff_ts,
                            home_score, away_score, home_rest, away_rest, div_game,
                            roof, surface, spread_line, home_moneyline, away_moneyline,
                            total_line)
        VALUES (:game_id, :season, :week, :home_team, :away_team, :kickoff_ts,
                :home_score, :away_score, :home_rest, :away_rest, :div_game,
                :roof, :surface, :spread_line, :home_moneyline, :away_moneyline,
                :total_line)
        ON CONFLICT (game_id) DO UPDATE SET
            season = excluded.season,
            week = excluded.week,
            home_team = excluded.home_team,
            away_team = excluded.away_team,
            kickoff_ts = excluded.kickoff_ts,
            home_score = COALESCE(excluded.home_score, games.home_score),
            away_score = COALESCE(excluded.away_score, games.away_score),
            home_rest = COALESCE(excluded.home_rest, games.home_rest),
            away_rest = COALESCE(excluded.away_rest, games.away_rest),
            div_game = COALESCE(excluded.div_game, games.div_game),
            roof = COALESCE(excluded.roof, games.roof),
            surface = COALESCE(excluded.surface, games.surface),
            spread_line = COALESCE(excluded.spread_line, games.spread_line),
            home_moneyline = COALESCE(excluded.home_moneyline, games.home_moneyline),
            away_moneyline = COALESCE(excluded.away_moneyline, games.away_moneyline),
            total_line = COALESCE(excluded.total_line, games.total_line)
        """,
        [{field: None for field in _OPTIONAL_GAME_FIELDS} | game for game in games],
    )
    conn.commit()


def games_from_db(conn: sqlite3.Connection, season: int, week: int) -> list[Game]:
    rows = conn.execute(
        """
        SELECT game_id, season, week, home_team, away_team, kickoff_ts
        FROM games WHERE season = ? AND week = ?
        ORDER BY kickoff_ts, game_id
        """,
        (season, week),
    ).fetchall()
    return [dict(row) for row in rows]  # type: ignore[misc]


def completed_games_before(conn: sqlite3.Connection, season: int, week: int) -> list[dict]:
    """Games in this season, strictly before `week`, with a final score on both sides."""
    rows = conn.execute(
        """
        SELECT home_team, away_team, home_score, away_score
        FROM games
        WHERE season = ? AND week < ? AND home_score IS NOT NULL AND away_score IS NOT NULL
        """,
        (season, week),
    ).fetchall()
    return [dict(row) for row in rows]


def team_recent_games(
    conn: sqlite3.Connection, season: int, team: str, before_week: int, limit: int = 5
) -> list[dict]:
    """Up to `limit` most recent completed games for `team` in `season`, strictly before
    `before_week` (most recent first) — used for standard-tier "recent form"."""
    rows = conn.execute(
        """
        SELECT week, home_team, away_team, home_score, away_score
        FROM games
        WHERE season = ? AND week < ? AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND (home_team = ? OR away_team = ?)
        ORDER BY week DESC
        LIMIT ?
        """,
        (season, before_week, team, team, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def head_to_head_games(
    conn: sqlite3.Connection, season: int, week: int, team_a: str, team_b: str, limit: int = 5
) -> list[dict]:
    """Up to `limit` most recent completed meetings between two teams, across any season
    already cached locally, strictly before (season, week) — best-effort (DESIGN §6):
    only as complete as whatever seasons have been synced into this database."""
    rows = conn.execute(
        """
        SELECT season, week, home_team, away_team, home_score, away_score
        FROM games
        WHERE (season < ? OR (season = ? AND week < ?))
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND ((home_team = ? AND away_team = ?) OR (home_team = ? AND away_team = ?))
        ORDER BY season DESC, week DESC
        LIMIT ?
        """,
        (season, season, week, team_a, team_b, team_b, team_a, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def game_situational_market_fields(
    conn: sqlite3.Connection, season: int, week: int
) -> dict[str, dict]:
    """Rest days, divisional/surface/roof, and betting-line fields for this week's games,
    straight from the cached nflverse schedule row — game_id -> field dict."""
    rows = conn.execute(
        """
        SELECT game_id, home_rest, away_rest, div_game, roof, surface,
               spread_line, home_moneyline, away_moneyline, total_line
        FROM games WHERE season = ? AND week = ?
        """,
        (season, week),
    ).fetchall()
    return {row["game_id"]: dict(row) for row in rows}


def full_schedule_from_db(conn: sqlite3.Connection, season: int) -> list[Game]:
    rows = conn.execute(
        """
        SELECT game_id, season, week, home_team, away_team, kickoff_ts
        FROM games WHERE season = ?
        ORDER BY kickoff_ts, game_id
        """,
        (season,),
    ).fetchall()
    return [dict(row) for row in rows]  # type: ignore[misc]


def week_sync_is_final(conn: sqlite3.Connection, season: int, week: int) -> bool:
    row = conn.execute(
        "SELECT synced_final FROM week_sync WHERE season = ? AND week = ?",
        (season, week),
    ).fetchone()
    return bool(row and row["synced_final"])


def set_week_synced_final(conn: sqlite3.Connection, season: int, week: int, synced_final: bool) -> None:
    conn.execute(
        """
        INSERT INTO week_sync (season, week, synced_final, last_synced_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (season, week) DO UPDATE SET
            synced_final = excluded.synced_final,
            last_synced_at = excluded.last_synced_at
        """,
        (season, week, int(synced_final), _now()),
    )
    conn.commit()


def insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    run_type: str,
    season: int,
    week: int,
    agent_design: str,
    prompt_variant: str,
    model: str,
    context_level: str,
    status: str = "ok",
) -> None:
    conn.execute(
        """
        INSERT INTO runs (run_id, run_type, season, week, agent_design, prompt_variant,
                           model, context_level, created_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, run_type, season, week, agent_design, prompt_variant, model, context_level, _now(), status),
    )
    conn.commit()


def insert_picks(conn: sqlite3.Connection, run_id: str, picks: list[Pick]) -> None:
    conn.executemany(
        """
        INSERT INTO picks (run_id, game_id, predicted_winner, win_probability, confidence)
        VALUES (:run_id, :game_id, :predicted_winner, :win_probability, :confidence)
        """,
        [{**pick, "run_id": run_id} for pick in picks],
    )
    conn.commit()


def games_with_results(conn: sqlite3.Connection, season: int, week: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT game_id, home_team, away_team, home_score, away_score
        FROM games WHERE season = ? AND week = ?
        """,
        (season, week),
    ).fetchall()
    return [dict(row) for row in rows]


def get_live_run(conn: sqlite3.Connection, season: int, week: int) -> dict | None:
    """The most recent `recommend` run for this (season, week), if any."""
    row = conn.execute(
        """
        SELECT run_id, agent_design, prompt_variant, model, context_level, status
        FROM runs
        WHERE run_type = 'live' AND season = ? AND week = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (season, week),
    ).fetchone()
    return dict(row) if row else None


def get_picks_for_run(conn: sqlite3.Connection, run_id: str) -> list[Pick]:
    rows = conn.execute(
        """
        SELECT game_id, predicted_winner, win_probability, confidence
        FROM picks WHERE run_id = ?
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]  # type: ignore[misc]


def upsert_score(conn: sqlite3.Connection, run_id: str, game_id: str, correct: bool, points_earned: int) -> None:
    """Idempotent — safe to re-run as more games in a week go final."""
    conn.execute(
        """
        INSERT INTO scores (run_id, game_id, correct, points_earned)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (run_id, game_id) DO UPDATE SET
            correct = excluded.correct,
            points_earned = excluded.points_earned
        """,
        (run_id, game_id, int(correct), points_earned),
    )
    conn.commit()


def live_season_points(conn: sqlite3.Connection, season: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(points_earned), 0) AS total
        FROM scores JOIN runs USING (run_id)
        WHERE runs.run_type = 'live' AND runs.season = ?
        """,
        (season,),
    ).fetchone()
    return row["total"]


def get_backtest_run(
    conn: sqlite3.Connection,
    season: int,
    week: int,
    agent_design: str,
    prompt_variant: str,
    model: str,
    context_level: str,
) -> dict | None:
    """The `runs` row for this exact backtest grid cell, if one already exists — lets the
    harness skip a grid cell it already completed rather than duplicate `runs`/`scores` rows
    on a resumed/re-run sweep."""
    row = conn.execute(
        """
        SELECT run_id FROM runs
        WHERE run_type = 'backtest' AND season = ? AND week = ?
          AND agent_design = ? AND prompt_variant = ? AND model = ? AND context_level = ?
        LIMIT 1
        """,
        (season, week, agent_design, prompt_variant, model, context_level),
    ).fetchone()
    return dict(row) if row else None


def season_summary_rows(conn: sqlite3.Connection, seasons: list[int]) -> list[dict]:
    """`season_summary` view rows for these seasons only (DESIGN §9.2/§9.3) — the raw
    per-season, per-config points/rank that `aggregation.py` rolls up across seasons."""
    placeholders = ",".join("?" * len(seasons))
    rows = conn.execute(
        f"SELECT * FROM season_summary WHERE season IN ({placeholders})", seasons
    ).fetchall()
    return [dict(row) for row in rows]


def backtest_weekly_points(conn: sqlite3.Connection, season: int) -> list[dict]:
    """Per-config, per-week backtest points for one season (DESIGN §9.4's season trend
    chart) -- the harness always runs a full season, so summing `picks`/`scores` by
    `runs.week` reconstructs the week-by-week series without a dedicated running total."""
    rows = conn.execute(
        """
        SELECT week, agent_design, prompt_variant, model, context_level,
               SUM(points_earned) AS points
        FROM scores JOIN runs USING (run_id)
        WHERE runs.run_type = 'backtest' AND runs.season = ?
        GROUP BY week, agent_design, prompt_variant, model, context_level
        ORDER BY week
        """,
        (season,),
    ).fetchall()
    return [dict(row) for row in rows]


def backtest_calibration_rows(
    conn: sqlite3.Connection,
    seasons: list[int],
    agent_design: str,
    prompt_variant: str,
    model: str,
    context_level: str,
) -> list[dict]:
    """(win_probability, correct) pairs across every backtest pick made by one exact
    config over these seasons (DESIGN §9.4's per-config calibration/reliability chart)."""
    placeholders = ",".join("?" * len(seasons))
    rows = conn.execute(
        f"""
        SELECT picks.win_probability AS win_probability, scores.correct AS correct
        FROM picks
        JOIN scores USING (run_id, game_id)
        JOIN runs USING (run_id)
        WHERE runs.run_type = 'backtest' AND runs.season IN ({placeholders})
          AND runs.agent_design = ? AND runs.prompt_variant = ?
          AND runs.model = ? AND runs.context_level = ?
        """,
        [*seasons, agent_design, prompt_variant, model, context_level],
    ).fetchall()
    return [dict(row) for row in rows]


def live_weekly_points(conn: sqlite3.Connection, season: int) -> list[dict]:
    """Per-week points earned by the live `recommend` config so far this season (DESIGN
    §8's season cumulative-points line chart) -- only weeks `pickem score` has graded."""
    rows = conn.execute(
        """
        SELECT week, SUM(points_earned) AS points
        FROM scores JOIN runs USING (run_id)
        WHERE runs.run_type = 'live' AND runs.season = ?
        GROUP BY week
        ORDER BY week
        """,
        (season,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_cached_response(conn: sqlite3.Connection, cache_key: str) -> str | None:
    row = conn.execute(
        "SELECT response_json FROM llm_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    return row["response_json"] if row else None


def set_cached_response(conn: sqlite3.Connection, cache_key: str, game_id: str, response_json: str) -> None:
    conn.execute(
        """
        INSERT INTO llm_cache (cache_key, game_id, response_json, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (cache_key) DO NOTHING
        """,
        (cache_key, game_id, response_json, _now()),
    )
    conn.commit()
