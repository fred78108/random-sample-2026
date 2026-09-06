"""Schedule Agent node. Resolves the week's real game slate via nflverse."""

from __future__ import annotations

import sqlite3
from typing import Callable

from random_sample.pickem import nflverse_client
from random_sample.pickem.state import PickemState


def build_schedule_node(conn: sqlite3.Connection) -> Callable[[PickemState], dict]:
    def schedule_node(state: PickemState) -> dict:
        games = nflverse_client.ensure_week(conn, state["season"], state["week"])
        return {"games": games}

    return schedule_node
