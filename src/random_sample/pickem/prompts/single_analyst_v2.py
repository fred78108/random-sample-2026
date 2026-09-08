"""Single-analyst prompt set, structured-factor variant (Milestone H). Same "analyst" role
and required output as `single_analyst_v1` -- only the instruction wording changes, forcing
the model to explicitly work through each context category (form/stats, situational, market,
head-to-head/injuries where present) before committing to a pick, rather than judging the raw
JSON blob in one unstructured pass. Reused by `ensemble_self_consistency` the same way v1 is
(same role name, same harmless no-op for that strategy's plain-schema call)."""

from random_sample.pickem.prompts import register_prompt_set

ANALYST_TEMPLATE = """You are an NFL analyst predicting the outcome of a single game.

Matchup: {away_team} (away) at {home_team} (home)

Known context (JSON):
{features_json}

Work through the available context one category at a time before deciding:
1. Team form/stats (record, point differential, recent form, EPA-per-play if present) — which
   team does this favor, and how strongly?
2. Situational factors (rest days, home field, divisional game, venue) if present — which team
   does this favor, and how strongly?
3. Market signal (spread, total, moneylines) if present — which team does the market favor, and
   by how much?
4. Head-to-head history and injury reports if present — any material edge either way?

Only some of these categories may actually be present in the JSON above (context depth varies by
run) — skip any that aren't there rather than guessing. Weigh whichever categories are present
against each other, then decide which team is more likely to win and estimate your confidence as
a win probability for that team. Also predict the final score for each team. Respond with the
predicted winner's team abbreviation, a win probability between 0.5 and 1.0, a predicted final
score for the home team, a predicted final score for the away team, and a one- or two-sentence
rationale that reflects the categories you weighed."""

register_prompt_set(
    {
        "id": "single_analyst_v2",
        "templates": {"analyst": ANALYST_TEMPLATE},
    }
)
