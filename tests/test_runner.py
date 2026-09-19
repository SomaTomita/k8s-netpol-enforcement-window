"""Tests for npw.runner's pure helpers: interleave, pending, trial_env, write_meta,
_terminating_trial_namespaces, _namespace_present, _parse_churn_summary,
achieved_rate_per_min, verify_churn, _check_churn, _move_to_failed.

`reclaim_trial_namespace` is also covered, with its two cluster calls
(`_namespace_status` and `subprocess.run`) monkeypatched -- the logic
being pinned is which calls it makes and when it gives up, neither of
which needs a cluster.

main() spawns subprocesses against a live cluster and is exercised by
Task 11's pilot, not here.
"""

import json
from pathlib import Path

import pytest

from npw import runner
from npw.matrix import Run
from npw.runner import (
    _achieved_rate_line,
    _check_churn,
    _create_failure_timestamps,
    _move_to_failed,
    _namespace_present,
    _parse_churn_summary,
    _terminating_trial_namespaces,
    _window_close_s,
    achieved_rate_per_min,
    interleave,
    pending,
    trial_env,
    verify_churn,
    write_meta,
)

SPEC = {
    "cni": ["cilium", "calico"],
    "churn_rate_per_min": [1, 10],
    "policy_set": ["p"],
    "repetitions": 2,
}


def test_interleave_round_robins_churn_within_cni():
    from npw.matrix import expand

    order = interleave(expand(SPEC), cni="cilium")
    assert [(r.churn_rate_per_min, r.repetition) for r in order] == [
        (1, 0),
        (10, 0),
        (1, 1),
        (10, 1),
    ]


def test_interleave_filters_other_cnis():
    from npw.matrix import expand

    assert all(r.cni == "calico" for r in interleave(expand(SPEC), cni="calico"))


def test_resuming_preserves_relative_order_of_remaining_runs(tmp_path: Path):
    """A resumed run must produce the same remaining order as an uninterrupted one.

    This is matrix.py's determinism invariant exercised the way main()
    actually uses it: pending() filtering an already-interleaved list.
    Marking a prefix of interleave()'s own output as completed and
    checking that pending() returns exactly the rest, in the same order,
    is what guarantees a resumed run is identical to one that never
    stopped -- not merely that interleave() filters by CNI (the property
    test_interleave_filters_other_cnis checks, which holds even for an
    implementation that reorders runs on every call).
    """
    from npw.matrix import expand

    full_order = interleave(expand(SPEC), cni="cilium")
    completed, remaining = full_order[:2], full_order[2:]
    for run in completed:
        run_dir = tmp_path / run.run_id
        run_dir.mkdir()
        (run_dir / "checksums.sha256").write_text("x")

    resumed = pending(full_order, tmp_path)
    assert [r.run_id for r in resumed] == [r.run_id for r in remaining]


def test_pending_skips_completed_trials(tmp_path: Path):
    runs = [Run("run-0000-000", "cilium", 1, "p", 0), Run("run-0000-001", "cilium", 1, "p", 1)]
    (tmp_path / "run-0000-000").mkdir()
    (tmp_path / "run-0000-000" / "checksums.sha256").write_text("x")
    assert [r.run_id for r in pending(runs, tmp_path)] == ["run-0000-001"]


def test_trial_env_is_explicit():
    r = Run("run-0001-003", "antrea", 60, "p", 3)
    env = trial_env(r, raw_root=Path("data/raw/exp1"), duration_s=30)
    assert env == {
        "CNI": "antrea",
        "RUN_ID": "run-0001-003",
        "RUN_DIR": "data/raw/exp1/run-0001-003",
        "DURATION": "30",
    }


def test_write_meta_records_frozen_parameters(tmp_path: Path):
    r = Run("run-0000-000", "cilium", 10, "default-deny-ingress", 0)
    write_meta(
        tmp_path,
        r,
        cni_version="1.20.0",
        k=3,
        probe_interval_ns=1_000_000,
        dial_timeout_ns=200_000_000,
        duration_s=30,
    )
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["cni"] == "cilium" and meta["churn_rate_per_min"] == 10 and meta["sustained_k"] == 3
    assert meta["background_churn_per_min"] == 9


