"""Tests for --review-flow CLI glue (FR-PIVOT-3d UX wrapper).

Locks the four user-visible properties of the wrapper:
  1. argparse registers --review-flow and rejects co-use with --tag-template
  2. PermissionError on write_template -> rc=3 + friendly message (Excel locks csv)
  3. EOFError on input() (piped / no-tty) -> rc=130 + no regen (also covers Ctrl+C
     path: both EOFError + KeyboardInterrupt share one except handler)
  4. Happy path with OS handlers mocked out -> rc=0 + csv refreshed + html built

GUI side-effects (Excel via os.startfile, browser via webbrowser.open) are
monkey-patched so the test suite stays headless.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import pivot, sqlite_store


def _empty_db(path: Path) -> str:
    """Bare SQLite with the trades schema but zero rows (review-flow only needs
    the table to exist; round-trip pairing returns []) — keeps tests data-free.
    Uses sqlite_store.connect to get the Row factory _migrate needs."""
    conn = sqlite_store.connect(path)
    sqlite_store.init_schema(conn)
    conn.close()
    return str(path)


# --- argparse layout (AC1 / R7) ---

def test_review_flow_and_tag_template_are_mutually_exclusive():
    """Both flags at once must exit 2 (argparse mutex error)."""
    with pytest.raises(SystemExit) as exc:
        pivot.main(["--tag-template", "--review-flow"])
    assert exc.value.code == 2


def test_review_flow_flag_parses_alone(tmp_path, monkeypatch):
    """`--review-flow` is a recognized flag (no parse error). We stub the
    interactive bits so the call returns cleanly. Isolated --export-dir keeps
    the re-export step from touching the real data/exports/."""
    db = _empty_db(tmp_path / "t.sqlite")
    ann = tmp_path / "annotations.csv"
    out = tmp_path / "pivot.html"
    export_dir = tmp_path / "exports"

    monkeypatch.setattr(pivot, "_open_in_default_app", lambda p: None)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")
    monkeypatch.setattr(pivot.webbrowser, "open", lambda *_a, **_k: True)

    rc = pivot.main(["--db", db, "--annotations", str(ann), "--out", str(out),
                     "--export-dir", str(export_dir), "--lookback", "2",
                     "--review-flow"])
    assert rc == 0


# --- error paths ---

def test_review_flow_permission_error_returns_rc3(tmp_path, monkeypatch, capsys):
    """Excel holding the csv -> write_template raises PermissionError ->
    friendly message + rc=3, no Excel open, no Enter wait."""
    db = _empty_db(tmp_path / "t.sqlite")
    ann = tmp_path / "annotations.csv"
    out = tmp_path / "pivot.html"

    def _raise(*_a, **_k):
        raise PermissionError("locked by Excel")

    monkeypatch.setattr(pivot, "write_template", _raise)

    opened: list = []
    monkeypatch.setattr(pivot, "_open_in_default_app",
                        lambda p: opened.append(p))
    waited: list = []
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: waited.append(1) or "")

    rc = pivot.review_flow(db, str(ann), str(out))
    captured = capsys.readouterr()

    assert rc == 3
    assert "locked" in captured.out.lower()
    assert not opened, "should not try to open Excel after lock failure"
    assert not waited, "should not wait for Enter after lock failure"


def test_review_flow_eof_during_wait_returns_rc130(tmp_path, monkeypatch, capsys):
    """EOF on input() (piped empty stdin / no tty) -> abort path: rc=130, no
    html regen. Shares the except handler with KeyboardInterrupt (Ctrl+C)."""
    db = _empty_db(tmp_path / "t.sqlite")
    ann = tmp_path / "annotations.csv"
    out = tmp_path / "pivot.html"

    monkeypatch.setattr(pivot, "_open_in_default_app", lambda p: None)

    def _eof(*_a, **_k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)

    regen_called: list = []
    real_generate = pivot.generate
    monkeypatch.setattr(pivot, "generate",
                        lambda *a, **k: regen_called.append(1) or real_generate(*a, **k))

    rc = pivot.review_flow(db, str(ann), str(out))
    captured = capsys.readouterr()

    assert rc == 130
    assert "abort" in captured.out.lower()
    assert not regen_called, "should not regenerate html after abort"
    assert ann.exists(), "annotations.csv (refreshed in step 1) must remain"


# --- happy path (AC2) ---

def test_review_flow_happy_path(tmp_path, monkeypatch, capsys):
    """Full flow with OS handlers mocked: should refresh csv, wait Enter,
    re-export mts csv lookback window, regenerate html, and try to open the
    browser. rc=0."""
    db = _empty_db(tmp_path / "t.sqlite")
    ann = tmp_path / "annotations.csv"
    out = tmp_path / "pivot.html"
    export_dir = tmp_path / "exports"                       # isolate from real data/exports

    excel_opens: list = []
    monkeypatch.setattr(pivot, "_open_in_default_app",
                        lambda p: excel_opens.append(p))
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")
    browser_opens: list = []
    monkeypatch.setattr(pivot.webbrowser, "open",
                        lambda url, *_a, **_k: browser_opens.append(url) or True)

    rc = pivot.review_flow(db, str(ann), str(out),
                           lookback=2, export_dir=export_dir)
    captured = capsys.readouterr()

    assert rc == 0
    assert ann.exists(), "annotations.csv must be created (step 1)"
    assert out.exists(), "pivot html must be generated (step 4)"
    assert excel_opens == [ann.resolve()], "Excel should be opened with the csv"
    assert len(browser_opens) == 1, "browser should be opened once for the html"
    assert browser_opens[0].startswith("file:"), \
        "browser URL should be a file:// uri"
    # Step prefixes appear in order (4 steps after state-machine re-export added)
    out_text = captured.out
    assert (out_text.index("[1/4]") < out_text.index("[2/4]")
            < out_text.index("[3/4]") < out_text.index("[4/4]"))
