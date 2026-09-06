"""Default-week resolution. See specs/pickem-agent/DESIGN.md §5.1."""

from __future__ import annotations

from datetime import datetime, timezone

from random_sample.pickem.state import Game


def current_week(schedule: list[Game], now: datetime) -> tuple[int, int]:
    """(season, week) of the earliest game with kickoff_ts >= now; falls back to the
    last available (season, week) in the schedule if now is past everything loaded."""
    if not schedule:
        raise ValueError("current_week: schedule is empty")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    def kickoff(game: Game) -> datetime:
        return datetime.fromisoformat(game["kickoff_ts"])

    upcoming = [g for g in schedule if kickoff(g) >= now]
    game = min(upcoming, key=kickoff) if upcoming else max(schedule, key=kickoff)
    return game["season"], game["week"]


_LAST_REGULAR_SEASON_WEEK = 18


def previous_week(season: int, week: int) -> tuple[int, int]:
    """(season, week) immediately preceding the given one, wrapping to the prior
    season's last regular-season week at week 1. v1 assumes an 18-week regular
    season (DESIGN §5.1)."""
    if week <= 1:
        return season - 1, _LAST_REGULAR_SEASON_WEEK
    return season, week - 1
