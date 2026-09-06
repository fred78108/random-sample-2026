"""Single-analyst prompt set. Reused by ensemble_self_consistency (same "analyst" role)."""

from random_sample.pickem.prompts import register_prompt_set

ANALYST_TEMPLATE = """You are an NFL analyst predicting the outcome of a single game.

Matchup: {away_team} (away) at {home_team} (home)

Known context (JSON):
{features_json}

Based only on this context, decide which team is more likely to win and estimate your
confidence as a win probability for that team. Respond with the predicted winner's team
abbreviation, a win probability between 0.5 and 1.0, and a one- or two-sentence rationale."""

register_prompt_set(
    {
        "id": "single_analyst_v1",
        "templates": {"analyst": ANALYST_TEMPLATE},
    }
)
