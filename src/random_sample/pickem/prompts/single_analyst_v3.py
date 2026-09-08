"""Single-analyst prompt set, market-anchored variant (Milestone H). Same "analyst" role and
required output as `single_analyst_v1`. Motivation: Milestone F's promoted config
(`single_analyst_v1`) beat the naive baseline every season but only tracked the market
baseline within noise (mean margin +3.7 pts over 2023-2025, losing outright in 2 of 3 seasons)
-- despite the rich-tier context already including the market's own moneylines/spread in the
same JSON blob v1 sees. v1 never tells the model *how* to use that signal, so nothing stops it
from being outweighed or ignored relative to noisier stat/situational features. This variant
makes the market line an explicit starting point (anchor) and asks the model to only move away
from it when the rest of the context gives a genuinely differentiated read, rather than
re-deriving a probability from scratch. Reused by `ensemble_self_consistency` the same way v1
and v2 are (same role name, harmless no-op for that strategy's plain-schema call)."""

from random_sample.pickem.prompts import register_prompt_set

ANALYST_TEMPLATE = """You are an NFL analyst predicting the outcome of a single game.

Matchup: {away_team} (away) at {home_team} (home)

Known context (JSON):
{features_json}

If a "market" section with moneylines is present in the context above, start from what it
implies: convert each team's moneyline to an implied win probability (for a favorite, negative
odds of -X imply X/(X+100); for an underdog, positive odds of +X imply 100/(X+100)), then
normalize the two implied probabilities so they sum to 1 -- that normalized number is your
starting estimate for the favored team's win probability. The market is a strong, well-calibrated
baseline (NFL closing lines are historically hard to beat), so only move your final probability
away from that starting estimate when the rest of the context (form, EPA, situational factors,
head-to-head, injuries) gives a genuinely differentiated signal the market may be underweighting
-- and even then, prefer a small adjustment over a large one. If no "market" section is present,
fall back to weighing whatever context categories are present on their own merits, the same as
you would without market data.

Decide which team is more likely to win and estimate your confidence as a win probability for
that team. Also predict the final score for each team. Respond with the predicted winner's team
abbreviation, a win probability between 0.5 and 1.0, a predicted final score for the home team, a
predicted final score for the away team, and a one- or two-sentence rationale that states your
market-implied starting estimate (if any) and what, if anything, moved you away from it."""

register_prompt_set(
    {
        "id": "single_analyst_v3",
        "templates": {"analyst": ANALYST_TEMPLATE},
    }
)
