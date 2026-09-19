#!/usr/bin/env bash
# Runs exactly one measurement trial against an already-running cluster.
# Bringing the cluster up and tearing it down is someone else's job
# (scripts/collect.sh for a developer pass, src/npw/runner.py for the
# experiment matrix); this script owns one trial and nothing else.
#
# Required env: CNI, RUN_ID, RUN_DIR, DURATION (seconds).
# Optional env: KUBECONFIG, and any churn driver the caller already has
#   running against this trial's namespace.
#
# Writes prober.jsonl, victim.jsonl, cri.jsonl, watcher.jsonl,
# watcher.log, trial.json and checksums.sha256 into $RUN_DIR. It never
# touches a $RUN_DIR that already holds a completed trial: data/raw/ is
# immutable (CLAUDE.md), so a re-run of the same run id is a bug to be
# reported, not data to be overwritten.
#
# Two properties of this ordering are the scientific content of this
# script, not incidental plumbing:
#
#   Policy first. The NetworkPolicy is applied before the victim
#   Deployment exists. The research question is about a Pod starting up
#   under a policy that is already present; applying the policy to an
#   already-serving Pod measures a different phenomenon (that is the
#   teardown/late-policy case, closer to CVE-2024-7598's shape).
#
#   A fresh, and genuinely cold, namespace per trial. Repeating trials in
#   one namespace with an identical Pod label set lets the CNI reuse
#   cached identity and policy state, so a later trial can see enforcement
#   land sooner purely because the caches are warm. Each trial therefore
#   runs in t-<run-id>, which makes the label set new every time — and
#   the previous trial's namespace must be *gone*, not merely deleted,
#   before this one starts measuring (see wait_for_cold_node below).
#
# All three t_ready candidate definitions from docs/methodology.md are
# collected side by side, for the ADR 0003 comparison:
#   A — watcher.jsonl: the watcher's own watch-local timestamp
#   B — cri.jsonl:     the victim container's CRI-reported start time,
#                      read via `docker exec <node> crictl inspect` (kind
#                      nodes are plain Docker containers, so this needs no
#                      in-cluster privileged access)
#   C — victim.jsonl:  the victim's own self-report, emitted the instant
#                      its listener starts accepting connections
#
# Every component is anchored to the same -run-epoch (docs/methodology.md,
# Clocks) so each offset_ns column is comparable and
# window = t_blocked - t_ready is meaningful.
set -euo pipefail

: "${CNI:?CNI is required}"
: "${RUN_ID:?RUN_ID is required}"
: "${RUN_DIR:?RUN_DIR is required}"
: "${DURATION:?DURATION is required (seconds)}"

KUBECONFIG_PATH="${KUBECONFIG:-${HOME}/.kube/config}"
CLUSTER_NAME="npw-${CNI}"                 # must match Taskfile.yml's CLUSTER_NAME
NODE_NAME="${CLUSTER_NAME}-control-plane" # kind's single-node container name

# How far candidate B may sit from candidate C before the trial is flagged.
# ADR 0003 measured the two agreeing to within 4.4-5.4ms across its three
# pilot trials, and the largest gap seen since is 11.5ms
# (20260809T060534Z; see preregistration.md's Addendum 1 B2). 50ms is a
# coarse runtime canary well clear of both: anything past it means one of
# the two is not measuring what it is documented to measure and the trial
# needs a human look. The 5ms analysis-time criterion is a different
# number answering a different question -- see Addendum 1 B2.
B_C_SKEW_LIMIT_NS=50000000