def test_write_meta_overwrites_a_stale_attempt(tmp_path: Path):
    """A retried trial must not keep the first attempt's meta.json.

    trial.sh never writes meta.json (Task 7's runner does, before invoking
    it), so if a first attempt crashed partway and a second attempt runs
    with different frozen parameters (e.g. a corrected --duration), the
    conditions file next to the second attempt's measurements must
    describe that second attempt, not linger with the first one's values.
    """
    r = Run("run-0000-000", "cilium", 10, "default-deny-ingress", 0)
    write_meta(
        tmp_path,
        r,
        cni_version="1.20.0",
        k=3,
        probe_interval_ns=1_000_000,
        dial_timeout_ns=200_000_000,
        duration_s=30,
    )
    write_meta(
        tmp_path,
        r,
        cni_version="1.20.0",
        k=3,
        probe_interval_ns=1_000_000,
        dial_timeout_ns=200_000_000,
        duration_s=45,
    )

    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["duration_s"] == 45


def test_terminating_trial_namespaces_filters_active_and_non_trial():
    status = "t-run-0000-000 Active\nt-run-0000-001 Terminating\nkube-system Active\n"
    assert _terminating_trial_namespaces(status) == ["t-run-0000-001"]


def test_terminating_trial_namespaces_empty_when_all_active():
    assert _terminating_trial_namespaces("t-run-0000-000 Active\n") == []


def test_verify_churn_requires_cut_short_true():
    """cut_short=true is now the PASSING outcome (Task 7 fix-round-2 Critical A).

    Churn's schedule is deliberately padded far past any plausible trial
    length (CHURN_OVERRUN_MARGIN_S), so being cut off by the runner's own
    terminate() before exhausting that schedule (cut_short=true) is
    positive proof churn was still running when the trial finished.
    Reaching the end of the schedule on its own (cut_short=false) means
    the opposite: churn stopped emitting Pods before the trial did.
    """
    log = (
        "churn: namespace=t-run-0000-000 scheduled=99 attempted=40 created=40 "
        "create_failed=0 deleted=40 delete_failed=0 cut_short=true elapsed_s=60.000000 "
        "first_create_ts=100.000000 last_create_ts=160.000000\n"
    )
    ok, reason = verify_churn(log, window_close_s=1_000_000_000.0)
    assert ok and reason == "ok"


def test_verify_churn_fails_when_schedule_ran_to_completion():
    # The old (inverted) semantics treated this as success; it is not:
    # churn ran out of scheduled creations before the trial finished.
    log = (
        "churn: namespace=t-run-0000-000 scheduled=9 attempted=9 created=9 "
        "create_failed=0 deleted=9 delete_failed=0 cut_short=false elapsed_s=60.000000 "
        "first_create_ts=100.000000 last_create_ts=160.000000\n"
    )
    ok, reason = verify_churn(log, window_close_s=1_000_000_000.0)
    assert not ok
    assert "exhausted its own schedule" in reason


def test_verify_churn_fails_on_create_failure_inside_the_window():
    log = (
        "churn: failed to create churn-0001 in t-run-0000-000 at ts=100.0 (kubectl exit 1): boom\n"
        "churn: namespace=t-run-0000-000 scheduled=99 attempted=40 created=39 "
        "create_failed=1 deleted=39 delete_failed=0 cut_short=true elapsed_s=60.000000 "
        "first_create_ts=100.000000 last_create_ts=160.000000\n"
    )
    ok, reason = verify_churn(log, window_close_s=200.0)
    assert not ok
    assert "measurement window closed" in reason


def test_verify_churn_treats_a_failure_exactly_at_window_close_as_in_window():
    # The boundary itself: ts == window_close_s must count as in-window
    # (fail), not after it -- "at or before", not "strictly before".
    log = (
        "churn: failed to create churn-0001 in t-run-0000-000 at ts=200.0 (kubectl exit 1): boom\n"
        "churn: namespace=t-run-0000-000 scheduled=99 attempted=40 created=39 "
        "create_failed=1 deleted=39 delete_failed=0 cut_short=true elapsed_s=60.000000 "
        "first_create_ts=100.000000 last_create_ts=160.000000\n"
    )
    ok, reason = verify_churn(log, window_close_s=200.0)
    assert not ok
    assert "measurement window closed" in reason


