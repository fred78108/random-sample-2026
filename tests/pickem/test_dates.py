from __future__ import annotations

from datetime import datetime, timezone

import pytest

from random_sample.pickem import dates


def _game(season: int, week: int, kickoff_ts: str) -> dict:
    return {
        "game_id": f"{season}_{week}",
        "season": season,
        "week": week,
        "home_team": "AAA",
        "away_team": "BBB",
        "kickoff_ts": kickoff_ts,
    }


def test_previous_week_same_season():
    assert dates.previous_week(2026, 5) == (2026, 4)


def test_previous_week_wraps_to_prior_season_week_18():
    assert dates.previous_week(2026, 1) == (2025, 18)


def test_recommend_and_score_defaults_differ():
    """DESIGN §5.1: `recommend` defaults to `current_week`; `score` defaults to
    `current_week` minus one. Guards against a regression that collapses the two."""
    schedule = [
        _game(2026, 1, "2026-09-10T13:00:00+00:00"),
        _game(2026, 2, "2026-09-17T13:00:00+00:00"),
    ]
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)

    recommend_default = dates.current_week(schedule, now)
    score_default = dates.previous_week(*recommend_default)

    assert recommend_default != score_default
    assert recommend_default == (2026, 2)
    assert score_default == (2026, 1)


def test_current_week_empty_schedule_raises():
    """No schedule loaded at all — can't guess a week, so this should error clearly rather
    than guess (DESIGN §5.1)."""
    with pytest.raises(ValueError):
        dates.current_week([], datetime(2026, 9, 10, tzinfo=timezone.utc))


def test_current_week_pre_season_returns_earliest_week():
    """`now` before every kickoff in the schedule (e.g. offseason) resolves to the first
    upcoming week, not an error — v1 doesn't special-case the offseason (DESIGN §5.1)."""
    schedule = [
        _game(2026, 1, "2026-09-10T13:00:00+00:00"),
        _game(2026, 2, "2026-09-17T13:00:00+00:00"),
    ]
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)

    assert dates.current_week(schedule, now) == (2026, 1)


def test_current_week_mid_week_stays_on_current_week():
    """`now` between two of a week's kickoffs (e.g. Tuesday of week 1, after Thursday night
    but before Sunday) should still resolve to week 1, since week 1 games are still upcoming."""
    schedule = [
        _game(2026, 1, "2026-09-10T00:00:00+00:00"),  # Thursday night, already kicked off
        _game(2026, 1, "2026-09-13T17:00:00+00:00"),  # Sunday, still upcoming
        _game(2026, 2, "2026-09-17T13:00:00+00:00"),
    ]
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)  # Friday, mid-week-1

    assert dates.current_week(schedule, now) == (2026, 1)


def test_current_week_rolls_over_after_last_game_of_the_week():
    """Once every game in week 1 has kicked off (e.g. right after Monday Night Football
    ends), `current_week` should already point at week 2 — the case DESIGN §5.1 calls out
    as the reason `score` needs its own `previous_week` default."""
    schedule = [
        _game(2026, 1, "2026-09-10T00:00:00+00:00"),
        _game(2026, 1, "2026-09-15T00:00:00+00:00"),  # Monday night, week 1's last kickoff
        _game(2026, 2, "2026-09-17T13:00:00+00:00"),
    ]
    now = datetime(2026, 9, 16, tzinfo=timezone.utc)  # Tuesday, after MNF has kicked off

    assert dates.current_week(schedule, now) == (2026, 2)


def test_current_week_past_entire_schedule_falls_back_to_last_week():
    """`now` after every game in the loaded schedule (e.g. season already over) falls back
    to the last available week rather than raising (DESIGN §5.1)."""
    schedule = [
        _game(2026, 1, "2026-09-10T13:00:00+00:00"),
        _game(2026, 18, "2027-01-04T13:00:00+00:00"),
    ]
    now = datetime(2027, 6, 1, tzinfo=timezone.utc)

    assert dates.current_week(schedule, now) == (2026, 18)


def test_current_week_naive_datetime_is_treated_as_utc():
    """`now` without tzinfo must not crash comparing against tz-aware kickoff timestamps —
    `current_week` coerces it to UTC rather than raising."""
    schedule = [_game(2026, 1, "2026-09-10T13:00:00+00:00")]
    now = datetime(2026, 9, 1)  # naive

    assert dates.current_week(schedule, now) == (2026, 1)


def test_current_week_exact_kickoff_boundary_counts_as_upcoming():
    """`now` exactly equal to a kickoff timestamp counts as still-upcoming (`>=`), matching
    the DESIGN §5.1 docstring contract."""
    schedule = [
        _game(2026, 1, "2026-09-10T13:00:00+00:00"),
        _game(2026, 2, "2026-09-17T13:00:00+00:00"),
    ]
    now = datetime(2026, 9, 10, 13, 0, 0, tzinfo=timezone.utc)

    assert dates.current_week(schedule, now) == (2026, 1)
