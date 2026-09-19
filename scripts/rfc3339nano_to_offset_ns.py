#!/usr/bin/env python3
"""Converts an RFC3339Nano timestamp (as `crictl inspect` reports
`status.startedAt`) to an offset in nanoseconds from a given run-epoch.

Python's datetime.fromisoformat truncates fractional seconds to
microsecond precision, which would silently throw away exactly the
sub-microsecond precision this project's window measurements depend on.
This parses the whole-second and fractional-nanosecond parts separately
to avoid that.

Usage: rfc3339nano_to_offset_ns.py <rfc3339nano-timestamp> <run-epoch-ns>
"""

import calendar
import re
import sys
import time

_PATTERN = re.compile(
    r"^(?P<whole>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.(?P<frac>\d+))?Z$"
)


def rfc3339nano_to_epoch_ns(timestamp: str) -> int:
    match = _PATTERN.match(timestamp)
    if not match:
        raise ValueError(f"not an RFC3339Nano UTC timestamp: {timestamp!r}")

    whole_seconds = calendar.timegm(time.strptime(match.group("whole"), "%Y-%m-%dT%H:%M:%S"))
    frac = (match.group("frac") or "").ljust(9, "0")[:9]
    return whole_seconds * 1_000_000_000 + int(frac)


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    timestamp, run_epoch_ns = sys.argv[1], int(sys.argv[2])
    print(rfc3339nano_to_epoch_ns(timestamp) - run_epoch_ns)


if __name__ == "__main__":
    main()
