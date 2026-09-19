"""Tests for the report CLI's file discovery, output files and failure modes.

The trial directories built here are synthetic fixtures written under
pytest's `tmp_path`; nothing in this file reads or writes `data/`.
"""

import csv
import json

from npw.analysis.report_cli import main

MS = 1_000_000


def _trial_dir(root, run_id, cni, churn, prober, c_ns=500, b_ns=400, complete=True):
    """Write one trial directory in the layout scripts/trial.sh produces."""
    d = root / run_id
    d.mkdir(parents=True)
    (d / "prober.jsonl").write_text("".join(json.dumps(o) + "\n" for o in prober))
    (d / "victim.jsonl").write_text(json.dumps({"offset_ns": c_ns, "t_ready_method": "C"}) + "\n")
    (d / "cri.jsonl").write_text(json.dumps({"offset_ns": b_ns, "t_ready_method": "B"}) + "\n")
    (d / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "cni": cni,
                "churn_rate_per_min": churn,
                "repetition": 0,
                "sustained_k": 3,
                "probe_interval_ns": MS,
            }
        )
    )
    if complete:
        # trial.sh writes checksums.sha256 last: its presence is what marks
        # a trial complete (mirrors npw.runner.pending).
        (d / "checksums.sha256").write_text("stub\n")
    return d


def _allowed_then_blocked():
    return [
        {"offset_ns": 1000, "outcome": "Allowed"},
        {"offset_ns": 2000, "outcome": "Allowed"},
        {"offset_ns": 3000, "outcome": "Blocked"},
        {"offset_ns": 4000, "outcome": "Blocked"},
        {"offset_ns": 5000, "outcome": "Blocked"},
    ]


def test_main_writes_trials_summary_pairwise_and_markdown(tmp_path):
    raw = tmp_path / "raw"
    out = tmp_path / "processed"
    _trial_dir(raw, "run-0000-000", "cilium", 1, _allowed_then_blocked())
    _trial_dir(raw, "run-0000-001", "calico", 1, _allowed_then_blocked())

    assert main([str(raw), str(out)]) == 0

    trials = list(csv.DictReader((out / "trials.csv").open()))
    assert {r["run_id"] for r in trials} == {"run-0000-000", "run-0000-001"}
    assert "right_censored" in trials[0] and "probe_interval_ns" in trials[0]

    summary = list(csv.DictReader((out / "summary.csv").open()))
    assert [r["cni"] for r in summary] == ["calico", "cilium"]

    pairwise = list(csv.DictReader((out / "pairwise.csv").open()))
    assert [(r["cni_a"], r["cni_b"]) for r in pairwise] == [("calico", "cilium")]

    assert "| CNI |" in (out / "summary.md").read_text()


def test_main_skips_directories_without_checksums(tmp_path):
    raw = tmp_path / "raw"
    out = tmp_path / "processed"
    _trial_dir(raw, "run-0000-000", "cilium", 1, _allowed_then_blocked())
    _trial_dir(raw, "run-0000-001", "cilium", 1, _allowed_then_blocked(), complete=False)

    assert main([str(raw), str(out)]) == 0
    assert [r["run_id"] for r in csv.DictReader((out / "trials.csv").open())] == ["run-0000-000"]


def test_main_never_writes_into_the_raw_root(tmp_path):
    raw = tmp_path / "raw"
    out = tmp_path / "processed"
    d = _trial_dir(raw, "run-0000-000", "cilium", 1, _allowed_then_blocked())
    before = sorted(p.name for p in d.iterdir())

    assert main([str(raw), str(out)]) == 0
    assert sorted(p.name for p in d.iterdir()) == before
    assert sorted(p.name for p in raw.iterdir()) == ["run-0000-000"]


def test_main_refuses_to_write_inside_the_raw_root(tmp_path, capsys):
    """data/raw/ is immutable (CLAUDE.md): derived output goes elsewhere."""
    raw = tmp_path / "raw"
    _trial_dir(raw, "run-0000-000", "cilium", 1, _allowed_then_blocked())

    assert main([str(raw), str(raw / "processed")]) != 0
    assert "immutable" in capsys.readouterr().err


def test_main_fails_clearly_on_an_empty_raw_root(tmp_path, capsys):
    """Regression: the planned implementation indexed rows[0] for the CSV
    header, so an empty raw root died with a bare IndexError."""
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "processed"

    assert main([str(raw), str(out)]) != 0
    err = capsys.readouterr().err
    assert str(raw) in err and "checksums.sha256" in err
    assert not out.exists()


def test_main_fails_clearly_on_a_missing_raw_root(tmp_path, capsys):
    missing = tmp_path / "nope"

    assert main([str(missing), str(tmp_path / "processed")]) != 0
    assert str(missing) in capsys.readouterr().err


def test_main_fails_clearly_on_a_corrupt_trial_directory(tmp_path, capsys):
    """`evaluate` raises on a checksummed-but-empty prober.jsonl. Failing
    closed is right, but a bare traceback is not how every other failure in
    this CLI presents itself."""
    raw = tmp_path / "raw"
    out = tmp_path / "processed"
    _trial_dir(raw, "run-0000-000", "cilium", 1, _allowed_then_blocked())
    corrupt = _trial_dir(raw, "run-0000-001", "cilium", 1, [])

    assert main([str(raw), str(out)]) != 0
    err = capsys.readouterr().err
    assert str(corrupt) in err
    assert "prober.jsonl" in err
    assert not out.exists()


def test_main_fails_on_wrong_argument_count(capsys):
    assert main([]) != 0
    assert "usage" in capsys.readouterr().err.lower()
