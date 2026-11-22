# Published Analytics F12 preparer

This directory contains the exact Python dependency closure required by
`analytics_service/f12_export.py` from `matumolise/modera-modelo` commit
`94ff3a82ad3581e74cb6f97449adda264972c61b` (published short ref `94ff3a8`).

The sources are intentionally unmodified. The backend executes them in an
isolated subprocess through `app.services.f12_published_runner`. Runtime
`tzdata` is pinned by the backend's `requirements.txt`; the upstream commit's
preparation requirements also pin `tzdata==2026.5`.

The 2026-11-21 contract ZIP was verified with `tzdata==2026.6`. Its fixture
identities therefore differ from the identities produced by this backend's
existing 2026.5 series binding. Do not update tzdata without an explicit
series-migration policy.
