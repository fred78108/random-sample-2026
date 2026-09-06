"""Debate prompt set: two opposing advocates argue for their assigned team using the full
context, then a judge decides (DESIGN §3.3)."""

from random_sample.pickem.prompts import register_prompt_set

ADVOCATE_HOME_TEMPLATE = """You are an NFL debate advocate. Your job is to argue that the
HOME team, {home_team}, will beat {away_team} — find the strongest possible case for
{home_team} winning, even if the data is mixed.

Matchup: {away_team} (away) at {home_team} (home)

Known context (JSON):
{features_json}

Make your strongest case for {home_team} winning. Respond with {home_team} as the predicted
winner, a win probability between 0.5 and 1.0 reflecting how strong your case actually is,
and a one- or two-sentence rationale."""

ADVOCATE_AWAY_TEMPLATE = """You are an NFL debate advocate. Your job is to argue that the
AWAY team, {away_team}, will beat {home_team} — find the strongest possible case for
{away_team} winning, even if the data is mixed.

Matchup: {away_team} (away) at {home_team} (home)

Known context (JSON):
{features_json}

Make your strongest case for {away_team} winning. Respond with {away_team} as the predicted
winner, a win probability between 0.5 and 1.0 reflecting how strong your case actually is,
and a one- or two-sentence rationale."""

JUDGE_TEMPLATE = """You are an NFL judge. Two advocates have each argued for a different
team in this game. Weigh their arguments on their merits — a confidently stated case is not
necessarily a strong one — and decide who actually wins.

Matchup: {away_team} (away) at {home_team} (home)

Home advocate's case for {home_team}: {home_case_json}
Away advocate's case for {away_team}: {away_case_json}

Decide which team is actually more likely to win and estimate a win probability between 0.5
and 1.0 for that team. Respond with the predicted winner's team abbreviation, that
probability, and a one- or two-sentence rationale for your decision."""

register_prompt_set(
    {
        "id": "debate_v1",
        "templates": {
            "advocate_home": ADVOCATE_HOME_TEMPLATE,
            "advocate_away": ADVOCATE_AWAY_TEMPLATE,
            "judge": JUDGE_TEMPLATE,
        },
    }
)
