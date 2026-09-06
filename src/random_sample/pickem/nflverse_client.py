"""Cache-first nflverse fetch. See specs/pickem-agent/DESIGN.md §5.3."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import nflreadpy as nfl
import polars as pl

from random_sample.pickem.db import repository
from random_sample.pickem.state import Game

# nflverse publishes gameday/gametime in US/Eastern local time.
_SCHEDULE_TZ = ZoneInfo("America/New_York")


def _kickoff_ts(gameday: str, gametime: str | None) -> str:
    time_part = gametime or "00:00"
    naive = datetime.strptime(f"{gameday} {time_part}", "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=_SCHEDULE_TZ).isoformat()


def _regular_season_games(schedule: pl.DataFrame) -> pl.DataFrame:
    return schedule.filter(pl.col("game_type") == "REG")


def _to_game_rows(schedule: pl.DataFrame) -> list[dict]:
    return [
        {
            "game_id": row["game_id"],
            "season": row["season"],
            "week": row["week"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "kickoff_ts": _kickoff_ts(row["gameday"], row["gametime"]),
            "home_score": row["home_score"],
            "away_score": row["away_score"],
            # Situational/market fields (DESIGN §6, Milestone C): already present on
            # nflverse's own schedule rows, so no separate fetch is needed.
            "home_rest": row["home_rest"],
            "away_rest": row["away_rest"],
            "div_game": row["div_game"],
            "roof": row["roof"],
            "surface": row["surface"],
            "spread_line": row["spread_line"],
            "home_moneyline": row["home_moneyline"],
            "away_moneyline": row["away_moneyline"],
            "total_line": row["total_line"],
        }
        for row in schedule.iter_rows(named=True)
    ]


def _week_is_final(schedule: pl.DataFrame, season: int, week: int) -> bool:
    week_games = schedule.filter((pl.col("season") == season) & (pl.col("week") == week))
    if week_games.is_empty():
        return False
    return bool(
        week_games.select(
            pl.col("home_score").is_not_null().all() & pl.col("away_score").is_not_null().all()
        ).item()
    )


def _refresh_season_cache(conn: sqlite3.Connection, season: int) -> None:
    schedule = _regular_season_games(nfl.load_schedules(seasons=[season]))
    repository.upsert_games(conn, _to_game_rows(schedule))
    for wk in schedule.select("week").unique().to_series().to_list():
        repository.set_week_synced_final(conn, season, wk, _week_is_final(schedule, season, wk))


def ensure_week(conn: sqlite3.Connection, season: int, week: int) -> list[Game]:
    """Local-first: only calls nflverse if this week isn't already fully resolved."""
    if not repository.week_sync_is_final(conn, season, week):
        _refresh_season_cache(conn, season)
    return repository.games_from_db(conn, season, week)


def ensure_season(conn: sqlite3.Connection, season: int) -> list[Game]:
    """Full-season schedule, refreshed if any week is still unresolved. Used to resolve
    the default --week/--season for `recommend`/`score` before either is known."""
    weeks = repository.full_schedule_from_db(conn, season)
    all_weeks_final = bool(weeks) and all(
        repository.week_sync_is_final(conn, season, w)
        for w in sorted({g["week"] for g in weeks})
    )
    if not all_weeks_final:
        _refresh_season_cache(conn, season)
    return repository.full_schedule_from_db(conn, season)
