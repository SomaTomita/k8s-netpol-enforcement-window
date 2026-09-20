"""CLI: evaluate a raw Experiment 1 tree into a processed report.

    uv run python -m npw.analysis.report_cli data/raw/exp1 data/processed/exp1

Reads every complete trial directory under the raw root, evaluates each
one (`npw.analysis.trial`), and writes four derived files to the output
directory:

- `trials.csv`   -- one row per evaluated trial, the full `TrialResult`
- `summary.csv`  -- one row per (CNI, churn) condition
- `pairwise.csv` -- Mann-Whitney U + Holm per churn level
- `summary.md`   -- the same two tables, rendered for the write-up

This module is the only part of the analysis stage that touches the
filesystem; all statistics live in `npw.analysis.report` and are
testable without it. `data/raw/` is immutable (CLAUDE.md): this reads
from it and writes nothing into it, and refuses an output directory that
would land inside it.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import asdict
from pathlib import Path

from npw.analysis.report import (
    PAIRWISE_FIELDS,
    SUMMARY_FIELDS,
    pairwise_cni,
    render_markdown,
    summarize,
)
from npw.analysis.trial import TrialResult, evaluate, load_trial

USAGE = "usage: python -m npw.analysis.report_cli <raw-root> <out-dir>"

# scripts/trial.sh writes checksums.sha256 last and atomically, so its
# presence -- not the directory's existence -- is what marks a trial
# complete. Same predicate as npw.runner.pending, deliberately: a trial
# the runner would still consider pending must not be analysed as if it
# were finished.
COMPLETE_MARKER = "checksums.sha256"


def _complete_trial_dirs(raw_root: Path) -> list[Path]:
    return sorted(d for d in raw_root.iterdir() if d.is_dir() and (d / COMPLETE_MARKER).exists())


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str]) -> int:
    """Return 0 on success, non-zero with a message on stderr otherwise.

    Every failure path here names the offending path and what was
    expected. An earlier draft of this CLI took the summary CSV's header
    from `rows[0]`, which turned "the raw root holds no complete trials"
    -- the normal state of this repository before Experiment 1 runs --
    into a bare `IndexError` with no indication of what was wrong.
    """
    if len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2

    raw_root, out_dir = Path(argv[0]), Path(argv[1])
    if not raw_root.is_dir():
        print(f"{raw_root}: not a directory (expected the raw data root)", file=sys.stderr)
        return 2
    if raw_root.resolve() in (out_dir.resolve(), *out_dir.resolve().parents):
        print(
            f"{out_dir}: output directory is inside {raw_root}; data/raw/ is immutable, "
            "write derived data to data/interim/ or data/processed/",
            file=sys.stderr,
        )
        return 2

    trial_dirs = _complete_trial_dirs(raw_root)
    if not trial_dirs:
        print(
            f"{raw_root}: no complete trial directories (none contain {COMPLETE_MARKER}); "
            "nothing to analyse",
            file=sys.stderr,
        )
        return 1

    # Fail closed on an unreadable or corrupt trial directory: a partial
    # report over an unknown subset of the trials would be worse than no
    # report. `load_trial`/`evaluate` already raise with a precise reason
    # (ValueError naming the offending file, KeyError for a corrupt
    # meta.json key); this turns that into the same "named path, numbered
    # exit code" shape as every other failure in this CLI instead of a
    # bare traceback.
    results: list[TrialResult] = []
    for d in trial_dirs:
        try:
            results.append(evaluate(load_trial(d)))
        except (ValueError, KeyError, OSError) as e:
            print(f"{d}: cannot evaluate this trial: {e}", file=sys.stderr)
            return 1
    summary_rows = summarize(results)
    conditions = sorted({(row["churn_rate_per_min"], row["policy_at"]) for row in summary_rows})
    pairwise_rows = [pairwise_cni(results, churn, policy_at) for churn, policy_at in conditions]

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        out_dir / "trials.csv",
        tuple(TrialResult.__dataclass_fields__),
        (asdict(r) for r in results),
    )
    _write_csv(out_dir / "summary.csv", SUMMARY_FIELDS, summary_rows)
    _write_csv(
        out_dir / "pairwise.csv", PAIRWISE_FIELDS, (p for level in pairwise_rows for p in level)
    )

    markdown = render_markdown(summary_rows, pairwise_rows)
    (out_dir / "summary.md").write_text(markdown)
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
