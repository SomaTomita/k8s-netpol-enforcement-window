"""Detect host suspension inside a trial from its own observation stream.

The prober dials every `p` (1 ms) with a 200 ms timeout, so no legitimate
gap between consecutive observations in prober.jsonl can exceed roughly
the timeout plus scheduling noise. A gap of seconds or minutes means the
host was not running -- Experiment 1 lost four trials to macOS
"Maintenance Sleep" this way (docs/results/exp1.md). Such a trial is not
a measurement of anything, and the pre-registered procedure is to move
it to `<raw-root>-failed/<run_id>/` with a REASON.md and re-collect.

    uv run python -m npw.gaps data/raw/exp1b            # exit 1 if any gap > 5 s
    uv run python -m npw.gaps data/raw/exp1b --threshold-ms 1000

This module only reads; moving a trial aside is a deliberate, hand-run
step so that the reason is written by someone who looked.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLD_NS = 5_000_000_000


def max_gap_ns(observations: Sequence[Mapping[str, Any]]) -> int:
    """Largest difference between consecutive `offset_ns` values, or 0."""
    best = 0
    prev: int | None = None
    for o in observations:
        cur = int(o["offset_ns"])
        if prev is not None and cur - prev > best:
            best = cur - prev
        prev = cur
    return best


def scan(raw_root: Path, threshold_ns: int = DEFAULT_THRESHOLD_NS) -> list[tuple[str, int, bool]]:
    """`(run_id, max_gap_ns, exceeds)` for every trial dir with a prober.jsonl."""
    out = []
    for d in sorted(p for p in raw_root.iterdir() if p.is_dir()):
        stream = d / "prober.jsonl"
        if not stream.exists():
            continue
        observations = [json.loads(line) for line in stream.read_text().splitlines() if line.strip()]
        gap = max_gap_ns(observations)
        out.append((d.name, gap, gap > threshold_ns))
    return out


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("raw_root", type=Path)
    p.add_argument("--threshold-ms", type=float, default=DEFAULT_THRESHOLD_NS / 1e6)
    a = p.parse_args(argv)
    if not a.raw_root.is_dir():
        print(f"{a.raw_root}: not a directory", file=sys.stderr)
        return 2
    rows = scan(a.raw_root, int(a.threshold_ms * 1e6))
    flagged = [r for r in rows if r[2]]
    for run_id, gap, exceeds in rows:
        mark = "GAP " if exceeds else "ok  "
        print(f"{mark} {run_id} max_gap_ms={gap / 1e6:.1f}")
    print(f"{len(rows)} trials scanned, {len(flagged)} over {a.threshold_ms:g} ms")
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
