"""Data access for the API layer.

Routers call these modules; nothing here imports FastAPI, so the same queries
are reusable from a CLI or a test without spinning up an app.
"""

from . import accounts, jobs, media

__all__ = ["accounts", "jobs", "media"]
