import json
from pathlib import Path

import pytest

from npw.analysis.trial import TrialResult, evaluate, load_trial

DEFAULT_PROBE_INTERVAL_NS = 1_000_000


def _write(
    dir: Path,
    prober,
    c_ns,
    b_ns,
    churn=1,
    k=3,
    probe_interval_ns=DEFAULT_PROBE_INTERVAL_NS,
    policy_at=None,
    policy_apply_issued_ns=None,
    trial_json=None,
):
    dir.mkdir()
    (dir / "prober.jsonl").write_text("".join(json.dumps(o) + "\n" for o in prober))
    (dir / "victim.jsonl").write_text(json.dumps({"offset_ns": c_ns, "t_ready_method": "C"}) + "\n")
    (dir / "cri.jsonl").write_text(json.dumps({"offset_ns": b_ns, "t_ready_method": "B"}) + "\n")
    meta = {
        "run_id": dir.name,
        "cni": "cilium",
        "churn_rate_per_min": churn,
        "repetition": 0,
        "sustained_k": k,
        "probe_interval_ns": probe_interval_ns,
    }
    if policy_at is not None:
        meta["policy_at"] = policy_at
    (dir / "meta.json").write_text(json.dumps(meta))
    if trial_json is not None:
        # An explicit trial.json, for the cases where its exact shape is
        # the thing under test rather than a carrier for the apply time.
        (dir / "trial.json").write_text(json.dumps(trial_json))
    elif policy_apply_issued_ns is not None:
        (dir / "trial.json").write_text(
            json.dumps({"run_epoch_ns": 0, "policy_apply_issued_ns": policy_apply_issued_ns})
        )


def _o(ns, out):
    return {"offset_ns": ns, "outcome": out}


def test_window_when_allowed_then_blocked(tmp_path):
    d = tmp_path / "run-0000-000"
    _write(
        d,
        [
            _o(1000, "Allowed"),
            _o(2000, "Allowed"),
            _o(3000, "Blocked"),
            _o(4000, "Blocked"),
            _o(5000, "Blocked"),
        ],
        c_ns=500,
        b_ns=400,
    )
    r = evaluate(load_trial(d))
    assert isinstance(r, TrialResult)
    assert r.window_ns == 2500 and r.censored is False and r.excluded_reason is None
    assert r.right_censored is False
    assert r.censoring_time_ns is None
    assert r.probe_interval_ns == DEFAULT_PROBE_INTERVAL_NS


def test_censored_when_blocked_from_first_observation(tmp_path):
    d = tmp_path / "run-0000-001"
    _write(d, [_o(1000, "Blocked"), _o(2000, "Blocked"), _o(3000, "Blocked")], c_ns=500, b_ns=400)
    r = evaluate(load_trial(d))
    assert r.censored is True and r.window_ns == 500  # floor: first_obs - t_ready
    assert r.right_censored is False


def test_leading_error_does_not_mask_left_censoring(tmp_path):
    """A transient first-dial Error before the sustained Blocked run must
    not be read as "an Allowed state was witnessed". Regression test for
    the bug where `censored` compared t_blocked to observations[0]'s
    offset: with an Error at index 0 and the k-run starting at index 1,
    that comparison came back False even though no Allowed observation
    ever appeared -- exactly the failure mode the prober's own Pod-creation
    latency (648-904ms under the architecture ADR 0003's pilots used; see
    ADR 0003's "Correction, 2026-09-19") makes the expected case, not an
    edge case. It stays the expected case under the current architecture
    too, where that latency is off the measured path: all six runs
    carrying a candidate C are still Blocked from their first
    observation, and none of the ten runs under data/raw/ witnesses an
    Allowed->Blocked transition (docs/threats-to-validity.md).

    Numbers mirror the reviewer's own repro: 501 observations, one
    leading Error then all Blocked, t_ready=500 -> window_ns=1500,
    error_rate~0.002 (well under the 0.05 exclusion threshold), and
    -- with the fix -- censored=True.
    """
    d = tmp_path / "run-0000-005"
    obs = [_o(1000, "Error")] + [_o((i + 1) * 1000, "Blocked") for i in range(1, 501)]
    _write(d, obs, c_ns=500, b_ns=400)
    r = evaluate(load_trial(d))
    assert r.error_rate == pytest.approx(1 / 501)
    assert r.excluded_reason is None
    assert r.window_ns == 1500
    assert r.censored is True


