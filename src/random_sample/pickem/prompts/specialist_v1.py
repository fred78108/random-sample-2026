"""Specialist prompt set: stats/situational/market roles, each seeing only its own data
slice, plus a synthesis role that weighs all three (DESIGN §3.3). Shared by
`specialist_synthesis` (uses all four roles) and `specialist_deterministic` (uses the
three specialist roles only, per its `required_roles`)."""

from random_sample.pickem.prompts import register_prompt_set

STATS_TEMPLATE = """You are an NFL statistics analyst. You only see team performance data —
no situational or betting-market information.

Matchup: {away_team} (away) at {home_team} (home)

Stats context (JSON):
{features_json}

Based only on this statistical context, decide which team is more likely to win and estimate
your confidence as a win probability for that team. Respond with the predicted winner's team
abbreviation, a win probability between 0.5 and 1.0, and a one- or two-sentence rationale."""

SITUATIONAL_TEMPLATE = """You are an NFL situational analyst. You only see rest days,
home-field advantage, divisional-game status, and venue information — no team performance
stats or betting-market information.

Matchup: {away_team} (away) at {home_team} (home)

Situational context (JSON):
{features_json}

Based only on this situational context, decide which team is more likely to win and estimate
your confidence as a win probability for that team. Respond with the predicted winner's team
abbreviation, a win probability between 0.5 and 1.0, and a one- or two-sentence rationale."""

MARKET_TEMPLATE = """You are an NFL betting-market analyst. You only see the market's own
spread, total, and moneyline lines for this game — no team performance stats or situational
information.

Matchup: {away_team} (away) at {home_team} (home)

Market context (JSON):
{features_json}

Based only on this market context, decide which team the market favors and estimate your
confidence as a win probability for that team. Respond with the predicted winner's team
abbreviation, a win probability between 0.5 and 1.0, and a one- or two-sentence rationale."""

SYNTHESIS_TEMPLATE = """You are an NFL synthesis analyst. Three specialists have each
independently analyzed this game from a different angle and reported their own prediction.
Weigh their assessments and produce one final prediction.

Matchup: {away_team} (away) at {home_team} (home)

Stats specialist: {stats_json}
Situational specialist: {situational_json}
Market specialist: {market_json}

Decide which team is actually more likely to win and estimate your confidence as a win
probability for that team. Respond with the predicted winner's team abbreviation, a win
probability between 0.5 and 1.0, and a one- or two-sentence rationale synthesizing the
three specialists' views."""

register_prompt_set(
    {
        "id": "specialist_v1",
        "templates": {
            "stats": STATS_TEMPLATE,
            "situational": SITUATIONAL_TEMPLATE,
            "market": MARKET_TEMPLATE,
            "synthesis": SYNTHESIS_TEMPLATE,
        },
    }
)