# This script is the trusted boundary between the matrix runner and the
# cluster: everything below interpolates these two values into object
# names, into sed programs and into shell arithmetic, so they are checked
# as whole strings before anything is created. RUN_ID ends up inside two
# Kubernetes object names and so has to be a valid DNS-1123 label by
# itself; the 56-character bound is the 63-character label limit minus the
# longest prefix this script prepends ("prober-"). `[[ =~ ]]` rather than
# a pipe into grep, which is line-oriented and would accept a value whose
# *second* line matches while the first smuggles in a newline.
if [[ ! "${RUN_ID}" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
  echo "RUN_ID must be a DNS-1123 label (lowercase alphanumerics and '-', starting and ending alphanumeric); got: ${RUN_ID}" >&2
  exit 2
fi
if [ "${#RUN_ID}" -gt 56 ]; then
  echo "RUN_ID must be at most 56 characters (63-char DNS-1123 label limit minus the 'prober-' prefix); got ${#RUN_ID}: ${RUN_ID}" >&2
  exit 2
fi
case "${DURATION}" in
  '' | *[!0-9]*)
    echo "DURATION must be a positive integer number of seconds; got: ${DURATION}" >&2
    exit 2
    ;;
esac
if [ "${DURATION}" -lt 1 ]; then
  echo "DURATION must be a positive integer number of seconds; got: ${DURATION}" >&2
  exit 2
fi

NS="t-${RUN_ID}"
POD="prober-${RUN_ID}"

# checksums.sha256 is written last and atomically, so its presence is what
# marks a trial as complete.
if [ -e "${RUN_DIR}/checksums.sha256" ]; then
  echo "refusing to overwrite completed trial ${RUN_DIR}" >&2
  exit 2
fi
mkdir -p "${RUN_DIR}"

WATCHER_PID=""
BUILD_DIR=""
CLEANED=""
WARNINGS=()

# Teardown is part of the measurement, not an afterthought: a namespace
# that is still Terminating is still releasing CNI endpoints, identities
# and policy-map entries on this single node, inside the same CNI agent
# whose reaction latency is this project's dependent variable (that
# teardown-side effect is CVE-2024-7598's own subject — it must not be
# allowed to bleed into a startup-side measurement). So the delete waits,
# bounded, and the next trial re-checks with wait_for_cold_node before it
# measures anything. The prober Pod lives outside the trial namespace (it
# must not be subject to the policy under test) and holds its own CNI
# endpoint until it is deleted, so it is waited on too. Both waits are
# bounded and both failures are swallowed: cleanup must never change the
# trial's exit status, and a teardown this script could not finish is the
# next trial's guard's problem to report.
cleanup() {
  [ -n "${CLEANED}" ] && return 0
  CLEANED=1
  [ -n "${WATCHER_PID}" ] && kill "${WATCHER_PID}" 2>/dev/null || true
  [ -n "${BUILD_DIR}" ] && rm -rf "${BUILD_DIR}" || true
  kubectl delete pod "${POD}" -n prober --ignore-not-found --wait --timeout=60s >/dev/null 2>&1 || true
  kubectl delete namespace "${NS}" --ignore-not-found --wait --timeout=120s >/dev/null 2>&1 || true
}
# A bare `trap cleanup INT` would run cleanup and then *resume* the script,
# so the signal traps exit explicitly; the re-entry guard above keeps the
# EXIT trap that this exit fires from repeating the work.
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

# Built, not `go run`: `go run` compiles and links client-go in the
# foreground before the watcher process exists at all, which on a cold
# build cache takes tens of seconds. cmd/watcher records the first event
# it sees carrying PodReady=True, including the initial-sync ADDED event,
# so a watch that opens late stamps "when the watch opened" and still
# labels it candidate A — a wrong number indistinguishable from a right
# one, biased by however warm the Go build cache happened to be. Building
# here, before the run epoch is even taken, moves that cost out of the
# measured section entirely. It also makes the cleanup `kill` above hit
# the watcher itself rather than a `go run` wrapper that would leave the
# watcher orphaned, still watching and still writing.
BUILD_DIR="$(mktemp -d)"
WATCHER_BIN="${BUILD_DIR}/watcher"
echo "==> building watcher"
go build -o "${WATCHER_BIN}" ./cmd/watcher

# The cold-start guarantee, asserted rather than assumed. Trial N's
# cleanup already waited for its namespace to go away, so in a healthy
# back-to-back run this returns immediately; it exists for the cases where
# that wait timed out, where the previous trial was killed, or where
# something else left state on the node. Failing here is correct: a trial
# that runs against a node still tearing the last one down produces a
# number that silently measures both.
wait_for_cold_node() {
  local deadline=$(( $(date +%s) + 180 ))
  local terminating leftover
  while :; do
    terminating="$(kubectl get namespace \
      -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' \
      | awk '$1 ~ /^t-/ && $2 == "Terminating" { printf "%s ", $1 }')"
    leftover="$(kubectl get pods -n prober \
      -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}' 2>/dev/null || true)"
    if [ -z "${terminating}" ] && [ -z "${leftover}" ]; then
      return 0
    fi
    if [ "$(date +%s)" -ge "${deadline}" ]; then
      echo "refusing to start ${RUN_ID}: the node is not cold after 180s (terminating trial namespaces: ${terminating:-none}; leftover prober pods: ${leftover:-none})" >&2
      return 1
    fi
    sleep 1
  done
}
echo "==> waiting for a cold node"
wait_for_cold_node

# A single shared instant every binary anchors its offset_ns to. Go's
# monotonic clock has no meaning across process boundaries, so this
# wall-clock read is the one deliberate exception documented in
# docs/methodology.md's Clocks section. macOS's `date` has no nanosecond
# format specifier, so this uses Python (already a project dependency)
# rather than `date +%s%N`, which is a GNU-only extension.
RUN_EPOCH="$(python3 -c 'import time; print(time.time_ns())')"

render() {
  sed \
    -e "s/__NS__/${NS}/g" \
    -e "s/__RUN_ID__/${RUN_ID}/g" \
    -e "s/__RUN_EPOCH__/${RUN_EPOCH}/g" \
    -e "s/__DURATION__/${DURATION}s/g" \
    "$1"
}

# Diagnostic only: how many ms have elapsed since RUN_EPOCH right now.
# Useful for breaking down where this script's own overhead goes when a
# t_ready-to-first-observation gap needs explaining.
now_offset_ms() {
  python3 -c "import time; print((time.time_ns() - ${RUN_EPOCH}) / 1e6)"
}

warn() {
  echo "warning: $1" >&2
  WARNINGS+=("$1")
}

echo "==> creating trial namespace ${NS}"
kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Policy before workload — see the header. By the time the victim's Pod is
# created below, this policy has already been accepted by the apiserver
# and the CNI has had the whole prober startup and watcher warm-up to
# react to it.
echo "==> applying default-deny policy (offset $(now_offset_ms)ms)"
render deploy/policies/baseline/default-deny-ingress.yaml | kubectl apply -f - >/dev/null

echo "==> deploying in-cluster prober (offset $(now_offset_ms)ms)"
render deploy/workloads/prober.yaml | kubectl apply -f - >/dev/null

# Confirmed Running (not just Ready — this Pod has no readiness probe)
# means its container has started and, in practice, is already past the
# few milliseconds of Go runtime startup needed to call Watch() against
# the apiserver: the watch has to be live before the victim is created,
# the same requirement cmd/watcher has below.
kubectl wait --for=jsonpath='{.status.phase}'=Running "pod/${POD}" -n prober --timeout=60s >/dev/null
echo "==> prober Running and watching (offset $(now_offset_ms)ms)"

echo "==> starting watcher (namespace=${NS}, selector=role=victim) before the victim exists"
"${WATCHER_BIN}" \
  -kubeconfig "${KUBECONFIG_PATH}" \
  -namespace "${NS}" \
  -selector role=victim \
  -duration "${DURATION}s" \
  -run-epoch "${RUN_EPOCH}" \
  >"${RUN_DIR}/watcher.jsonl" 2>"${RUN_DIR}/watcher.log" &
WATCHER_PID=$!
sleep 1 # let the watch open before the victim exists

echo "==> deploying victim workload (offset $(now_offset_ms)ms)"
render deploy/workloads/victim.yaml | kubectl apply -f - >/dev/null

kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${POD}" -n prober --timeout="$((DURATION + 90))s" >/dev/null
kubectl logs "pod/${POD}" -n prober >"${RUN_DIR}/prober.jsonl"
if [ ! -s "${RUN_DIR}/prober.jsonl" ]; then
  echo "prober produced no observations; there is no t_blocked to compute" >&2
  exit 1
fi

# t_ready candidate B: the victim container's CRI-reported start time.
# The container id comes from the Pod's own status rather than from
# `crictl ps --name victim`, which is ambiguous the moment churn Pods or
# an earlier trial's containers share the node. `startedAt` is
# nanosecond-precision (RFC3339Nano) — unlike the apiserver's own Pod
# status timestamps, which are second-precision (docs/methodology.md), and
# which must never be used for this. Read only now, after the prober has
# finished: `startedAt` is a historical fact recorded by containerd the
# instant the container started, so reading it late does not change its
# value, and by this point there is no critical-path timing left to
# disturb.
#
# containerStatuses[0] is the *current* container. The victim runs under a
# Deployment (restartPolicy Always), so a crash or OOM mid-trial replaces
# it and `startedAt` would then be the restart time — a plainly wrong
# t_ready that nothing downstream could detect. A restart also means the
# victim stopped listening mid-run, which breaks the "victim always
# listens" invariant in docs/methodology.md, so this fails the trial
# rather than flagging it.
VICTIM_STATUS="$(kubectl get pod -n "${NS}" -l role=victim \
  -o jsonpath='{.items[0].status.containerStatuses[0].containerID} {.items[0].status.containerStatuses[0].restartCount}')"
CID="$(echo "${VICTIM_STATUS}" | awk '{print $1}' | sed 's#.*://##')"
RESTARTS="$(echo "${VICTIM_STATUS}" | awk '{print $2}')"
if [ -z "${CID}" ]; then
  echo "victim containerID not found in Pod status" >&2
  exit 1
fi
if [ "${RESTARTS:-0}" -ne 0 ]; then
  echo "victim container restarted ${RESTARTS} time(s) during the trial; candidate B would be the restart time and the listener invariant was broken" >&2
  exit 1
fi
STARTED_AT="$(docker exec "${NODE_NAME}" crictl inspect "${CID}" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"]["startedAt"])')"
B_OFFSET_NS="$(python3 scripts/rfc3339nano_to_offset_ns.py "${STARTED_AT}" "${RUN_EPOCH}")"
echo "{\"offset_ns\": ${B_OFFSET_NS}, \"t_ready_method\": \"B\"}" >"${RUN_DIR}/cri.jsonl"
echo "==> candidate B (CRI startedAt): offset_ns=${B_OFFSET_NS}"