def test_excluded_on_error_rate(tmp_path):
    d = tmp_path / "run-0000-002"
    obs = [_o(i * 1000, "Error" if i % 10 == 0 else "Blocked") for i in range(1, 21)]
    _write(d, obs, c_ns=0, b_ns=0)
    r = evaluate(load_trial(d))
    assert r.error_rate == 0.1 and r.excluded_reason == "error_rate>0.05"


def test_excluded_trial_still_carries_its_window(tmp_path):
    """excluded_reason and window_ns are independent: an excluded trial's
    window must still be reported so the exclusion rate can be assessed
    alongside real numbers (preregistration.md), not hidden behind None.
    """
    d = tmp_path / "run-0000-006"
    obs = [
        _o(1000, "Allowed"),
        _o(2000, "Allowed"),
        _o(3000, "Blocked"),
        _o(4000, "Blocked"),
        _o(5000, "Blocked"),
        _o(6000, "Error"),
        _o(7000, "Error"),
    ]
    _write(d, obs, c_ns=500, b_ns=400)
    r = evaluate(load_trial(d))
    assert r.error_rate == pytest.approx(2 / 7)
    assert r.excluded_reason == "error_rate>0.05"
    assert r.window_ns == 2500
    assert r.censored is False


def test_flagged_when_b_c_diverge(tmp_path):
    d = tmp_path / "run-0000-003"
    _write(d, [_o(10, "Blocked")] * 3, c_ns=10_000_000, b_ns=0)
    r = evaluate(load_trial(d))
    # b_c_skew_ns = t_ready_b - t_ready_c, matching trial.json's own
    # b_c_skew_ns and ADR 0003's "B - C" pilot table convention.
    assert r.b_c_skew_ns == -10_000_000 and r.b_c_flagged is True


def test_no_sustained_block_is_right_censored_not_excluded(tmp_path):
    """No sustained Blocked run within the trial means enforcement never
    arrived while this trial was watching -- the largest unprotected
    window this trial could report, not a measurement failure. It must
    not be excluded (preregistration.md names only error_rate > 0.05).
    censoring_time_ns gives that "at least this long" bound directly.
    """
    d = tmp_path / "run-0000-004"
    _write(d, [_o(1000, "Allowed"), _o(2000, "Allowed")], c_ns=500, b_ns=400)
    r = evaluate(load_trial(d))
    assert r.window_ns is None
    assert r.excluded_reason is None
    assert r.right_censored is True
    assert r.censored is False
    assert r.censoring_time_ns == 1500  # last_offset(2000) - t_ready_c(500)


def test_evaluate_raises_on_empty_observations(tmp_path):
    """An empty prober.jsonl cannot occur in a well-formed dataset --
    scripts/trial.sh refuses to checksum one -- so this is a corrupt
    trial directory, not an excluded or censored trial outcome, and must
    not silently become a fabricated row in trials.csv.
    """
    d = tmp_path / "run-0000-007"
    _write(d, [], c_ns=500, b_ns=400)
    with pytest.raises(ValueError, match="no observations"):
        evaluate(load_trial(d))


def test_load_trial_raises_on_missing_candidate_c(tmp_path):
    d = tmp_path / "run-0000-008"
    d.mkdir()
    (d / "prober.jsonl").write_text(json.dumps(_o(1000, "Blocked")) + "\n")
    (d / "victim.jsonl").write_text(json.dumps({"offset_ns": 1, "t_ready_method": "not-C"}) + "\n")
    (d / "cri.jsonl").write_text(json.dumps({"offset_ns": 1, "t_ready_method": "B"}) + "\n")
    (d / "meta.json").write_text(
        json.dumps(
            {
                "run_id": d.name,
                "cni": "cilium",
                "churn_rate_per_min": 1,
                "repetition": 0,
                "sustained_k": 3,
                "probe_interval_ns": DEFAULT_PROBE_INTERVAL_NS,
            }
        )
    )
    with pytest.raises(ValueError, match="candidate-C"):
        load_trial(d)


