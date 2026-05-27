"""Tests for annotations.csv robustness guarantees (DATA_ARCHITECTURE §7).

R1: backup before write — every write_template snapshots the previous file
    to data/annotations.bak/{timestamp}.csv, pruning to last 20.
R2: atomic write — tmp file + Path.replace, crash mid-write leaves previous
    file intact (covered implicitly by R1 + the existing 14 annotation tests).
R3: schema validation on read — missing open_trade_id column raises
    AnnotationSchemaError (Excel-corrupted schema fails loud, not silent).
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

import pytest

from src.annotations import (
    ANNOTATION_COLUMNS,
    AnnotationSchemaError,
    backup_annotations,
    load_annotations,
    write_tag_template,
)


def _row_dict(open_trade_id: str, setup_tag: str = "S1") -> dict:
    """Helper to build a 10-col annotation row dict (4 editable + 6 ref)."""
    return {
        "open_trade_id": open_trade_id, "setup_tag": setup_tag,
        "score": "", "notes": "",
        "ref_open_date": "", "ref_open_time": "", "ref_underlying": "",
        "ref_direction": "", "ref_pnl_usd": "", "ref_round_trips": "",
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ANNOTATION_COLUMNS)
        w.writeheader()
        w.writerows(rows)


# --- R1: backup ---

def test_backup_snapshots_existing_file(tmp_path):
    """backup_annotations copies the current csv to {bak_dir}/{ts}.csv."""
    src = tmp_path / "annotations.csv"
    bak = tmp_path / "annotations.bak"
    _write_csv(src, [_row_dict("T1")])

    result = backup_annotations(src=src, bak_dir=bak, keep=20)

    assert result is not None
    assert result.exists()
    assert result.parent == bak
    # Copied content matches source byte-for-byte (R1 fidelity).
    assert result.read_bytes() == src.read_bytes()


def test_backup_returns_none_when_source_missing(tmp_path):
    """No source = no-op (don't create empty backups). Returns None."""
    src = tmp_path / "no_such.csv"
    bak = tmp_path / "annotations.bak"
    result = backup_annotations(src=src, bak_dir=bak, keep=20)
    assert result is None
    assert not bak.exists() or not list(bak.iterdir()), "no backup dir contents"


def test_backup_prunes_to_keep_n(tmp_path):
    """FIFO prune: with keep=3 and 5 backups, only the 3 most recent survive.
    Backup filenames carry millisecond precision so rapid successive calls
    (like this test) produce distinct file names."""
    csv_path = tmp_path / "annotations.csv"
    bak = tmp_path / "annotations.bak"
    _write_csv(csv_path, [_row_dict("T1")])

    for _ in range(5):
        # Tiny sleep to guarantee distinct ms timestamps even on fast machines.
        time.sleep(0.005)
        backup_annotations(src=csv_path, bak_dir=bak, keep=3)

    surviving = sorted(bak.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
    assert len(surviving) == 3, f"keep=3 should leave 3 backups, got {len(surviving)}"


def test_write_template_creates_backup_before_rewrite(tmp_path, monkeypatch):
    """write_tag_template's R1 hook: before overwriting annotations.csv, the
    previous content should be backed up to data/annotations.bak/."""
    from src import annotations as ann_mod

    csv_path = tmp_path / "annotations.csv"
    bak = tmp_path / "annotations.bak"
    _write_csv(csv_path, [_row_dict("T1", setup_tag="S1")])
    original_bytes = csv_path.read_bytes()

    # Redirect the module's bak dir to tmp_path so we don't touch real data/.
    monkeypatch.setattr(ann_mod, "ANNOTATIONS_BAK_DIR", bak)

    # write_template with no round-trips → rewrites with orphan kept.
    result = ann_mod.write_tag_template(round_trips=[], out_path=csv_path)

    # R1: a backup was made (caller can introspect via result['backup']).
    assert result.get("backup") is not None
    assert result["backup"].exists()
    # And the backup matches the pre-rewrite content (the original T1 row).
    assert result["backup"].read_bytes() == original_bytes


# --- R3: schema validation on read ---

def test_load_annotations_raises_on_missing_key_column(tmp_path):
    """If annotations.csv is missing 'open_trade_id' (Excel-corrupted schema),
    load fails loud — silently dropping every annotation would be far worse."""
    src = tmp_path / "annotations.csv"
    # Write a csv with bad header (rename open_trade_id to something else).
    src.write_text(
        "id,setup_tag,score,notes\nT1,S1,,\n",
        encoding="utf-8",
    )
    with pytest.raises(AnnotationSchemaError) as exc:
        load_annotations(src)
    assert "open_trade_id" in str(exc.value)


def test_load_annotations_returns_empty_when_file_missing(tmp_path):
    """Missing file is fine (no annotations yet) — returns empty dict, no raise."""
    result = load_annotations(tmp_path / "no_such.csv")
    assert result == {}


def test_load_annotations_tolerates_extra_columns(tmp_path):
    """User can add columns in Excel; load_annotations uses DictReader by name
    so extras are silently ignored as long as open_trade_id is present."""
    src = tmp_path / "annotations.csv"
    # All required + an extra "user_note" column.
    src.write_text(
        "open_trade_id,setup_tag,score,notes,user_note\nT1,Q_intraday,8,gm,hello\n",
        encoding="utf-8",
    )
    result = load_annotations(src)
    assert "T1" in result
    assert result["T1"].setup_tag == "Q_intraday"
    assert result["T1"].score == "8"


# --- R2: atomic write (smoke — full crash sim is impractical here) ---

def test_write_template_atomic_no_tmp_left_behind(tmp_path, monkeypatch):
    """write_tag_template uses tmp + rename. After success, no .tmp file remains."""
    from src import annotations as ann_mod
    csv_path = tmp_path / "annotations.csv"
    bak = tmp_path / "annotations.bak"
    monkeypatch.setattr(ann_mod, "ANNOTATIONS_BAK_DIR", bak)
    ann_mod.write_tag_template(round_trips=[], out_path=csv_path)
    assert csv_path.exists()
    assert not (tmp_path / "annotations.csv.tmp").exists(), \
        "atomic write should not leave .tmp file behind"
