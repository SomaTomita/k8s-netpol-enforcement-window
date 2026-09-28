"""Tests for npw.analysis.window: sustained-run detection and window math.

These mirror the semantics planned for the Go implementation
(internal/probe/sustained.go, issue #4) at the level of behavior, not
byte-for-byte source: a single blip must not be mistaken for enforcement,
a matching run must be contiguous, and an Error observation breaks a run
just like any other non-matching outcome.
"""

import pytest

from npw.analysis.window import compute_window, first_sustained


def _obs(offset_ns, outcome):
    return {"offset_ns": offset_ns, "outcome": outcome}


def test_first_sustained_ignores_single_blip():
    observations = [
        _obs(0, "allowed"),
        _obs(10, "allowed"),
        _obs(20, "blocked"),  # single blip, broken by the next observation
        _obs(30, "allowed"),
        _obs(40, "blocked"),
        _obs(50, "blocked"),
        _obs(60, "blocked"),
    ]
    # k=3: the blip at offset 20 never reaches length 3, so the first
    # *sustained* run starts at offset 40, not 20.
    assert first_sustained(observations, k=3) == 40


def test_first_sustained_blocked_from_start():
    observations = [_obs(0, "blocked"), _obs(10, "blocked"), _obs(20, "blocked")]
    assert first_sustained(observations, k=3) == 0


def test_first_sustained_returns_none_when_no_run_found():
    observations = [_obs(0, "blocked"), _obs(10, "allowed"), _obs(20, "blocked")]
    assert first_sustained(observations, k=2) is None


def test_first_sustained_k_one_matches_on_first_hit():
    observations = [_obs(0, "allowed"), _obs(10, "blocked")]
    assert first_sustained(observations, k=1) == 10


def test_first_sustained_error_breaks_a_run():
    observations = [
        _obs(0, "blocked"),
        _obs(10, "error"),
        _obs(20, "blocked"),
        _obs(30, "blocked"),
        _obs(40, "blocked"),
    ]
    # The lone "blocked" at offset 0 is immediately followed by an Error,
    # which breaks it -- so the sustained run of 3 doesn't start counting
    # again until offset 20, not 0.
    assert first_sustained(observations, k=3) == 20


def test_first_sustained_error_is_not_silently_skipped():
    observations = [_obs(0, "blocked"), _obs(10, "error"), _obs(20, "blocked")]
    # If "error" observations were silently skipped rather than breaking
    # the run, offsets 0 and 20 would look like two consecutive "blocked"
    # observations and satisfy k=2. They must not: the run resets at the
    # Error, so no run of length 2 actually exists here.
    assert first_sustained(observations, k=2) is None


def test_first_sustained_want_allowed():
    observations = [_obs(0, "blocked"), _obs(10, "allowed"), _obs(20, "allowed")]
    assert first_sustained(observations, k=2, want="allowed") == 10


def test_first_sustained_want_is_case_insensitive():
    observations = [_obs(0, "Blocked"), _obs(10, "Blocked")]
    assert first_sustained(observations, k=2, want="blocked") == 0


def test_first_sustained_empty_observations_returns_none():
    assert first_sustained([], k=1) is None


def test_first_sustained_rejects_non_positive_k():
    with pytest.raises(ValueError):
        first_sustained([_obs(0, "blocked")], k=0)


def test_compute_window_positive():
    assert compute_window(t_ready_ns=100, t_blocked_ns=250) == 150


def test_compute_window_negative():
    assert compute_window(t_ready_ns=250, t_blocked_ns=100) == -150


def test_compute_window_zero():
    assert compute_window(t_ready_ns=100, t_blocked_ns=100) == 0