def test_load_trial_raises_on_missing_candidate_b(tmp_path):
    d = tmp_path / "run-0000-009"
    d.mkdir()
    (d / "prober.jsonl").write_text(json.dumps(_o(1000, "Blocked")) + "\n")
    (d / "victim.jsonl").write_text(json.dumps({"offset_ns": 1, "t_ready_method": "C"}) + "\n")
    (d / "cri.jsonl").write_text("")
    (d / "meta.json").write_text(
        json.dumps(
            {
                "run_id": d.name,
                "cni": "cilium",
                "churn_rate_per_min": 1,
                "repetition": 0,
                "sustained_k": 3,
                "probe_interval_ns": DEFAULT_PROBE_INTERVAL_NS,
            }
        )
    )
    with pytest.raises(ValueError, match="candidate-B"):
        load_trial(d)


def test_load_trial_raises_on_multiple_candidate_c_records(tmp_path):
    """Two C-method records in victim.jsonl (e.g. the victim restarted
    and self-reported twice) is exactly as unusable as zero -- there is
    no way to pick the right one, so this must fail the same way, not
    silently take the first or last.
    """
    d = tmp_path / "run-0000-013"
    d.mkdir()
    (d / "prober.jsonl").write_text(json.dumps(_o(1000, "Blocked")) + "\n")
    (d / "victim.jsonl").write_text(
        json.dumps({"offset_ns": 1, "t_ready_method": "C"})
        + "\n"
        + json.dumps({"offset_ns": 2, "t_ready_method": "C"})
        + "\n"
    )
    (d / "cri.jsonl").write_text(json.dumps({"offset_ns": 1, "t_ready_method": "B"}) + "\n")
    (d / "meta.json").write_text(
        json.dumps(
            {
                "run_id": d.name,
                "cni": "cilium",
                "churn_rate_per_min": 1,
                "repetition": 0,
                "sustained_k": 3,
                "probe_interval_ns": DEFAULT_PROBE_INTERVAL_NS,
            }
        )
    )
    with pytest.raises(ValueError, match="candidate-C"):
        load_trial(d)


def test_victim_jsonl_with_extra_lines_still_loads(tmp_path):
    """victim.jsonl is `kubectl logs deploy/victim` -- the container's
    entire stdout -- so a line that isn't the candidate-C self-report
    must not make the whole trial unreadable, mirroring
    scripts/trial.sh's own filter-then-require-one parsing.
    """
    d = tmp_path / "run-0000-010"
    d.mkdir()
    (d / "prober.jsonl").write_text(json.dumps(_o(1000, "Blocked")) + "\n")
    (d / "victim.jsonl").write_text(
        json.dumps({"level": "info", "msg": "listener starting"})
        + "\n"
        + json.dumps({"offset_ns": 777, "t_ready_method": "C"})
        + "\n"
    )
    (d / "cri.jsonl").write_text(json.dumps({"offset_ns": 1, "t_ready_method": "B"}) + "\n")
    (d / "meta.json").write_text(
        json.dumps(
            {
                "run_id": d.name,
                "cni": "cilium",
                "churn_rate_per_min": 1,
                "repetition": 0,
                "sustained_k": 3,
                "probe_interval_ns": DEFAULT_PROBE_INTERVAL_NS,
            }
        )
    )
    t = load_trial(d)
    assert t.t_ready_c_ns == 777