# t_ready candidate C: the victim's own self-report (emitted once, the
# instant its listener started accepting connections). Safe to read any
# time after that point — it is a historical fact, not a live stream.
# `kubectl logs` exits 0 on an empty log, so the file is parsed rather
# than merely tested for existence: C is the primary t_ready (ADR 0003),
# and a trial without it has no primary measurement at all.
kubectl logs -n "${NS}" deploy/victim >"${RUN_DIR}/victim.jsonl"
C_OFFSET_NS="$(python3 -c '
import json, sys

records = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
c = [r for r in records if r.get("t_ready_method") == "C"]
if len(c) != 1:
    sys.exit(f"expected exactly one candidate-C record in victim.jsonl, found {len(c)}")
print(c[0]["offset_ns"])
' "${RUN_DIR}/victim.jsonl")"
echo "==> candidate C (victim self-report): offset_ns=${C_OFFSET_NS}"

SKEW_NS=$(( B_OFFSET_NS - C_OFFSET_NS ))
ABS_SKEW_NS="${SKEW_NS#-}"
if [ "${ABS_SKEW_NS}" -gt "${B_C_SKEW_LIMIT_NS}" ]; then
  warn "candidate B and C disagree by ${ABS_SKEW_NS}ns, past the ${B_C_SKEW_LIMIT_NS}ns limit (ADR 0003 measured ~5ms)"