def test_verify_churn_accepts_create_failure_after_the_window_closed():
    """A creation failure racing scripts/trial.sh's own teardown must not fail the trial.

    scripts/trial.sh's EXIT trap deletes the trial namespace as soon as it
    returns, and _stop_churn only stops churn after that -- so a handful
    of "namespace not found" failures right at the end are an accepted,
    harmless race (Task 7 fix-round-1 Ruling 3), not a real degraded rate
    (Task 7 fix-round-2 Important C).
    """
    log = (
        "churn: failed to create churn-0040 in t-run-0000-000 at ts=250.0 (kubectl exit 1): "
        'namespaces "t-run-0000-000" not found\n'
        "churn: namespace=t-run-0000-000 scheduled=99 attempted=41 created=40 "
        "create_failed=1 deleted=40 delete_failed=0 cut_short=true elapsed_s=60.000000 "
        "first_create_ts=100.000000 last_create_ts=160.000000\n"
    )
    ok, reason = verify_churn(log, window_close_s=200.0)
    assert ok
    assert "after the window closed" in reason


def test_verify_churn_fails_closed_on_unparseable_failure_timestamp():
    # No ts= field -- cannot prove the failure happened after the window,
    # so it must count against the trial rather than be silently ignored.
    log = (
        "churn: failed to create churn-0001 in t-run-0000-000 (kubectl exit 1): boom\n"
        "churn: namespace=t-run-0000-000 scheduled=99 attempted=40 created=39 "
        "create_failed=1 deleted=39 delete_failed=0 cut_short=true elapsed_s=60.000000 "
        "first_create_ts=100.000000 last_create_ts=160.000000\n"
    )
    ok, reason = verify_churn(log, window_close_s=200.0)
    assert not ok
    assert "measurement window closed" in reason


def test_verify_churn_fails_when_no_summary_present():
    # e.g. the churn process crashed before ever printing its summary line.
    log = "churn: failed to create churn-0000 in t-run-0000-000 at ts=1.0 (kubectl exit 1): boom\n"
    ok, reason = verify_churn(log, window_close_s=200.0)
    assert not ok
    assert "no churn summary" in reason


def test_create_failure_timestamps_parses_and_flags_unparseable():
    log = (
        "churn: failed to create churn-0001 in t-run-0000-000 at ts=12.5 (kubectl exit 1): boom\n"
        "churn: failed to create churn-0002 in t-run-0000-000 (kubectl exit 1): no ts field\n"
        "churn: failed to delete churn-0003 in t-run-0000-000 (kubectl exit 1): not a creation failure\n"
    )
    assert _create_failure_timestamps(log) == [12.5, None]


def test_window_close_s_uses_last_prober_observation_not_duration(tmp_path: Path):
    """The window closes at run_epoch + the last probe's offset_ns, not run_epoch + duration.

    Task 7 fix-round-3 Important 1: an earlier version returned
    `run_epoch_ns/1e9 + duration_s`, which is systematically early by
    however long scripts/trial.sh's own setup (policy/prober/victim
    applies, kubectl wait for Running) took before the measurement even
    began -- cmd/prober's own `-duration` deadline starts only once its
    probe loop is entered, well after `run_epoch_ns` is taken. The last
    line in prober.jsonl already reflects that real startup delay, so
    using it needs no separate estimate of it at all.
    """
    (tmp_path / "trial.json").write_text(json.dumps({"run_epoch_ns": 1_700_000_000_000_000_000}))
    (tmp_path / "prober.jsonl").write_text(
        '{"offset_ns": 5000000000, "outcome": "Allowed"}\n'
        '{"offset_ns": 37123456789, "outcome": "Blocked"}\n'
    )
    # duration_s (e.g. 30) is nowhere in this computation any more.
    assert _window_close_s(tmp_path) == 1_700_000_000.0 + 37.123456789


def test_window_close_s_raises_on_missing_trial_json(tmp_path: Path):
    (tmp_path / "prober.jsonl").write_text('{"offset_ns": 1, "outcome": "Allowed"}\n')
    with pytest.raises(FileNotFoundError):
        _window_close_s(tmp_path)


