#!/usr/bin/env python3
"""Create/delete background Pods in a trial namespace at a churn rate.

Usage: scripts/churn.py <namespace> <background_rate_per_min> <duration_s> [pod_lifetime_s=15]

Each Pod uses the netpol-window/victim:dev image (loaded into kind ahead
of trials by Task 7's runner) and carries label role=churn: the
default-deny policy's podSelector {} matches it, so the CNI agent has to
realise policy for it concurrently with the victim, but the prober's
role=victim selector does not, so a churn Pod is never mistaken for the
measurement target.

This is a thin driver over npw.churn.churn_schedule: it owns only the
kubectl subprocess calls and the wall-clock wait between them. It is
deliberately not unit-tested -- the scheduling decision it calls is what
carries the tests (tests/test_churn.py); there is nothing left here to
assert against that would not just restate the subprocess calls.

Run as `uv run python scripts/churn.py ...` so `npw` resolves on the
import path.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

from npw.churn import churn_schedule

# Set by _handle_sigterm when the runner (Task 7's npw.runner) stops this
# trial's churn after scripts/trial.sh returns. Checked in the schedule
# loop below so the summary line is still printed on the way out --
# without this, SIGTERM's default disposition kills the process between
# one loop iteration and the next, before the `print` after the loop ever
# runs, and the achieved-rate evidence Task 6 exists to produce is lost at
# exactly the moment (an early stop) it matters most.
_stop_requested = False


def _handle_sigterm(signum: int, frame: object) -> None:
    global _stop_requested
    _stop_requested = True


# The realisation cost this experiment's churn-rate variable manipulates
# is on Pod *creation* (see npw.churn's module docstring), so -- like the
# victim (deploy/workloads/victim.yaml) -- a churn Pod must not linger in
# Terminating under the default 30s grace period; that would depress the
# actually-achieved creation rate below the configured one and slow the
# next trial's cold-node guard (scripts/trial.sh). `kubectl run` has no
# dedicated flag for this: its own `--grace-period` is documented as
# applying only to deletion ("Can only be set to 0 when --force is true
# (force deletion)" -- `kubectl run --help`), so it is set here via a
# strategic-merge override on the generated Pod spec instead.
_ZERO_GRACE_OVERRIDES = '{"apiVersion":"v1","spec":{"terminationGracePeriodSeconds":0}}'


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        print(__doc__, file=sys.stderr)
        return 2

    signal.signal(signal.SIGTERM, _handle_sigterm)

    namespace, rate_per_min, duration_s = argv[1], float(argv[2]), float(argv[3])
    pod_lifetime_s = float(argv[4]) if len(argv) > 4 else 15.0

    # Counts, not just a best-effort attempt: the achieved churn rate is
    # this experiment's independent variable, and a kubectl failure here
    # (image not pre-loaded into kind, resource pressure, quota) must be
    # visible to whoever is watching a live pilot, not merely unraised.
    # `meta.json`'s configured rate is the runner's concern (Task 7); this
    # driver's job stops at making its own outcomes observable.
    schedule = churn_schedule(rate_per_min, duration_s)
    attempted = 0
    created = 0
    create_failed = 0
    deleted = 0
    delete_failed = 0
    # Wall-clock (time.time()) stamps of the first and last *successful*
    # creation. With `elapsed_s` below, these are what make the achieved
    # creation rate -- this experiment's independent variable -- a
    # recorded number rather than an assumption: `kubectl run` latency
    # approaching the schedule's period would silently make the loop fall
    # behind, and counts alone cannot show that (see
    # experiments/exp1-window/preregistration.md, Addendum 1 A3).
    first_create_ts: float | None = None
    last_create_ts: float | None = None

    start = time.monotonic()
    live: list[tuple[float, str]] = []
    for index, offset in enumerate(schedule):
        while time.monotonic() - start < offset:
            if _stop_requested:
                break
            time.sleep(0.01)
        if _stop_requested:
            break

        attempted += 1
        name = f"churn-{index:04d}"
        # stderr is captured (not discarded) so a failed creation leaves a
        # trail instead of vanishing silently -- kubectl still isn't
        # `check=True`'d, since one failed churn Pod must not crash the
        # driver and abort the rest of the schedule.
        result = subprocess.run(
            [
                "kubectl",
                "run",
                name,
                "-n",
                namespace,
                "--image=netpol-window/victim:dev",
                "--image-pull-policy=IfNotPresent",
                "--labels=role=churn",
                "--restart=Never",
                f"--overrides={_ZERO_GRACE_OVERRIDES}",
                "--",
                "-addr=:8080",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            created += 1
            last_create_ts = time.time()
            if first_create_ts is None:
                first_create_ts = last_create_ts
            live.append((time.monotonic() + pod_lifetime_s, name))
        else:
            # Not added to `live`: a Pod that was never created has
            # nothing to reap later.
            create_failed += 1
            # `ts=` is a wall-clock (time.time(), not time.monotonic())
            # timestamp: Task 7's runner compares it against the instant
            # the measurement window closed, which `npw.runner
            # ._window_close_s` derives as `run_epoch_ns` (trial.json)
            # plus the offset of the last observation in prober.jsonl --
            # not `run_epoch_ns + duration`, which an earlier version of
            # that function used and which is early by the trial's whole
            # startup interval. The comparison tells a failure during the
            # measurement window (a real degraded rate) apart from one
            # after it (scripts/trial.sh's own EXIT trap has by then
            # already started deleting the trial namespace -- an
            # accepted, harmless race). Both sides are wall-clock
            # seconds, so the two are directly comparable.
            print(
                f"churn: failed to create {name} in {namespace} at ts={time.time():.6f} "
                f"(kubectl exit {result.returncode}): {result.stderr.strip()}",
                file=sys.stderr,
            )

        # Reaping happens only here, tied to the next scheduled creation --
        # not on a timer of its own. That is deliberate: after the final
        # creation in the schedule, any churn Pods still short of their
        # lifetime are left undeleted when this process exits. In
        # practice that is fine, because the caller (scripts/trial.sh, via
        # Task 7's runner) deletes the whole trial namespace immediately
        # after the churn window ends, which reclaims them; a self-timed
        # reaper thread here would only be deleting Pods that are about to
        # be deleted anyway.
        #
        # `now` is read once and reused for the whole filter below, so
        # every live Pod is judged against the same instant. Calling
        # time.monotonic() again per Pod (as in an earlier draft of this
        # driver) would let it advance mid-filter, so two Pods scheduled
        # to expire at the same due time could be judged inconsistently
        # against each other for no reason.
        now = time.monotonic()
        due = [pod for pod in live if pod[0] <= now]
        for due_at, pod_name in due:
            result = subprocess.run(
                ["kubectl", "delete", "pod", pod_name, "-n", namespace, "--wait=false"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            live.remove((due_at, pod_name))
            if result.returncode == 0:
                deleted += 1
            else:
                delete_failed += 1
                print(
                    f"churn: failed to delete {pod_name} in {namespace} "
                    f"(kubectl exit {result.returncode}): {result.stderr.strip()}",
                    file=sys.stderr,
                )

    # cut_short is derived from `attempted` vs `len(schedule)`, not from
    # `_stop_requested` alone: a SIGTERM that arrives after the very last
    # scheduled creation (the ordinary case, since the runner stops this
    # process once scripts/trial.sh returns) has nothing left to cut, and
    # must not be reported as if it did. This line is the caller's only
    # evidence of what churn actually achieved -- Task 7's runner fails a
    # trial on `cut_short=false` or an in-window creation failure, and
    # records (does not gate on) the achieved rate derived from the
    # timing fields below -- so it is printed on every exit path from the
    # loop above, not only the one where the schedule ran to completion.
    cut_short = attempted < len(schedule)
    # `elapsed_s` and the two creation stamps are what let a reader --
    # and npw.runner -- compute the rate churn actually achieved, instead
    # of only how many creations it attempted. Without a duration the
    # counts are unitless: a loop that fell behind because `kubectl run`
    # took nearly a whole period reports the same counts as one that kept
    # cadence, and (because falling behind makes `cut_short=true` *more*
    # likely) the existing cut_short check cannot tell them apart. The
    # stamps are wall-clock, on the same basis as `ts=` above; `elapsed_s`
    # is monotonic, so it is unaffected by a wall-clock step.
    elapsed_s = time.monotonic() - start
    print(
        f"churn: namespace={namespace} scheduled={len(schedule)} attempted={attempted} "
        f"created={created} create_failed={create_failed} "
        f"deleted={deleted} delete_failed={delete_failed} cut_short={str(cut_short).lower()} "
        f"elapsed_s={elapsed_s:.6f} "
        f"first_create_ts={'none' if first_create_ts is None else f'{first_create_ts:.6f}'} "
        f"last_create_ts={'none' if last_create_ts is None else f'{last_create_ts:.6f}'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
