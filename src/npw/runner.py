"""Drive Experiment 1 trials from the frozen matrix.

Pure helpers (`interleave`, `pending`, `trial_env`, `write_meta`,
`_terminating_trial_namespaces`, `_namespace_present`,
`_parse_churn_summary`, `achieved_rate_per_min`, `_achieved_rate_line`,
`_create_failure_timestamps`, `verify_churn`, `_check_churn`) are
unit-tested without a cluster. `reclaim_trial_namespace` is tested with
its two cluster calls (`_namespace_status`, `subprocess.run`)
monkeypatched. `_window_close_s` and `_move_to_failed`
touch only the filesystem (no subprocess, no cluster) and are exercised
with `tmp_path` fixtures. `main()` is the only place subprocesses are
spawned: one `scripts/churn.py` per trial (background churn, backgrounded
for the trial's duration) and one `scripts/trial.sh` per trial.

Resumability: this module never keeps its own record of what has run.
`pending()` re-derives that from the filesystem (`checksums.sha256`,
`scripts/trial.sh`'s own completion marker) every time it is called, so
stopping this process and starting it again -- for any reason, including a
crashed trial -- reproduces the same remaining work `interleave()` would
have produced for an uninterrupted run, in the same order (`matrix.py`'s
determinism invariant).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

from npw.churn import background_rate
from npw.matrix import Run, expand

# How long the runner sleeps after starting churn but before invoking
# scripts/trial.sh, so background Pod creation is already under way once
# trial.sh starts rather than starting simultaneously with it.
CHURN_WARMUP_S = 30

# Padding added on top of CHURN_WARMUP_S + duration when computing how
# long churn's own schedule should span, so that `_stop_churn`'s
# terminate() -- called only after scripts/trial.sh returns, i.e. after
# the measurement window has already closed -- always arrives long before
# churn's own schedule would otherwise run out on its own. That is what
# turns `cut_short=true` in scripts/churn.py's summary line into positive
# proof that churn was still actively scheduled, and being cut off, at the
# moment the trial actually finished: see `verify_churn`'s docstring for
# why that direction is the correct one (Task 7 fix-round-2 Critical A
# corrects an earlier, inverted version of this check, which required
# `cut_short=false` and therefore rejected the common case of a trial that
# finished *faster* than a fixed budget).
#
# Derived from summing scripts/trial.sh's own worst-case bounds on its
# success path (not "any plausible trial", which was too vague to check
# against -- Task 7 fix-round-3 Minor 5): wait_for_cold_node's 180s guard
# + the 60s `kubectl wait` for the prober Pod to report Running + the 1s
# watcher-warmup sleep + the `DURATION + 90`s `kubectl wait` for the
# prober Pod to report Succeeded + the 60s prober Pod delete and 120s
# namespace delete in its own cleanup = 180+60+1+120+60+120 = 541s at
# DURATION=30, plus a generous ~90s allowance for a cold `go build` of
# cmd/watcher (trial.sh's own comment above its `go build` call describes
# this as taking "tens of seconds" on a cold cache) -- roughly 630s
# summed. 900 leaves several hundred seconds of margin beyond even that
# pessimistic sum without requiring every one of those bounds to be hit
# simultaneously.
CHURN_OVERRUN_MARGIN_S = 900

# Mirrors scripts/trial.sh's own wait_for_cold_node bound exactly.
COLD_NODE_TIMEOUT_S = 180.0

# How long `reclaim_trial_namespace` waits for a leftover trial namespace
# to actually disappear. Mirrors scripts/trial.sh's own bound on the same
# operation (`kubectl delete namespace ... --timeout=120s` in its EXIT
# trap's cleanup), so the two agree on how long a namespace deletion is
# allowed to take on this node.
NAMESPACE_DELETE_TIMEOUT_S = 120.0


def interleave(runs: list[Run], cni: str) -> list[Run]:
    """Round-robin across churn levels, repetitions ascending, for one CNI.

    Grouping by repetition and keeping each group's runs in `runs`' own
    (matrix-order) sequence means every churn level for repetition N runs
    before any run of repetition N+1. That spreads a drift in machine
    state (thermal, cache warmth, prior trials' leftover load) across
    conditions instead of letting it load onto whichever condition
    happens to run last -- see this module's docstring and matrix.py's
    determinism invariant, which is what makes this order reproducible
    across a resume.
    """
    by_rep: dict[int, list[Run]] = defaultdict(list)
    for r in runs:
        if r.cni == cni:
            by_rep[r.repetition].append(r)
    return [r for rep in sorted(by_rep) for r in by_rep[rep]]


def pending(runs: list[Run], raw_root: Path) -> list[Run]:
    """Filter to runs that have not completed, per scripts/trial.sh's own marker.

    `checksums.sha256` is written last, atomically, by trial.sh, so its
    presence is exactly trial.sh's own definition of "this trial is done"
    -- reusing it here is what keeps the runner's idea of "pending" from
    ever disagreeing with trial.sh's idea of "already have this one".
    """
    return [r for r in runs if not (raw_root / r.run_id / "checksums.sha256").exists()]


def trial_env(run: Run, raw_root: Path, duration_s: int) -> dict[str, str]:
    """The exact six env vars scripts/trial.sh reads. No more, no fewer.

    `POLICY_AT` selects when trial.sh applies the NetworkPolicy relative
    to the victim (see that script's header). It is always passed, even
    for Experiment 1's `before`, so the script never has to guess from an
    unset variable which experiment it is running.

    `POLICY_DELAY_MS` is pinned to 0 rather than left unset. `main` runs
    trial.sh with `{**os.environ, **trial_env(...)}`, so anything not set
    here is inherited from whoever started the run -- and a delay exported
    during a positive-control session (`scripts/positive-control.sh` is
    `POLICY_AT=at-ready POLICY_DELAY_MS=2000`) would then be accepted by
    trial.sh on the `at-ready` arm, silently contradicting the
    `POLICY_DELAY_MS = 0` that `experiments/exp1b-ordering/matrix.yaml`
    freezes for it. Pinning it closes the environment against a stale
    export instead of leaving a frozen protocol parameter to chance. If a
    future experiment needs a non-zero delay, it becomes a `Run` field
    then, varied by the matrix like every other frozen parameter -- never
    an ambient environment variable.
    """
    return {
        "CNI": run.cni,
        "RUN_ID": run.run_id,
        "RUN_DIR": str(raw_root / run.run_id),
        "DURATION": str(duration_s),
        "POLICY_AT": run.policy_at,
        "POLICY_DELAY_MS": "0",
    }


def write_meta(
    run_dir: Path,
    run: Run,
    *,
    cni_version: str,
    k: int,
    probe_interval_ns: int,
    dial_timeout_ns: int,
    duration_s: int,
) -> None:
    """Record the frozen conditions this trial attempt runs under.

    trial.sh never writes meta.json -- this is the runner's own record,
    and it is written unconditionally, every time this is called,
    overwriting whatever was there before. That matters for a retried
    trial: if a first attempt crashed partway (no checksums.sha256, so
    `pending()` offers it again), the caller calls this again immediately
    before the second attempt's scripts/trial.sh, so the meta.json that
    ends up checksummed alongside that attempt's measurements always
    describes the attempt that actually produced them, never a stale copy
    left by the first one.

    `meta.json` is deliberately **not** an `internal/record.RunRecord`.
    CLAUDE.md names `internal/record/` as the Go<->Python contract, and
    this file also carries `schema_version: 1`, but the two are
    different shapes with independent version lines: `RunRecord` is the
    per-run record the Go binaries would write, and nothing in this
    pipeline writes one today. `meta.json` is the runner's own record of
    the frozen conditions a trial attempt ran under. Do not read a
    `meta.json` as a `RunRecord`, or conclude from the matching
    `schema_version` that they evolve together -- they do not.

    `repetition` is written exactly as `Run.repetition` gives it: 0-based,
    matching `matrix.py`'s own run_id numbering and this pipeline's
    determinism invariant. `internal/record.RunRecord.Repetition` in the
    Go side documents itself as 1-based, but nothing in this pipeline
    produces a Go RunRecord, so there is no live mismatch to reconcile --
    only two independent, currently-unconnected conventions. Renumbering
    here to match Go would change every run_id this project has already
    produced, for a doc comment on a struct nothing here writes.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema_version": 1,
        "run_id": run.run_id,
        "cni": run.cni,
        "cni_version": cni_version,
        "policy_set": run.policy_set,
        "policy_at": run.policy_at,
        "churn_rate_per_min": run.churn_rate_per_min,
        "background_churn_per_min": background_rate(run.churn_rate_per_min),
        "repetition": run.repetition,
        "sustained_k": k,
        "probe_interval_ns": probe_interval_ns,
        "dial_timeout_ns": dial_timeout_ns,
        "duration_s": duration_s,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def _terminating_trial_namespaces(namespace_status: str) -> list[str]:
    """Names of `t-*` namespaces currently Terminating.

    `namespace_status` is one "<name> <phase>" pair per line -- the exact
    shape `kubectl get namespace -o jsonpath=...` produces for both this
    function and scripts/trial.sh's own `wait_for_cold_node`, which this
    is intentionally kept in parity with (see `wait_for_cold_node` below
    for why the runner now runs this check too, not just trial.sh).
    """
    names = []
    for line in namespace_status.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].startswith("t-") and parts[1] == "Terminating":
            names.append(parts[0])
    return names


def _namespace_status() -> str:
    """`kubectl get namespace` as one "<name> <phase>" pair per line.

    The single place that jsonpath is written on the Python side (it is
    also written, identically, in scripts/trial.sh's own
    `wait_for_cold_node`). `check=True`: a kubectl that cannot reach the
    cluster must raise out to main()'s handler and stop the run, not be
    read as "no namespaces exist", which every caller here would take as
    a clean node.
    """
    return subprocess.run(
        [
            "kubectl",
            "get",
            "namespace",
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{" "}{.status.phase}{"\\n"}{end}',
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _namespace_present(namespace_status: str, ns: str) -> bool:
    """Whether `ns` appears at all in a `_namespace_status()` listing.

    Phase-agnostic, unlike `_terminating_trial_namespaces`: an `Active`
    leftover is precisely the case `reclaim_trial_namespace` exists to
    catch, and a `Terminating` one still has to be waited out before the
    name can be reused.
    """
    for line in namespace_status.splitlines():
        parts = line.split()
        if parts and parts[0] == ns:
            return True
    return False


def reclaim_trial_namespace(
    ns: str,
    timeout_s: float = NAMESPACE_DELETE_TIMEOUT_S,
    poll_interval_s: float = 1.0,
) -> bool:
    """Delete a leftover `t-<run_id>` and block until it is gone. True if one was there.

    The case this exists for: main() pre-creates `t-<run_id>`, starts
    churn, and sleeps `CHURN_WARMUP_S` before invoking scripts/trial.sh.
    If the process dies inside that window -- SIGKILL, a crash, or an
    exception before trial.sh's own EXIT trap takes over -- the namespace
    is left `Active`, holding up to ~29 churn Pods at `r = 60`. (The
    Ctrl-C path is already safe: SIGINT reaches trial.sh's own
    `trap 'cleanup; exit 130' INT`.)

    On resume `pending()` re-offers that same run id, `wait_for_cold_node`
    passes by design (it rejects only *Terminating* `t-*` namespaces, and
    must, because main()'s own pre-create leaves an Active one), and the
    idempotent `kubectl apply` succeeds -- so without this, the retried
    trial would measure a CNI that already holds warm identity and
    policy-realisation state for exactly that namespace. That is the
    confound preregistration.md Addendum 1 A4 exists to eliminate, it
    biases `t_blocked` earlier (toward this project's own negative
    result), and the trial carries no marker saying so.

    Delete-and-wait rather than a strict `kubectl create namespace`: a
    strict create would fail the trial loudly, but `pending()` would then
    re-offer the same run id forever, breaking the resumability the
    270-trial plan depends on. Deleting first makes a resume both work
    and cold.

    Raises `TimeoutError` if the namespace is still there after
    `timeout_s`, matching `wait_for_cold_node`'s shape: failing loudly is
    correct, since the alternative is running the trial against the warm
    namespace this function was called to remove.
    """
    if not _namespace_present(_namespace_status(), ns):
        return False
    print(
        f"reclaiming leftover trial namespace {ns} (an interrupted earlier attempt at this "
        "run id); its churn Pods would otherwise leave the CNI warm for this namespace "
        "(preregistration.md Addendum 1 A4)",
        file=sys.stderr,
    )
    subprocess.run(
        ["kubectl", "delete", "namespace", ns, "--ignore-not-found", "--wait=false"],
        check=True,
        capture_output=True,
        text=True,
    )
    deadline = time.monotonic() + timeout_s
    while _namespace_present(_namespace_status(), ns):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"leftover trial namespace {ns} still exists {timeout_s}s after being deleted; "
                "refusing to run this trial against a namespace the CNI is already warm for"
            )
        time.sleep(poll_interval_s)
    return True


def wait_for_cold_node(
    timeout_s: float = COLD_NODE_TIMEOUT_S, poll_interval_s: float = 1.0
) -> None:
    """Block until no `t-*` namespace is Terminating and no prober Pod lingers.

    This duplicates scripts/trial.sh's own `wait_for_cold_node`, run here
    too, before churn starts (Task 7 fix-round-1 Critical 1b). Without
    this, when the *previous* trial's namespace is still tearing down,
    trial.sh's own guard is what blocks -- for up to `timeout_s` -- but
    only *after* this runner has already started churn and slept through
    `CHURN_WARMUP_S`, so churn's entire window burns against a namespace
    that either doesn't exist yet or belongs to the trial that is still
    finishing. Running the same check here first makes trial.sh's own
    guard a no-op by the time it runs, which removes that failure mode
    outright instead of merely budgeting for it.

    Raises `TimeoutError` if the node is not cold within `timeout_s`,
    mirroring trial.sh's own fail-fast behaviour rather than starting
    churn (and burning its whole window) against a node that will make
    the trial fail anyway once trial.sh's own guard times out.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        namespace_status = _namespace_status()
        prober_pods = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                "prober",
                "-o",
                'jsonpath={range .items[*]}{.metadata.name}{" "}{end}',
            ],
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        terminating = _terminating_trial_namespaces(namespace_status)
        if not terminating and not prober_pods.strip():
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"node not cold after {timeout_s}s: terminating trial namespaces "
                f"{terminating or ['none']}; leftover prober pods {prober_pods.split() or ['none']}"
            )
        time.sleep(poll_interval_s)


_CHURN_SUMMARY_RE = re.compile(
    r"^churn: namespace=(?P<namespace>\S+) scheduled=(?P<scheduled>\d+) "
    r"attempted=(?P<attempted>\d+) created=(?P<created>\d+) "
    r"create_failed=(?P<create_failed>\d+) deleted=(?P<deleted>\d+) "
    r"delete_failed=(?P<delete_failed>\d+) cut_short=(?P<cut_short>true|false) "
    r"elapsed_s=(?P<elapsed_s>\d+\.\d+) "
    r"first_create_ts=(?P<first_create_ts>none|\d+\.\d+) "
    r"last_create_ts=(?P<last_create_ts>none|\d+\.\d+)$"
)


def _parse_churn_summary(log_text: str) -> dict[str, str] | None:
    """The last line in a churn.log matching scripts/churn.py's summary shape, or None.

    Scanned from the end: per-failure warning lines (`churn: failed to
    create ...`) share the `churn: ` prefix but not this exact shape, and
    the real summary is always the last thing scripts/churn.py prints
    before exiting -- on every exit path, including SIGTERM, since Task 7
    fix-round-1 Important 3 made that unconditional.
    """
    for line in reversed(log_text.splitlines()):
        m = _CHURN_SUMMARY_RE.match(line.strip())
        if m:
            return m.groupdict()
    return None


def achieved_rate_per_min(summary: dict[str, str] | None) -> float | None:
    """Background Pods per minute churn actually created, or None if unknowable.

    The configured rate is in `meta.json`; this is the *achieved* one --
    the experiment's independent variable as it actually happened. It is
    derived from scripts/churn.py's own summary line: `created` over
    `elapsed_s`, the monotonic span the driver's schedule loop ran for.

    This is deliberately **recorded, not gated on**. No threshold here
    fails a trial for a degraded rate: choosing a tolerance with no pilot
    data in hand would be exactly the post-hoc calibration
    experiments/exp1-window/preregistration.md's Amendment policy forbids,
    and Addendum 1 B2 already refused to pick such a number for the B/C
    divergence threshold on the same grounds. Addendum 1 A3 records that
    the tolerance is the pilot's to set, as a dated amendment.

    `None` (rather than a number) when there is no summary line, when
    `elapsed_s` is not positive, or when either field is missing or
    non-numeric: an unrecordable rate is reported as unrecorded, never as
    zero, which would read as "churn created nothing".
    """
    if summary is None:
        return None
    try:
        elapsed_s = float(summary["elapsed_s"])
        created = float(summary["created"])
    except (KeyError, TypeError, ValueError):
        return None
    if elapsed_s <= 0:
        return None
    return created * 60.0 / elapsed_s


_CREATE_FAILURE_PREFIX = "churn: failed to create "
_CREATE_FAILURE_TS_RE = re.compile(r"^churn: failed to create \S+ in \S+ at ts=(?P<ts>\d+\.\d+)\b")


def _create_failure_timestamps(log_text: str) -> list[float | None]:
    """Wall-clock (`time.time()`) timestamps of churn Pod creation failures.

    `None` stands for a failure line this function could not parse a
    timestamp out of, rather than that line being skipped -- which is
    what makes an unparseable failure line fail closed in `verify_churn`:
    a creation failure this module cannot place in time is treated as if
    it happened during the measurement window, since nothing here proves
    otherwise.
    """
    timestamps: list[float | None] = []
    for line in log_text.splitlines():
        line = line.strip()
        if not line.startswith(_CREATE_FAILURE_PREFIX):
            continue
        m = _CREATE_FAILURE_TS_RE.match(line)
        timestamps.append(float(m.group("ts")) if m else None)
    return timestamps


def _window_close_s(run_dir: Path) -> float:
    """Wall-clock instant a trial's measurement window actually closed.

    An earlier version of this function returned
    `run_epoch_ns / 1e9 + duration_s`. That is systematically too early
    (Task 7 fix-round-3 Important 1): `run_epoch_ns` (scripts/trial.sh
    :187) is taken *before* the namespace/policy/prober applies, the
    prober's own `kubectl wait ... Running` (bounded 60s), the watcher's
    1s warmup sleep, and the victim apply -- and cmd/prober's own
    `-duration` deadline starts only once its probe loop actually begins,
    after `waitForTargetIP` resolves (cmd/prober/main.go), not at
    `run_epoch_ns`. So the real close is
    `run_epoch_ns + T_startup + duration`, and using `duration` alone
    cuts the window short by `T_startup` -- fail-open over the tail of
    every measurement window, worse exactly when startup is slow (cluster
    pressure), which is also when a churn creation is more likely to
    genuinely fail: the two error modes are adversely correlated.

    The fix uses data already being checksummed rather than re-deriving
    `T_startup`: `prober.jsonl`'s last observation records `offset_ns`
    immediately before that attempt's dial, measured via `time.Since`
    from the same `run_epoch_ns` origin (see cmd/prober/main.go's
    `observation` and `probeLoop`) -- so it *is*, by construction, the
    window's close relative to `run_epoch_ns`, with no separate estimate
    of `T_startup` needed at all.

    Clock assumption: `run_epoch_ns` comes from the *host* wall clock
    (scripts/trial.sh :191, `time.time_ns()`), while `last_offset_ns` is a
    node-side offset computed inside the kind node. Adding them assumes
    those two clocks agree. On Linux and in CI the node shares the host's
    clock, so they do. On Docker Desktop the node runs in a VM whose
    clock can drift: a VM running behind the host makes this close time
    too early, which is fail-open (an in-window creation failure could be
    scored as after the window). The *reported window itself* is
    unaffected either way -- `t_ready` and `t_blocked` are both node-side
    offsets from the same epoch, so any skew cancels in their difference;
    only this verification boundary is exposed to it.

    Raises `FileNotFoundError`, `json.JSONDecodeError`, `KeyError`,
    `TypeError` (a present but wrong-typed field), or `ValueError` (empty
    `prober.jsonl`) if either file is missing, malformed, empty or
    wrong-typed. The caller (`main()`) treats any of these as a
    verification failure rather than letting the exception escape and
    leave a trial with an intact `checksums.sha256` that was never
    actually churn-verified (Task 7 fix-round-3 Important 2).
    """
    trial = json.loads((run_dir / "trial.json").read_text())
    last_offset_ns = None
    for line in (run_dir / "prober.jsonl").read_text().splitlines():
        line = line.strip()
        if line:
            last_offset_ns = json.loads(line)["offset_ns"]
    if last_offset_ns is None:
        raise ValueError(f"{run_dir / 'prober.jsonl'} has no observations")
    return trial["run_epoch_ns"] / 1e9 + last_offset_ns / 1e9


def verify_churn(log_text: str, window_close_s: float) -> tuple[bool, str]:
    """Whether a trial's churn actually covered its measurement window.

    `cut_short=true` (scripts/churn.py's own summary line) is *required*,
    not merely tolerated. churn's own schedule length
    (`CHURN_WARMUP_S + CHURN_OVERRUN_MARGIN_S` past `duration`) is padded
    far beyond any plausible trial length specifically so that
    `_stop_churn`'s terminate() -- called only after scripts/trial.sh has
    returned, i.e. only after the measurement window has already closed
    -- always arrives before churn exhausts that schedule on its own.
    `cut_short=true` is therefore *positive proof* that churn was still
    actively scheduled, and being cut off by that terminate(), at the
    moment the trial actually finished: i.e. that it covered the whole
    window. `cut_short=false` means the opposite -- churn ran out of
    scheduled creations on its own, at some point *before* the trial
    finished -- which is a real gap regardless of how the trial's own
    timing happened to land.

    (An earlier version of this function required `cut_short=false`,
    treating churn simply reaching the end of a fixed-length schedule as
    success. That inverted the actual guarantee: with a deliberately
    generous schedule, `cut_short=false` could only happen if the trial
    ran *longer* than the schedule anticipated, which a healthy trial that
    finishes faster than expected can never satisfy -- see Task 7
    fix-round-2 Critical A.)

    A creation failure only counts against a trial if it happened at or
    before `window_close_s`. scripts/trial.sh's own EXIT trap deletes the
    trial namespace as soon as it returns, and `_stop_churn` only stops
    churn after that returns -- so a handful of "namespace not found"
    creation failures in the last moments before churn is signalled are
    an accepted, harmless race (Task 7 fix-round-1 Ruling 3), not evidence
    of a degraded rate, and must not fail a trial on their own (Task 7
    fix-round-2 Important C).
    """
    summary = _parse_churn_summary(log_text)
    if summary is None:
        return False, "no churn summary found in churn.log"
    if summary["cut_short"] != "true":
        return False, (
            f"churn exhausted its own schedule (attempted {summary['attempted']}/"
            f"{summary['scheduled']}) before this trial finished, so it cannot "
            "have covered the whole measurement window"
        )
    failures = _create_failure_timestamps(log_text)
    in_window = [ts for ts in failures if ts is None or ts <= window_close_s]
    if in_window:
        return False, (
            f"{len(in_window)} churn Pod creation(s) failed at or before the "
            f"measurement window closed (window_close_s={window_close_s:.3f})"
        )
    if failures:
        return (
            True,
            f"ok ({len(failures)} churn Pod creation(s) failed after the window closed; accepted)",
        )
    return True, "ok"


def _check_churn(run_dir: Path, log_path: Path) -> tuple[bool, str]:
    """`verify_churn`, but failing (never raising) when its inputs can't be read.

    `_window_close_s` reads `trial.json` and `prober.jsonl`, both written
    by scripts/trial.sh immediately before it writes `checksums.sha256`,
    so in the ordinary case they exist and parse. But main() calls this
    only after `checksums.sha256` already exists (rc == 0) -- so if
    `_window_close_s` raised uncaught here, that exception would propagate
    out of main() itself, leaving the run directory with an intact
    completion marker that `pending()` treats as "done" on the next
    invocation, even though its churn was never actually verified. That
    is the fail-open case Task 7 fix-round-3 Important 2 flagged: it must
    not be possible for a trial to enter the dataset by virtue of a
    verification step crashing. Catching the narrow set of exceptions
    `_window_close_s` documents and turning them into an ordinary failed
    verification -- routed through `_move_to_failed` exactly like a real
    churn failure -- closes that gap without weakening what
    `_window_close_s` itself raises on.

    `churn.log` is read *inside* the same guard, not by the caller: an
    unreadable churn.log is the same fail-open case (the exception would
    escape main() with `checksums.sha256` intact) and deserves the same
    handling, not a path around the guard.

    `TypeError` is in the caught set alongside `OSError`/`ValueError`/
    `KeyError` for the same reason: `json.loads` will happily return
    `{"run_epoch_ns": null}` or `{"offset_ns": [1]}`, and the arithmetic
    in `_window_close_s` then raises `TypeError`, not `ValueError` -- a
    wrong-*typed* field must fail the trial exactly like a missing one,
    rather than escaping with the completion marker in place.
    """
    try:
        window_close_s = _window_close_s(run_dir)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        return False, f"could not determine the measurement window close time: {exc!r}"
    try:
        log_text = log_path.read_text()
    except OSError as exc:
        return False, f"could not read {log_path}: {exc!r}"
    return verify_churn(log_text, window_close_s)


def _run_churn(
    ns: str, background_per_min: int, duration_s: float, log_path: Path
) -> subprocess.Popen:
    """Start the background churn driver for one trial, logging to `log_path`.

    Task 6's driver prints per-failure warnings and a created/failed
    summary specifically so a degraded achieved rate is not invisible
    (the achieved rate is this experiment's independent variable, and
    meta.json only records the *configured* one). Redirecting stdout and
    stderr to a file next to the trial's other artifacts -- rather than
    leaving them on the runner's own inherited stdio, where they would be
    unrecoverable scrollback in an unattended 7.5-hour run -- is what
    keeps that output from vanishing into a detached subprocess, and is
    also what `verify_churn()` reads. It is named *.log, not *.jsonl, so
    it lands outside scripts/trial.sh's checksums glob deliberately: like
    watcher.log, it is a debugging / audit aid, not measurement data --
    the pass/fail decision it feeds happens in this module, before a
    trial's checksums are allowed to stand.
    """
    with log_path.open("w") as log_file:
        # The child inherits its own duplicated fd on spawn, so the parent's
        # handle can close as soon as Popen returns instead of staying open
        # (and accumulating, across up to 270 trials) for the rest of this
        # process's life.
        return subprocess.Popen(
            [sys.executable, "scripts/churn.py", ns, str(background_per_min), str(duration_s)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )


def _achieved_rate_line(log_text: str, configured_per_min: int) -> str:
    """The one line this runner appends to a trial's churn.log recording the achieved rate.

    Written with a `runner: ` prefix, not `churn: `, so it cannot be
    confused with -- or parsed as -- anything scripts/churn.py itself
    printed: `_parse_churn_summary` and `_create_failure_timestamps` both
    key off the driver's own prefix and shape, and this line matches
    neither.
    """
    achieved = achieved_rate_per_min(_parse_churn_summary(log_text))
    shown = "unrecordable" if achieved is None else f"{achieved:.3f}"
    return (
        f"runner: churn rate configured_per_min={configured_per_min} "
        f"achieved_per_min={shown} (recorded, not a pass/fail criterion; "
        "see npw.runner.achieved_rate_per_min)"
    )


def _stop_churn(
    proc: subprocess.Popen, run_id: str, log_path: Path, configured_per_min: int
) -> None:
    """Stop a trial's churn driver, if it is still running, and surface its summary.

    The achieved rate is appended to `log_path` as well as printed:
    stderr in an unattended multi-hour run is scrollback, and Addendum 1
    A3's claim that "a trial's achieved rate is checkable against its
    configured one" is only true if the achieved rate is on disk next to
    the trial it belongs to.

    `proc.poll()` is checked first so a process that has already exited
    isn't sent a pointless signal. Under `CHURN_OVERRUN_MARGIN_S`'s
    deliberately long schedule, a churn process exiting on its own before
    this is called is *not* the ordinary case any more (that framing was
    true of an earlier, much shorter schedule -- see Task 7 fix-round-2
    Critical A): it now means churn ran out of its own schedule before
    the trial finished, which is exactly the condition `verify_churn`
    fails a trial for via `cut_short=false`. This function does not make
    that judgment itself; it only stops whatever is still running and
    leaves the call to `verify_churn`, which reads what this function
    leaves at `log_path`.

    A plain terminate()+wait() with no timeout would let one hung kubectl
    call inside the driver stall this trial -- and, since main() stops on
    the first failing trial, potentially the rest of a 270-trial run --
    forever. The kill() fallback bounds that.
    """
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    log_text = log_path.read_text()
    lines = [line for line in log_text.splitlines() if line.strip()]
    if lines:
        print(f"{run_id}: {lines[-1]}", file=sys.stderr)
    else:
        print(f"{run_id}: churn produced no output in {log_path}", file=sys.stderr)
    rate_line = _achieved_rate_line(log_text, configured_per_min)
    with log_path.open("a") as log_file:
        log_file.write(rate_line + "\n")
    print(f"{run_id}: {rate_line}", file=sys.stderr)


def _move_to_failed(raw_root: Path, run_dir: Path, run: Run, reason: str) -> Path:
    """Move a trial whose churn could not be verified aside, with a REASON.md.

    "Aside" is `data/raw/exp1-failed/<run_id>/`, which is *inside*
    `data/raw/` -- as the plan specifies. Nothing leaves `data/raw/`
    here, and nothing in it is deleted or overwritten; the move only
    takes the directory out of `--raw-root`, so `pending()` re-offers the
    run id.

    `data/raw/` is immutable (CLAUDE.md), and scripts/trial.sh enforces
    that itself by refusing to run again over an existing
    `checksums.sha256` ("data/raw/ is immutable ..., so a re-run of the
    same run id is a bug to be reported, not data to be overwritten" --
    scripts/trial.sh's own comment). An earlier version of this function
    deleted just that marker to force a retry, which is deleting a file
    from data/raw/ specifically to disarm the interlock that enforces the
    project's own immutability rule -- and the retry that follows then
    truncates every other file next to it (prober.jsonl, trial.json,
    meta.json, this trial's own churn.log, ...) in place, destroying the
    only record of why verification failed (Task 7 fix-round-2 Critical
    B).

    Moving the whole directory to `<raw_root's parent>/<raw_root's
    name>-failed/<run_id>/` instead -- e.g. `data/raw/exp1-failed/<run_id>/`
    for the default `--raw-root` -- satisfies `pending()` re-offering this
    run_id (no `checksums.sha256` remains at the original path) without
    deleting or overwriting anything that was in data/raw/, and matches
    the plan's own procedure for a failed trial (docs/plans/
    2026-09-19-three-cni-experiment-1.md: "move it to
    data/raw/exp1-failed/<run_id>/ with a REASON.md, and let the runner
    redo the trial"). If a directory already exists at the destination
    (an earlier failure of the same run_id that has not yet been cleaned
    up), this disambiguates with a numeric suffix rather than clobbering
    it -- an earlier failure is itself data.

    `REASON.md` is written into `run_dir` *before* the rename, not after
    (Task 7 fix-round-3 Minor 3): the reason a trial was discarded is the
    only explanation for the discard, and an `OSError` from the rename
    itself (cross-device move, permissions, a full disk) must not be able
    to take that explanation down with it. Writing it first means it is
    already on disk -- at the original path if the rename then fails, at
    the new one if it succeeds -- either way it survives.
    """
    failed_root = raw_root.parent / f"{raw_root.name}-failed"
    destination = failed_root / run_dir.name
    suffix = 1
    while destination.exists():
        destination = failed_root / f"{run_dir.name}-{suffix}"
        suffix += 1
    (run_dir / "REASON.md").write_text(
        f"# {run.run_id} failed churn verification\n\n"
        f"- cni: {run.cni}\n"
        f"- churn_rate_per_min: {run.churn_rate_per_min}\n"
        f"- policy_set: {run.policy_set}\n"
        f"- repetition: {run.repetition}\n\n"
        f"{reason}\n"
    )
    failed_root.mkdir(parents=True, exist_ok=True)
    run_dir.rename(destination)
    return destination


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--matrix", default="experiments/exp1-window/matrix.yaml")
    p.add_argument("--cni", required=True)
    p.add_argument("--cni-version", required=True)
    p.add_argument("--raw-root", default="data/raw/exp1")
    p.add_argument("--duration", type=int, default=30)
    p.add_argument("--limit", type=int, default=None, help="run at most N pending trials (pilots)")
    a = p.parse_args(argv)

    spec = yaml.safe_load(Path(a.matrix).read_text())
    raw_root = Path(a.raw_root)
    todo = pending(interleave(expand(spec), a.cni), raw_root)[: a.limit]
    print(f"{len(todo)} trials pending for {a.cni}", file=sys.stderr)

    for run in todo:
        run_dir = raw_root / run.run_id
        # Written fresh before every attempt of this run -- see write_meta's
        # own docstring for why that is what makes a retry correct.
        write_meta(
            run_dir,
            run,
            cni_version=a.cni_version,
            k=3,
            # These two are the Python-side copy of deploy/workloads/
            # prober.yaml's `-interval=1ms` and `-timeout=200ms` (its
            # `args:`, around :82-83). Nothing cross-checks them at run
            # time, and they must not drift: `probe_interval_ns` is the
            # `p` that npw.analysis.report._all_at_or_below_floor
            # compares every window against, so it is the predicate
            # behind the pre-registered "no window detected above the
            # floor" phrase. Change one, change the other.
            probe_interval_ns=1_000_000,
            dial_timeout_ns=200_000_000,
            duration_s=a.duration,
        )

        churn: subprocess.Popen | None = None
        churn_log = run_dir / "churn.log"
        background_per_min = background_rate(run.churn_rate_per_min)

        try:
            if background_per_min:
                ns = f"t-{run.run_id}"
                # Must happen before churn starts, not just before trial.sh
                # -- see wait_for_cold_node's own docstring.
                wait_for_cold_node()
                # A resume can find `ns` already there, Active, holding an
                # interrupted earlier attempt's churn Pods -- see
                # reclaim_trial_namespace's docstring. Deleting it puts it
                # into Terminating, which is exactly the state
                # wait_for_cold_node exists to exclude, so that check is
                # re-run afterwards rather than trusted from before.
                if reclaim_trial_namespace(ns):
                    wait_for_cold_node()
                # Pre-created only so churn has a namespace to warm up in
                # before the victim exists; scripts/trial.sh's own
                # wait_for_cold_node deliberately tolerates an Active (not
                # Terminating) t-* namespace, so this does not trip its
                # guard (now doubly true: the wait above already ensures
                # no Terminating t-* namespace exists before this runs).
                # trial.sh re-applies the same namespace idempotently, so
                # this is not a race with it. No churn -> no namespace is
                # created here at all, since a churn-less trial has
                # nothing to warm up.
                manifest = subprocess.run(
                    ["kubectl", "create", "namespace", ns, "--dry-run=client", "-o", "yaml"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                subprocess.run(
                    ["kubectl", "apply", "-f", "-"],
                    input=manifest,
                    check=True,
                    text=True,
                    stdout=subprocess.DEVNULL,
                )
                # See CHURN_OVERRUN_MARGIN_S for why this is padded far
                # past warmup + duration, and verify_churn's docstring for
                # what that padding is actually for: making cut_short=true
                # the expected, provable outcome of a healthy trial.
                churn = _run_churn(
                    ns,
                    background_per_min,
                    CHURN_WARMUP_S + a.duration + CHURN_OVERRUN_MARGIN_S,
                    churn_log,
                )
                time.sleep(CHURN_WARMUP_S)

            rc = subprocess.run(
                ["scripts/trial.sh"],
                env={**os.environ, **trial_env(run, raw_root, a.duration)},
                check=False,
            ).returncode
        except (subprocess.SubprocessError, TimeoutError, OSError) as exc:
            # subprocess.SubprocessError (covers CalledProcessError from
            # kubectl apply failing against a Terminating namespace,
            # check=True above), TimeoutError (wait_for_cold_node timing
            # out), OSError (covers FileNotFoundError if scripts/trial.sh
            # isn't found because cwd isn't the repo root). `finally` below
            # still stops churn in every one of these cases -- and on
            # KeyboardInterrupt/SystemExit, which this clause deliberately
            # does not catch but which `finally` still runs under --
            # without it, churn would be orphaned, still creating Pods
            # into the trial namespace for the rest of its window, for as
            # long as it takes an operator to notice and kill it by hand.
            print(
                f"trial {run.run_id} raised {exc!r} before completing; "
                "stopping so the failure is investigated, not absorbed",
                file=sys.stderr,
            )
            return 1
        finally:
            if churn is not None:
                _stop_churn(churn, run.run_id, churn_log, background_per_min)

        if rc != 0:
            print(
                f"trial {run.run_id} failed rc={rc}; stopping so the failure is investigated, not absorbed",
                file=sys.stderr,
            )
            return rc

        if churn is not None:
            # The log is read inside _check_churn, not here: an OSError
            # from this read would escape main() with checksums.sha256
            # already in place, which is the same fail-open case
            # _check_churn's own guard exists to close.
            ok, reason = _check_churn(run_dir, churn_log)
            if not ok:
                # Printed before the move (Task 7 fix-round-3 Minor 3):
                # if _move_to_failed itself raises (e.g. the rename hits
                # an OSError), this line -- the only explanation of why
                # the trial was discarded -- has already reached stderr
                # rather than being lost behind that traceback. REASON.md
                # inside _move_to_failed is the second, persisted copy of
                # the same explanation.
                print(
                    f"trial {run.run_id} completed but its churn is unverified ({reason}); "
                    "moving it under "
                    f"{raw_root.parent / f'{raw_root.name}-failed'} for review "
                    "(docs/plans/2026-09-19-three-cni-experiment-1.md's failed-trial procedure); "
                    "stopping so the failure is investigated, not absorbed",
                    file=sys.stderr,
                )
                destination = _move_to_failed(raw_root, run_dir, run, reason)
                print(f"trial {run.run_id}: moved to {destination}", file=sys.stderr)
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