def test_window_close_s_raises_on_missing_prober_jsonl(tmp_path: Path):
    (tmp_path / "trial.json").write_text(json.dumps({"run_epoch_ns": 1}))
    with pytest.raises(FileNotFoundError):
        _window_close_s(tmp_path)


def test_window_close_s_raises_on_empty_prober_jsonl(tmp_path: Path):
    (tmp_path / "trial.json").write_text(json.dumps({"run_epoch_ns": 1}))
    (tmp_path / "prober.jsonl").write_text("")
    with pytest.raises(ValueError):
        _window_close_s(tmp_path)


def test_window_close_s_raises_on_malformed_trial_json(tmp_path: Path):
    (tmp_path / "trial.json").write_text("not json")
    (tmp_path / "prober.jsonl").write_text('{"offset_ns": 1, "outcome": "Allowed"}\n')
    with pytest.raises(json.JSONDecodeError):
        _window_close_s(tmp_path)


PASSING_SUMMARY = (
    "churn: namespace=t-x scheduled=1 attempted=1 created=1 create_failed=0 "
    "deleted=1 delete_failed=0 cut_short=true elapsed_s=60.000000 "
    "first_create_ts=100.000000 last_create_ts=160.000000\n"
)


def _churn_log(tmp_path: Path, text: str = PASSING_SUMMARY) -> Path:
    path = tmp_path / "churn.log"
    path.write_text(text)
    return path


def test_check_churn_fails_without_raising_when_trial_json_is_missing(tmp_path: Path):
    """A run directory whose trial.json can't be read must fail verification, not crash main().

    Task 7 fix-round-3 Important 2: _window_close_s raising uncaught out
    of main() would leave checksums.sha256 intact and the trial silently
    unverified in the dataset. _check_churn is what main() actually calls,
    so this is the boundary that must not raise.
    """
    ok, reason = _check_churn(tmp_path, _churn_log(tmp_path))
    assert not ok
    assert "could not determine the measurement window close time" in reason


def test_check_churn_fails_without_raising_when_trial_json_is_malformed(tmp_path: Path):
    (tmp_path / "trial.json").write_text("{not valid json")
    (tmp_path / "prober.jsonl").write_text('{"offset_ns": 1, "outcome": "Allowed"}\n')
    ok, reason = _check_churn(tmp_path, _churn_log(tmp_path))
    assert not ok
    assert "could not determine the measurement window close time" in reason


def test_check_churn_fails_without_raising_on_a_wrong_typed_field(tmp_path: Path):
    """A present-but-wrong-typed field raises TypeError, not ValueError, and must fail closed.

    `json.loads` accepts `{"run_epoch_ns": null}` happily; the
    arithmetic in _window_close_s is where it breaks, with a TypeError
    that was outside _check_churn's caught set. Uncaught, it escapes
    main() with checksums.sha256 already written -- the same fail-open
    class the branch closed once for missing and malformed files.
    """
    (tmp_path / "trial.json").write_text(json.dumps({"run_epoch_ns": None}))
    (tmp_path / "prober.jsonl").write_text('{"offset_ns": 1, "outcome": "Allowed"}\n')
    ok, reason = _check_churn(tmp_path, _churn_log(tmp_path))
    assert not ok
    assert "could not determine the measurement window close time" in reason


def test_check_churn_fails_without_raising_when_the_churn_log_cannot_be_read(tmp_path: Path):
    """An unreadable churn.log must fail the trial, not crash main().

    main() used to call `churn_log.read_text()` itself, outside this
    guard, so an OSError there escaped with checksums.sha256 intact.
    """
    (tmp_path / "trial.json").write_text(json.dumps({"run_epoch_ns": 0}))
    (tmp_path / "prober.jsonl").write_text('{"offset_ns": 1, "outcome": "Allowed"}\n')
    ok, reason = _check_churn(tmp_path, tmp_path / "does-not-exist.log")
    assert not ok
    assert "could not read" in reason


def test_check_churn_delegates_to_verify_churn_when_window_close_is_known(tmp_path: Path):
    (tmp_path / "trial.json").write_text(json.dumps({"run_epoch_ns": 0}))
    (tmp_path / "prober.jsonl").write_text('{"offset_ns": 30000000000, "outcome": "Blocked"}\n')
    ok, reason = _check_churn(tmp_path, _churn_log(tmp_path))
    assert ok and reason == "ok"


