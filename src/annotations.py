"""Subjective annotation layer for the local pivot (Priority 2 / FR-PIVOT-3).

Decoupled from the immutable fact layer (industry-standard split): the user's
trade-system tags / quality scores / review notes live in a hand-editable CSV
(`data/annotations.csv`, gitignored — real trades), keyed by the ENTRY leg's IB
`trade_id` (stable across re-fetch + re-pairing). A `setup_tag` taxonomy + an
order_ref alias map live in `config/pivot_tags.json` (committed template).

setup_tag resolution is three-tier (FR-PIVOT-3c):
    1. explicit annotation in annotations.csv     (manual orders, filled later)
  > 2. order_ref via the alias map                 (quant orders, auto from Flex)
  > 3. "untagged"                                   (explicit, never hidden)

Pure-ish: only file I/O (csv/json), no network. `--tag-template` (in pivot.py)
calls write_tag_template to pre-generate/refresh the CSV so the user fills tags
in Excel, then re-runs the pivot.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .constants import (
    ANNOTATIONS_BAK_DIR,
    ANNOTATIONS_BAK_KEEP,
    ANNOTATIONS_PATH,
    PIVOT_TAGS_PATH,
    UNTAGGED,
)

log = logging.getLogger("tradelens.annotations")


class AnnotationSchemaError(Exception):
    """Raised when annotations.csv exists but its header is missing the key
    column (open_trade_id). Excel users can accidentally delete/rename columns
    when saving — failing loud here beats silently dropping every annotation."""

# annotations.csv layout: editable cols first (user fills these in Excel),
# then read-only ref_* cols (refreshed each --tag-template run, for human ID).
_EDITABLE_COLS = ("open_trade_id", "setup_tag", "score", "notes", "planned_stop")
_REF_COLS = ("ref_open_date", "ref_open_time", "ref_underlying",
             "ref_direction", "ref_pnl_usd", "ref_round_trips")
ANNOTATION_COLUMNS = _EDITABLE_COLS + _REF_COLS


@dataclass(frozen=True)
class Annotation:
    """One user-supplied annotation row, keyed by entry-leg trade_id."""

    setup_tag: str            # "" if not yet filled
    score: str                # raw cell ("" / "7" / "8.5"); parse via score_value
    notes: str
    planned_stop: str = ""    # FR-PIVOT-10: initial stop price; parse via planned_stop_value

    @property
    def score_value(self) -> float | None:
        try:
            return float(self.score) if self.score.strip() else None
        except ValueError:
            return None

    @property
    def planned_stop_value(self) -> float | None:
        """Parsed initial planned stop price, or None if blank / non-numeric.

        Same lenient parse as score_value: an Excel text cell or empty string
        yields None (→ no R for this entry), never an exception."""
        try:
            return float(self.planned_stop) if self.planned_stop.strip() else None
        except ValueError:
            return None


@dataclass(frozen=True)
class TagConfig:
    """setup_tag taxonomy (code -> display) + order_ref alias map (ref -> code)."""

    setup_tags: dict[str, str]
    aliases: dict[str, str]

    def display(self, code: str) -> str:
        """Human label for a setup_tag code; falls back to the code itself."""
        if code == UNTAGGED:
            return "Untagged"
        return self.setup_tags.get(code, code)


def load_tag_config(path: str | Path = PIVOT_TAGS_PATH) -> TagConfig:
    """Read config/pivot_tags.json. Missing/blank file -> empty config (graceful:
    everything just resolves to its raw code / untagged)."""
    p = Path(path)
    if not p.exists():
        log.warning("pivot_tags.json not found at %s — using empty tag config", p)
        return TagConfig({}, {})
    raw = json.loads(p.read_text(encoding="utf-8"))
    setup_tags = {str(k): str(v) for k, v in (raw.get("setup_tags") or {}).items()}
    aliases = {str(k): str(v) for k, v in (raw.get("order_ref_aliases") or {}).items()}
    return TagConfig(setup_tags, aliases)


def load_annotations(path: str | Path = ANNOTATIONS_PATH) -> dict[str, Annotation]:
    """Read annotations.csv -> {open_trade_id: Annotation}. Missing file -> {}.

    Only the editable columns are loaded; ref_* are throwaway display aids.
    Raises AnnotationSchemaError (R3) if the file exists but its header has
    no `open_trade_id` column — that means Excel saved with a broken schema
    and we'd silently lose every annotation; let the user restore from
    data/annotations.bak/ instead."""
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, Annotation] = {}
    with p.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        # R3: schema validation — the key column must be present, else fail loud.
        if "open_trade_id" not in (reader.fieldnames or []):
            raise AnnotationSchemaError(
                f"{p} is missing the 'open_trade_id' column "
                f"(header={reader.fieldnames}). "
                f"Restore a backup from {ANNOTATIONS_BAK_DIR} and re-run."
            )
        for row in reader:
            tid = (row.get("open_trade_id") or "").strip()
            if not tid:
                continue
            out[tid] = Annotation(
                setup_tag=(row.get("setup_tag") or "").strip(),
                score=(row.get("score") or "").strip(),
                notes=(row.get("notes") or "").strip(),
                # Old files (pre-FR-PIVOT-10) lack the column -> row.get None -> "".
                planned_stop=(row.get("planned_stop") or "").strip(),
            )
    return out


def backup_annotations(
    src: str | Path = ANNOTATIONS_PATH,
    bak_dir: str | Path = ANNOTATIONS_BAK_DIR,
    keep: int = ANNOTATIONS_BAK_KEEP,
) -> Path | None:
    """R1: snapshot the current annotations.csv to bak_dir/{timestamp}.csv,
    then FIFO-prune to the last `keep` backups. Returns the backup path, or
    None if there's nothing to back up (file doesn't exist).

    Bounded disk use: 20 × ~100 KB = ~2 MB at paper-scale, growth-bounded."""
    src_p = Path(src)
    if not src_p.exists():
        return None
    bak_p = Path(bak_dir)
    bak_p.mkdir(parents=True, exist_ok=True)
    # Millisecond precision so rapid successive backups (e.g. tests) stay distinct.
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S-%f")[:-3]   # drop trailing µs
    out = bak_p / f"{ts}.csv"
    shutil.copy2(src_p, out)
    # FIFO prune: keep most recent N by mtime.
    backups = sorted(bak_p.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
    for stale in backups[keep:]:
        try:
            stale.unlink()
        except OSError:                                       # best-effort
            log.warning("could not prune backup %s", stale)
    return out


def resolve_setup_tag(
    open_trade_id: str,
    order_ref: str | None,
    annotations: dict[str, Annotation],
    cfg: TagConfig,
) -> str:
    """Three-tier setup_tag (FR-PIVOT-3c). Returns a code (or free string), else
    UNTAGGED. An empty explicit tag does NOT win — it falls through to the alias."""
    ann = annotations.get(open_trade_id)
    if ann and ann.setup_tag:
        return ann.setup_tag
    if order_ref and order_ref in cfg.aliases:
        return cfg.aliases[order_ref]
    return UNTAGGED


# --- --tag-template generation (FR-PIVOT-3d) ---

def _aggregate_by_entry(round_trips: Iterable) -> dict[str, dict]:
    """Collapse round-trips to one record per entry leg (annotation granularity).

    A single entry can split into several round-trips (partial closes); they
    share one annotation, so the template shows one row per open_trade_id with
    summed PnL + a round-trip count.
    """
    agg: dict[str, dict] = {}
    for rt in round_trips:
        a = agg.get(rt.open_trade_id)
        if a is None:
            a = agg[rt.open_trade_id] = {
                "open_date": rt.open_date, "open_time": rt.open_time,
                "underlying": rt.underlying, "direction": rt.direction,
                "pnl": 0.0, "n": 0,
            }
        a["pnl"] += rt.pnl_usd or 0.0
        a["n"] += 1
    return agg


def write_tag_template(
    round_trips: Iterable,
    out_path: str | Path = ANNOTATIONS_PATH,
) -> dict:
    """Pre-generate / refresh annotations.csv (FR-PIVOT-3d).

    PRESERVES any already-filled setup_tag/score/notes (R4 — never discards
    user's work), APPENDS rows for entries not yet listed, REFRESHES the
    read-only ref_* columns from current data. Existing annotations whose
    entry is no longer in the data are kept with blank ref_* columns.

    Robustness (DATA_ARCHITECTURE §7):
    - R1: snapshots current file to data/annotations.bak/{ts}.csv before write
    - R2: writes atomically via tmp file + Path.replace (POSIX rename)
    - R3: load_annotations validates header schema; raises AnnotationSchemaError

    Returns counts (path, total, new, preserved, orphaned, backup).
    """
    out_path = Path(out_path)
    existing = load_annotations(out_path)            # R3 schema check happens here
    backup_path = backup_annotations(out_path)       # R1 (no-op if file absent)
    agg = _aggregate_by_entry(round_trips)

    # Order: current entries chronologically (newest review surface first kept
    # ascending for stable diffs), then any orphaned existing annotations.
    ordered_ids = sorted(agg, key=lambda t: (agg[t]["open_date"], agg[t]["open_time"], t))
    orphan_ids = [t for t in existing if t not in agg]

    n_new = 0
    rows: list[dict] = []
    for tid in ordered_ids:
        a = agg[tid]
        ann = existing.get(tid)
        if ann is None:
            n_new += 1
        rows.append({
            "open_trade_id": tid,
            "setup_tag": ann.setup_tag if ann else "",
            "score": ann.score if ann else "",
            "notes": ann.notes if ann else "",
            "planned_stop": ann.planned_stop if ann else "",   # R4: preserve user's stop
            "ref_open_date": a["open_date"],
            "ref_open_time": a["open_time"],
            "ref_underlying": a["underlying"],
            "ref_direction": a["direction"],
            "ref_pnl_usd": f"{a['pnl']:.2f}",
            "ref_round_trips": a["n"],
        })
    for tid in orphan_ids:                       # R4: keep user's work, no fresh ref
        ann = existing[tid]
        rows.append({
            "open_trade_id": tid, "setup_tag": ann.setup_tag,
            "score": ann.score, "notes": ann.notes,
            "planned_stop": ann.planned_stop,                  # R4: preserve on orphan rows too
            "ref_open_date": "", "ref_open_time": "", "ref_underlying": "",
            "ref_direction": "", "ref_pnl_usd": "", "ref_round_trips": "",
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # R2: write to tmp + atomic rename so a crash mid-write leaves the
    # previous file intact (preserved by R1 backup if rename succeeded but
    # something downstream blew up).
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ANNOTATION_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out_path)

    return {
        "path": out_path, "total": len(rows), "new": n_new,
        "preserved": len(existing) - len(orphan_ids), "orphaned": len(orphan_ids),
        "backup": backup_path,
    }
