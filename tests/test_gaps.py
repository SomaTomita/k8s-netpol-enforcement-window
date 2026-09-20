"""Tests for the host-sleep detector: max inter-observation gap per trial."""

import json

from npw.gaps import DEFAULT_THRESHOLD_NS, main, max_gap_ns, scan


def _trial(root, run_id, offsets):
    d = root / run_id
    d.mkdir(parents=True)
    (d / "prober.jsonl").write_text(
        "".join(json.dumps({"offset_ns": o, "outcome": "Blocked"}) + "\n" for o in offsets)
    )


def test_max_gap_is_zero_for_fewer_than_two_observations():
    assert max_gap_ns([]) == 0
    assert max_gap_ns([{"offset_ns": 5}]) == 0


def test_max_gap_is_the_largest_consecutive_difference():
    obs = [{"offset_ns": o} for o in (0, 1_000_000, 3_000_000, 3_500_000)]
    assert max_gap_ns(obs) == 2_000_000


def test_scan_flags_only_trials_over_the_threshold(tmp_path):
    _trial(tmp_path, "run-0000-000", [0, 1_000_000, 2_000_000])
    _trial(tmp_path, "run-0000-001", [0, 1_000_000, 1_000_000 + DEFAULT_THRESHOLD_NS + 1])
    (tmp_path / "not-a-trial").mkdir()
    assert scan(tmp_path) == [
        ("run-0000-000", 1_000_000, False),
        ("run-0000-001", DEFAULT_THRESHOLD_NS + 1, True),
    ]


def test_main_exits_one_when_a_gap_exceeds_the_threshold(tmp_path, capsys):
    _trial(tmp_path, "run-0000-000", [0, 7_000_000_000])
    assert main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "run-0000-000" in out and "7000.0" in out


def test_main_threshold_override_in_ms(tmp_path):
    _trial(tmp_path, "run-0000-000", [0, 400_000_000])
    assert main([str(tmp_path)]) == 0
    assert main([str(tmp_path), "--threshold-ms", "300"]) == 1