def test_move_to_failed_relocates_directory_and_writes_reason(tmp_path: Path):
    raw_root = tmp_path / "exp1"
    run_dir = raw_root / "run-0000-000"
    run_dir.mkdir(parents=True)
    (run_dir / "checksums.sha256").write_text("x")
    r = Run("run-0000-000", "cilium", 10, "default-deny-ingress", 0)

    destination = _move_to_failed(raw_root, run_dir, r, "1 churn Pod creation(s) failed")

    assert destination == tmp_path / "exp1-failed" / "run-0000-000"
    assert not run_dir.exists()
    assert (destination / "checksums.sha256").read_text() == "x"
    reason_text = (destination / "REASON.md").read_text()
    assert "run-0000-000" in reason_text
    assert "1 churn Pod creation(s) failed" in reason_text


def test_move_to_failed_does_not_clobber_an_earlier_failure(tmp_path: Path):
    raw_root = tmp_path / "exp1"
    run_dir = raw_root / "run-0000-000"
    run_dir.mkdir(parents=True)
    r = Run("run-0000-000", "cilium", 10, "default-deny-ingress", 0)

    earlier = tmp_path / "exp1-failed" / "run-0000-000"
    earlier.mkdir(parents=True)
    (earlier / "REASON.md").write_text("earlier failure\n")

    destination = _move_to_failed(raw_root, run_dir, r, "second failure")

    assert destination == tmp_path / "exp1-failed" / "run-0000-000-1"
    assert (earlier / "REASON.md").read_text() == "earlier failure\n"
    assert "second failure" in (destination / "REASON.md").read_text()


# --- The achieved churn rate: recorded, and therefore checkable -----------
#
# The experiment's independent variable is the churn rate. Before these,
# scripts/churn.py's summary carried counts and no timing at all, so
# nothing in churn.log, trial.json or meta.json said *when* churn ran --
# and a loop that fell behind (kubectl run latency approaching the
# schedule's period) was indistinguishable from one that kept cadence.
# Worse, falling behind makes cut_short=true *more* likely, so the one
# existing check could not catch it. preregistration.md Addendum 1 A3
# claims the achieved rate is checkable against the configured one;
# these pin what makes that claim true.


def test_parse_churn_summary_reads_the_timing_fields():
    summary = _parse_churn_summary(PASSING_SUMMARY)
    assert summary is not None
    assert summary["elapsed_s"] == "60.000000"
    assert summary["first_create_ts"] == "100.000000"
    assert summary["last_create_ts"] == "160.000000"


def test_parse_churn_summary_accepts_a_run_that_created_nothing():
    log = (
        "churn: namespace=t-x scheduled=9 attempted=1 created=0 create_failed=1 "
        "deleted=0 delete_failed=0 cut_short=true elapsed_s=0.500000 "
        "first_create_ts=none last_create_ts=none\n"
    )
    summary = _parse_churn_summary(log)
    assert summary is not None and summary["first_create_ts"] == "none"


def test_parse_churn_summary_is_not_shadowed_by_the_runners_own_appended_line():
    """_stop_churn appends to churn.log after the driver's summary.

    _parse_churn_summary scans from the end, so an appended line that
    matched would shadow the real summary. The `runner: ` prefix is what
    keeps it from matching at all.
    """
    log = PASSING_SUMMARY + _achieved_rate_line(PASSING_SUMMARY, 9) + "\n"
    summary = _parse_churn_summary(log)
    assert summary is not None and summary["created"] == "1"


def test_achieved_rate_per_min_is_created_over_elapsed():
    log = (
        "churn: namespace=t-x scheduled=99 attempted=30 created=30 create_failed=0 "
        "deleted=25 delete_failed=0 cut_short=true elapsed_s=60.000000 "
        "first_create_ts=100.000000 last_create_ts=158.000000\n"
    )
    assert achieved_rate_per_min(_parse_churn_summary(log)) == pytest.approx(30.0)


