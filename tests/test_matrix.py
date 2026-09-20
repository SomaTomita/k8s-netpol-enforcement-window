"""Tests for npw.matrix.expand: deterministic experiment matrix expansion."""

import pytest

from npw.matrix import expand


def _spec(**overrides):
    base = {
        "cni": ["cilium", "calico"],
        "churn_rate_per_min": [0, 10],
        "policy_set": ["baseline"],
        "repetitions": 3,
    }
    base.update(overrides)
    return base


def test_cartesian_product_size():
    runs = expand(_spec())
    # 2 cni x 2 churn x 1 policy x 3 repetitions = 12 runs
    assert len(runs) == 12


def test_unique_run_id_assignment_across_repetitions():
    runs = expand(_spec())
    run_ids = [run.run_id for run in runs]
    assert len(run_ids) == len(set(run_ids))


def test_deterministic_ordering_across_calls():
    spec = _spec()
    first = expand(spec)
    second = expand(spec)
    assert [run.run_id for run in first] == [run.run_id for run in second]
    assert first == second


def test_missing_required_key_raises_key_error():
    spec = _spec()
    del spec["policy_set"]
    with pytest.raises(KeyError):
        expand(spec)


def test_repetitions_zero_raises_value_error():
    with pytest.raises(ValueError):
        expand(_spec(repetitions=0))


from npw.matrix import POLICY_AT_VALUES, Run


def test_policy_at_defaults_to_before_and_keeps_run_ids():
    spec = _spec()
    runs = expand(spec)
    assert all(r.policy_at == "before" for r in runs)
    # Adding an optional factor with one value must not renumber anything:
    # Experiment 1's committed checksums are keyed by these run ids.
    assert [r.run_id for r in runs] == [f"run-{c:04d}-{rep:03d}" for c in range(4) for rep in range(3)]


def test_policy_at_expands_as_the_innermost_factor():
    runs = expand(_spec(cni=["cilium"], churn_rate_per_min=[1], policy_at=["with-victim", "at-ready"]))
    assert len(runs) == 6
    assert [(r.run_id, r.policy_at) for r in runs[:2]] == [
        ("run-0000-000", "with-victim"),
        ("run-0000-001", "with-victim"),
    ]
    assert runs[3].run_id == "run-0001-000" and runs[3].policy_at == "at-ready"


def test_unknown_policy_at_raises_value_error():
    with pytest.raises(ValueError, match="policy_at"):
        expand(_spec(policy_at=["after-lunch"]))


def test_run_positional_construction_still_works():
    r = Run("run-0001-003", "antrea", 60, "p", 3)
    assert r.policy_at == "before"
    assert POLICY_AT_VALUES == ("before", "with-victim", "at-ready")
