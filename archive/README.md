# /archive

Your media. Nothing in here is committed (see `.gitignore`) and nothing in here
is owned by the application.

## Layout

One folder per account, named after the account handle, with media split by type:

```
archive/
├── someaccount/
│   ├── photos/
│   │   ├── 2024-05-01_C6xYzAbCdEf.jpg
│   │   └── 2024-05-03_C6xYzAbCdEg.jpg
│   └── videos/
│       └── 2024-04-28_C6xYzAbCdEh.mp4
└── another.handle/
    ├── photos/
    └── videos/
```

## Files stay raw

There is no transcoding step, no thumbnail cache, no sidecar metadata, and no
container format wrapping the originals. A `.jpg` in `photos/` is the exact bytes
that were downloaded, so you can open this tree in Finder, Explorer, Kodi or
`mpv` and it behaves like any other media folder — with or without this
application running.

That constraint is the reason the dashboard serves media as raw bytes with HTTP
range support rather than generating derivatives: a second copy in a cache is a
second thing that can disagree with the disk.

## Adding files by hand

Drop them into the right subfolder and run **Scan archive** from the dashboard
(or `POST /api/scan`). The scanner walks the tree, classifies by
extension, hashes anything new or changed, and indexes it. Manually added files
get a `media_files` row with no `source_provider`, which is how the UI knows they
were not scraped.

Creating a bare `somename/` folder is enough to register a new account — the
scanner discovers accounts from directory names on its next pass.

## Deleting files by hand

Delete them and rescan. The scanner marks the row `is_missing = 1` rather than
removing it, and the scraper treats a missing-but-known file as "the user got rid
of this on purpose" and will not re-download it. Purge the row from the account
detail view if you do want it fetched again.

## Renaming accounts

Rename the folder and set `accounts.archive_path` to the new directory name. The
account keeps its identity, history, links and counters while its files live
under a different name — which is what you want when a handle changes on the
platform but the archive should not churn.
