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
