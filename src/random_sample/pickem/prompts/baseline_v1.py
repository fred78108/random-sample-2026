"""Placeholder prompt set for the non-LLM baseline strategies (DESIGN §9.3). Empty
templates — `naive_favorite`/`market_favorite` declare `required_roles = ()` so the
compatibility check in `nodes/prediction.py` never looks a template up here, but `runs`/
`RunConfig` still require a `prompt_variant` value, so this exists to be that value rather
than borrowing an unrelated real prompt set's id."""

from random_sample.pickem.prompts import register_prompt_set

register_prompt_set(
    {
        "id": "baseline_v1",
        "templates": {},
    }
)
