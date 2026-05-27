"""TraderLens — IBKR trade sync (v1 IBKR adapter).

Priority 1 pipeline: Flex Query -> SQLite (full archive) -> csv export
(NQ/MNQ/ES/MES only) for the sister MTS project to ingest.
"""

__version__ = "0.1.0"