def test_load_trial_raises_on_unsorted_prober_offsets(tmp_path):
    d = tmp_path / "run-0000-011"
    _write(d, [_o(1000, "Allowed"), _o(3000, "Blocked"), _o(2000, "Blocked")], c_ns=500, b_ns=400)
    with pytest.raises(ValueError, match="ascending"):
        load_trial(d)


def test_jsonl_malformed_line_names_the_file(tmp_path):
    d = tmp_path / "run-0000-012"
    d.mkdir()
    (d / "prober.jsonl").write_text("not json\n")
    (d / "victim.jsonl").write_text(json.dumps({"offset_ns": 1, "t_ready_method": "C"}) + "\n")
    (d / "cri.jsonl").write_text(json.dumps({"offset_ns": 1, "t_ready_method": "B"}) + "\n")
    (d / "meta.json").write_text(
        json.dumps(
            {
                "run_id": d.name,
                "cni": "cilium",
                "churn_rate_per_min": 1,
                "repetition": 0,
                "sustained_k": 3,
                "probe_interval_ns": DEFAULT_PROBE_INTERVAL_NS,
            }
        )
    )
    with pytest.raises(ValueError, match="prober.jsonl"):
        load_trial(d)


def test_pre_ready_blocked_run_does_not_produce_a_negative_window(tmp_path):
    # Reproduces a real, observed defect (Addendum 1 D2, confirmed live on
    # Antrea during the Task-11.5 positive control, run pc-antrea-2000ms):
    # the prober can start dialing a Pod whose IP was assigned before its
    # listener opened, and a pre-ready ECONNREFUSED is classified Blocked
    # (internal/probe/outcome.go), producing a sustained "Blocked" run
    # that starts and ends *before* t_ready. Without a t_ready lower
    # bound, first_sustained finds that pre-ready run first and reports
    # t_blocked < t_ready -- a negative window that is a harness artefact,
    # not a measurement, and would be printed as "no window detected
    # above the floor" with nothing to distinguish it from a genuine
    # left-censored trial.
    d = tmp_path / "run-0000-002"
    _write(
        d,
        [
            _o(100, "Blocked"),  # pre-ready ECONNREFUSED artefact
            _o(200, "Blocked"),
            _o(300, "Blocked"),
            _o(1500, "Allowed"),  # t_ready has now happened; genuinely Allowed
            _o(2500, "Allowed"),
            _o(3500, "Blocked"),  # the real, post-ready enforcement transition
            _o(4500, "Blocked"),
            _o(5500, "Blocked"),
        ],
        c_ns=1000,  # t_ready sits strictly between the artefact and the real transition
        b_ns=900,
    )
    r = evaluate(load_trial(d))
    assert r.window_ns == 2500  # 3500 - 1000, the real post-ready transition
    assert r.window_ns > 0
    assert r.censored is False
    assert r.right_censored is False


def test_pre_ready_blocked_run_with_no_post_ready_transition_is_right_censored(tmp_path):
    # Same artefact, but with nothing after t_ready sustained-Blocked
    # within the trial: the corrected reading is right-censored (no
    # window observed), not a negative-window "measurement" taken from
    # the pre-ready noise.
    d = tmp_path / "run-0000-003"
    _write(
        d,
        [
            _o(100, "Blocked"),
            _o(200, "Blocked"),
            _o(300, "Blocked"),
            _o(1500, "Allowed"),
            _o(2500, "Allowed"),
            _o(3500, "Allowed"),
        ],
        c_ns=1000,
        b_ns=900,
    )
    r = evaluate(load_trial(d))
    assert r.right_censored is True
    assert r.window_ns is None
    assert r.censoring_time_ns == 3500 - 1000


def test_exp1_trial_without_policy_fields_defaults_to_before(tmp_path):
    d = tmp_path / "run-0000-000"
    _write(d, [_o(1000, "Allowed"), _o(2000, "Blocked"), _o(3000, "Blocked"), _o(4000, "Blocked")], c_ns=500, b_ns=400)
    r = evaluate(load_trial(d))
    assert r.policy_at == "before"
    assert r.policy_apply_issued_ns is None
    assert r.enforcement_latency_ns is None
    assert r.head_start_ns is None


