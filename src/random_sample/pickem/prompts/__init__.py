"""PromptSet and registry. See specs/pickem-agent/DESIGN.md §3.3."""

from __future__ import annotations

from typing import TypedDict


class PromptSet(TypedDict):
    id: str
    templates: dict[str, str]   # role -> prompt template


PROMPT_SET_REGISTRY: dict[str, PromptSet] = {}


def register_prompt_set(prompt_set: PromptSet) -> PromptSet:
    PROMPT_SET_REGISTRY[prompt_set["id"]] = prompt_set
    return prompt_set


def load_all() -> None:
    """Import every prompt module so its registration side-effect runs."""
    from random_sample.pickem.prompts import (  # noqa: F401
        baseline_v1,
        debate_v1,
        single_analyst_v1,
        single_analyst_v2,
        single_analyst_v3,
        specialist_v1,
    )