fi

# Candidate A is a diagnostic signal, not the basis of a reported window
# (ADR 0003), so a watcher that failed does not fail the trial — but it
# must not pass silently either, or a missing candidate A looks the same
# as a candidate A that was never expected.
if wait "${WATCHER_PID}"; then
  WATCHER_STATUS=0
else
  WATCHER_STATUS=$?
fi
WATCHER_PID=""
if [ "${WATCHER_STATUS}" -ne 0 ]; then
  warn "watcher exited ${WATCHER_STATUS}; see watcher.log"
fi
if [ ! -s "${RUN_DIR}/watcher.jsonl" ]; then
  warn "watcher recorded no Pod-Ready event; candidate A is missing for this trial"
fi

# trial.json is the per-trial provenance record: the shared epoch every
# offset_ns hangs off, where the trial ran, and the cross-checks that a
# reader would otherwise have to recompute to know whether to trust the
# numbers beside it.
python3 - \
  "${RUN_EPOCH}" "${NS}" "${NODE_NAME}" "${RESTARTS}" \
  "${B_OFFSET_NS}" "${C_OFFSET_NS}" "${SKEW_NS}" \
  ${WARNINGS[@]+"${WARNINGS[@]}"} >"${RUN_DIR}/trial.json" <<'PY'
import json
import sys

epoch, ns, node, restarts, b, c, skew = sys.argv[1:8]
print(
    json.dumps(
        {
            "run_epoch_ns": int(epoch),
            "namespace": ns,
            "node": node,
            "victim_restart_count": int(restarts),
            "t_ready_b_ns": int(b),
            "t_ready_c_ns": int(c),
            "b_c_skew_ns": int(skew),
            "warnings": sys.argv[8:],
        }
    )
)
PY

# The integrity record for an immutable data/raw/ tree. It covers every
# data file in the directory: the JSONL streams, trial.json, and
# meta.json — the runner's record of the conditions the trial ran under,
# the one thing that cannot be reconstructed from the measurements
# themselves. A bare scripts/collect.sh pass has no runner and therefore
# no meta.json; its absence is normal, not a failure. watcher.log is the
# deliberate exception: it is the watcher's stderr, a debugging aid rather
# than data, and it stays out of the record.
#
# Written to a temp file and moved into place because its presence is the
# marker that says this trial completed. A half-written checksums.sha256
# would both block a legitimate retry and verify happily against whatever
# subset of lines reached disk.
echo "==> writing checksums"
(
  cd "${RUN_DIR}"
  files=(./*.jsonl ./trial.json)
  if [ -e ./meta.json ]; then
    files+=(./meta.json)
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${files[@]}" >.checksums.sha256.tmp
  else
    # macOS has no sha256sum; shasum -a 256 produces the same format.
    shasum -a 256 "${files[@]}" >.checksums.sha256.tmp
  fi
  mv .checksums.sha256.tmp checksums.sha256
)

echo "==> trial complete: ${RUN_DIR}"
