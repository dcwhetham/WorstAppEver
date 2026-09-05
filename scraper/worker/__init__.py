"""Isolated scraper worker.

No HTTP server, no UI, no config file. Reads its behaviour from the `settings`
table and its work from `scrape_jobs`, both in the shared SQLite database. Kill
this container and the dashboard keeps serving the archive with jobs sitting in
`queued`.
"""

__version__ = "0.1.0"
