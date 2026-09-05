"""FastAPI dependencies."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from .config import Settings, get_settings
from .db import connect


def db_connection() -> Iterator[sqlite3.Connection]:
    """One SQLite connection per request, closed on the way out.

    Opening a connection costs microseconds and sidesteps the thread-affinity
    rules that make a shared sqlite3 connection unsafe across FastAPI's sync
    threadpool.
    """
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(db_connection)]
Config = Annotated[Settings, Depends(get_settings)]