def test_achieved_rate_per_min_exposes_a_loop_that_fell_behind():
    """The case the counts alone could not show, and cut_short=true hides.

    Configured 59/min, but only 20 creations landed in 60 s: kubectl run
    latency ate most of each period. cut_short=true here too -- being
    behind makes it *more* likely -- so this number is the only evidence
    of the degradation. It is recorded, not gated on (Addendum 1 A3).
    """
    log = (
        "churn: namespace=t-x scheduled=99 attempted=20 created=20 create_failed=0 "
        "deleted=15 delete_failed=0 cut_short=true elapsed_s=60.000000 "
        "first_create_ts=100.000000 last_create_ts=157.000000\n"
    )
    assert achieved_rate_per_min(_parse_churn_summary(log)) == pytest.approx(20.0)
    ok, _ = verify_churn(log, window_close_s=1_000_000_000.0)
    assert ok, "the existing check passes this trial; only the recorded rate shows the shortfall"


def test_achieved_rate_per_min_is_none_without_a_summary():
    assert achieved_rate_per_min(None) is None


def test_achieved_rate_per_min_is_none_rather_than_zero_on_zero_elapsed():
    # 0 pods per 0 seconds is unrecordable, not "churn created nothing".
    summary = {"created": "0", "elapsed_s": "0.000000"}
    assert achieved_rate_per_min(summary) is None


def test_achieved_rate_line_names_both_rates():
    line = _achieved_rate_line(PASSING_SUMMARY, 9)
    assert line.startswith("runner: ")
    assert "configured_per_min=9" in line
    assert "achieved_per_min=1.000" in line


def test_achieved_rate_line_says_unrecordable_when_there_is_no_summary():
    line = _achieved_rate_line("churn: something went wrong\n", 9)
    assert "achieved_per_min=unrecordable" in line


# --- Reclaiming a leftover trial namespace on resume ---------------------
#
# main() pre-creates t-<run_id>, starts churn, then sleeps CHURN_WARMUP_S
# before invoking trial.sh. A hard death inside that window leaves the
# namespace Active with the aborted attempt's churn Pods in it. On resume
# pending() re-offers the run id and wait_for_cold_node passes by design
# (it rejects only *Terminating* t-* namespaces), so without reclaiming,
# the retried trial measures a CNI already warm for that namespace --
# the confound Addendum 1 A4 exists to eliminate, biasing t_blocked
# earlier.


def test_namespace_present_is_phase_agnostic():
    status = "t-run-0000-000 Active\nt-run-0000-001 Terminating\nkube-system Active\n"
    assert _namespace_present(status, "t-run-0000-000")
    assert _namespace_present(status, "t-run-0000-001")
    assert not _namespace_present(status, "t-run-0000-002")


def test_namespace_present_does_not_match_a_prefix():
    assert not _namespace_present("t-run-0000-0001 Active\n", "t-run-0000-000")


def test_reclaim_trial_namespace_is_a_no_op_when_nothing_is_left_over(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "_namespace_status", lambda: "kube-system Active\n")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: calls.append(a))

    assert runner.reclaim_trial_namespace("t-run-0000-000") is False
    assert calls == [], "no kubectl delete may be issued when there is nothing to reclaim"


def test_reclaim_trial_namespace_deletes_and_waits_for_the_namespace_to_go(monkeypatch):
    statuses = iter(
        [
            "t-run-0000-000 Active\n",  # the existence check: a leftover is there
            "t-run-0000-000 Terminating\n",  # first poll after delete
            "kube-system Active\n",  # gone
        ]
    )
    monkeypatch.setattr(runner, "_namespace_status", lambda: next(statuses))
    deletes = []
    monkeypatch.setattr(
        runner.subprocess, "run", lambda argv, **kw: deletes.append(argv) or _Completed()
    )
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)

    assert runner.reclaim_trial_namespace("t-run-0000-000") is True
    assert deletes == [
        ["kubectl", "delete", "namespace", "t-run-0000-000", "--ignore-not-found", "--wait=false"]
    ]


def test_reclaim_trial_namespace_raises_rather_than_running_against_a_warm_namespace(monkeypatch):
    """Bounded, and loud on timeout -- the alternative is measuring the confound."""
    monkeypatch.setattr(runner, "_namespace_status", lambda: "t-run-0000-000 Active\n")
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kw: _Completed())
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)

    with pytest.raises(TimeoutError, match="still exists"):
        runner.reclaim_trial_namespace("t-run-0000-000", timeout_s=0.0, poll_interval_s=0.0)


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""
