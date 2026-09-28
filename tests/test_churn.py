"""Tests for npw.churn: background Pod count and creation-offset scheduling."""

import pytest

from npw.churn import background_rate, churn_schedule


def test_background_rate_is_rate_minus_victim():
    assert background_rate(1) == 0
    assert background_rate(10) == 9
    assert background_rate(60) == 59


def test_background_rate_rejects_below_one():
    with pytest.raises(ValueError):
        background_rate(0)


def test_churn_schedule_is_evenly_spaced_and_deterministic():
    s = churn_schedule(rate_per_min=60, duration_s=3.0)
    assert s == [0.0, 1.0, 2.0]
    assert churn_schedule(60, 3.0) == s


def test_churn_schedule_zero_rate_is_empty():
    assert churn_schedule(0, 60.0) == []


def test_churn_schedule_duration_shorter_than_one_interval():
    # rate_per_min=1 -> a 60s period; a 30s duration is less than one full
    # interval, but the schedule still starts at 0.0 per the documented
    # contract, so exactly one offset is returned.
    assert churn_schedule(1, 30.0) == [0.0]


def test_churn_schedule_fractional_rate():
    assert churn_schedule(rate_per_min=9, duration_s=20.0) == pytest.approx([0.0, 60 / 9, 120 / 9])
