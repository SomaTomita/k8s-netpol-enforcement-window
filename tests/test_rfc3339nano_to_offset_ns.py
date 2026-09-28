"""Tests for scripts/rfc3339nano_to_offset_ns.py.

The script lives under scripts/, not src/npw/, because it is a small
converter invoked directly by scripts/trial.sh's shell orchestration, not
part of the analysis package. Loaded here by file path since scripts/ is
not an importable package.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "rfc3339nano_to_offset_ns.py"
_spec = importlib.util.spec_from_file_location("rfc3339nano_to_offset_ns", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
rfc3339nano_to_epoch_ns = _mod.rfc3339nano_to_epoch_ns

# Reference values cross-checked against calendar.timegm directly, since
# datetime.fromisoformat cannot represent nanosecond precision and would
# silently corrupt exactly the precision this project's window
# measurements depend on.
_REFERENCE_EPOCH_NS = 1786206471000000000


def test_parses_nanosecond_precision():
    assert rfc3339nano_to_epoch_ns("2026-08-08T16:27:51.285244551Z") == _REFERENCE_EPOCH_NS + 285244551


def test_parses_without_fractional_seconds():
    assert rfc3339nano_to_epoch_ns("2026-08-08T16:27:51Z") == _REFERENCE_EPOCH_NS


def test_pads_short_fractional_part():
    # crictl/containerd normally emit full 9-digit precision, but the
    # parser must not assume that width.
    assert rfc3339nano_to_epoch_ns("2026-08-08T16:27:51.5Z") == _REFERENCE_EPOCH_NS + 500_000_000


def test_rejects_non_utc_offset():
    with pytest.raises(ValueError):
        rfc3339nano_to_epoch_ns("2026-08-08T16:27:51.285244551+02:00")


def test_rejects_malformed_input():
    with pytest.raises(ValueError):
        rfc3339nano_to_epoch_ns("not-a-timestamp")