def test_at_ready_trial_derives_latency_and_negative_head_start(tmp_path):
    d = tmp_path / "run-0001-000"
    # t_ready at 500; policy issued at 700 (after ready); blocked run starts at 3000.
    _write(
        d,
        [_o(600, "Allowed"), _o(1000, "Allowed"), _o(3000, "Blocked"), _o(4000, "Blocked"), _o(5000, "Blocked")],
        c_ns=500,
        b_ns=400,
        policy_at="at-ready",
        policy_apply_issued_ns=700,
    )
    r = evaluate(load_trial(d))
    assert r.policy_at == "at-ready"
    assert r.window_ns == 2500
    assert r.enforcement_latency_ns == 2300  # 3000 - 700
    assert r.head_start_ns == -200  # 500 - 700
    assert r.censored is False


def test_right_censored_trial_has_no_latency(tmp_path):
    d = tmp_path / "run-0001-001"
    _write(d, [_o(600, "Allowed"), _o(700, "Allowed")], c_ns=500, b_ns=400, policy_at="at-ready", policy_apply_issued_ns=650)
    r = evaluate(load_trial(d))
    assert r.right_censored is True
    assert r.enforcement_latency_ns is None
    assert r.head_start_ns == -150


def test_left_censored_trial_still_reports_latency_as_a_value(tmp_path):
    # Blocked from the first post-ready look: window is a floor, and so is
    # L. trial.py reports the number; latency.py decides not to average it.
    d = tmp_path / "run-0000-001"
    _write(d, [_o(600, "Blocked"), _o(700, "Blocked"), _o(800, "Blocked")], c_ns=500, b_ns=400, policy_at="with-victim", policy_apply_issued_ns=100)
    r = evaluate(load_trial(d))
    assert r.censored is True
    assert r.enforcement_latency_ns == 500
    assert r.head_start_ns == 400


def test_policy_at_disagreeing_between_meta_and_trial_json_raises(tmp_path):
    """meta.json holds the runner's intent, trial.json what trial.sh ran.

    ADR 0004 rests on the ordering being measured rather than assumed, so
    a disagreement is an error naming both values, not a silent
    preference for one of the two.
    """
    d = tmp_path / "run-0001-002"
    _write(
        d,
        [_o(600, "Allowed"), _o(3000, "Blocked"), _o(4000, "Blocked"), _o(5000, "Blocked")],
        c_ns=500,
        b_ns=400,
        policy_at="at-ready",
        trial_json={
            "run_epoch_ns": 0,
            "policy_at": "with-victim",
            "policy_apply_issued_ns": 700,
        },
    )
    with pytest.raises(ValueError) as excinfo:
        load_trial(d)
    msg = str(excinfo.value)
    assert str(d / "trial.json") in msg
    assert "'with-victim'" in msg and "'at-ready'" in msg


def test_exp1_trial_json_without_policy_keys_still_loads(tmp_path):
    """Experiment 1's 270 trials have a trial.json that predates the
    policy fields: the file is present, but carries neither `policy_at`
    nor `policy_apply_issued_ns`. That shape must keep loading as the
    `before` arm -- the cross-check above fires on disagreement only, not
    on absence.
    """
    d = tmp_path / "run-0000-002"
    _write(
        d,
        [_o(1000, "Blocked"), _o(2000, "Blocked"), _o(3000, "Blocked")],
        c_ns=500,
        b_ns=400,
        trial_json={
            "run_epoch_ns": 1_789_858_399_649_745_000,
            "namespace": "t-run-0000-002",
            "node": "npw-cilium-control-plane",
            "victim_restart_count": 0,
            "t_ready_b_ns": 400,
            "t_ready_c_ns": 500,
            "b_c_skew_ns": -100,
            "warnings": [],
        },
    )
    r = evaluate(load_trial(d))
    assert r.policy_at == "before"
    assert r.policy_apply_issued_ns is None
    assert r.enforcement_latency_ns is None
