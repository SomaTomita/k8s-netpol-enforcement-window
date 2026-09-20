#!/usr/bin/env bash
# Instrument check, NOT part of Experiment 1: applies default-deny-ingress
# at a known delay AFTER the victim becomes ready, instead of before it
# (see scripts/trial.sh for the policy-first ordering that IS the
# experiment). This deliberately produces the late-policy / teardown-side
# case (CVE-2024-7598's shape), which should yield an uncensored trial
# whose window is close to the imposed delay.
#
# Why this exists: every trial recorded to date, across all three CNIs and
# all three churn levels (Addendum 1 D1, Addendum 2), has been left-
# censored -- Blocked from its first observation. That is consistent both
# with "no window exists" and with "the harness cannot see a window that
# does exist". This script tells those two apart: if it recovers a window
# close to DELAY_MS, the detection path works and Experiment 1's negative
# results mean what they claim to. If it does not, the harness has a real
# defect that must be fixed before Task 12's full run.
#
# Its data is NOT pooled with Experiment 1's, is NOT added to the matrix,
# and is written to a separate root so it can never be picked up by
# npw.analysis.report_cli's data/raw/exp1 scan.
#
# Required env: CNI, RUN_ID, RUN_DIR, DURATION (seconds), DELAY_MS.
#
# Since Experiment 1b, `POLICY_AT=at-ready POLICY_DELAY_MS=<ms> scripts/trial.sh`
# performs the same measurement and also records the apply timestamps.
# This script is kept unchanged as the exact instrument the Addendum 3
# positive-control data was collected with.
set -euo pipefail

: "${CNI:?CNI is required}"
: "${RUN_ID:?RUN_ID is required}"
: "${RUN_DIR:?RUN_DIR is required}"
: "${DURATION:?DURATION is required (seconds)}"
: "${DELAY_MS:?DELAY_MS is required (milliseconds after t_ready before the policy is applied)}"

KUBECONFIG_PATH="${KUBECONFIG:-${HOME}/.kube/config}"
CLUSTER_NAME="npw-${CNI}"
NODE_NAME="${CLUSTER_NAME}-control-plane"

if [[ ! "${RUN_ID}" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
  echo "RUN_ID must be a DNS-1123 label; got: ${RUN_ID}" >&2
  exit 2
fi
if [ "${#RUN_ID}" -gt 56 ]; then
  echo "RUN_ID must be at most 56 characters; got ${#RUN_ID}: ${RUN_ID}" >&2
  exit 2
fi

NS="t-${RUN_ID}"
POD="prober-${RUN_ID}"

if [ -e "${RUN_DIR}/checksums.sha256" ]; then
  echo "refusing to overwrite completed run ${RUN_DIR}" >&2
  exit 2
fi
mkdir -p "${RUN_DIR}"

WATCHER_PID=""
BUILD_DIR=""
CLEANED=""

cleanup() {
  [ -n "${CLEANED}" ] && return 0
  CLEANED=1
  [ -n "${WATCHER_PID}" ] && kill "${WATCHER_PID}" 2>/dev/null || true
  [ -n "${BUILD_DIR}" ] && rm -rf "${BUILD_DIR}" || true
  kubectl delete pod "${POD}" -n prober --ignore-not-found --wait --timeout=60s >/dev/null 2>&1 || true
  kubectl delete namespace "${NS}" --ignore-not-found --wait --timeout=120s >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

BUILD_DIR="$(mktemp -d)"
WATCHER_BIN="${BUILD_DIR}/watcher"
echo "==> building watcher"
go build -o "${WATCHER_BIN}" ./cmd/watcher

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
      echo "refusing to start ${RUN_ID}: node not cold after 180s" >&2
      return 1
    fi
    sleep 1
  done
}
echo "==> waiting for a cold node"
wait_for_cold_node

RUN_EPOCH="$(python3 -c 'import time; print(time.time_ns())')"

render() {
  sed \
    -e "s/__NS__/${NS}/g" \
    -e "s/__RUN_ID__/${RUN_ID}/g" \
    -e "s/__RUN_EPOCH__/${RUN_EPOCH}/g" \
    -e "s/__DURATION__/${DURATION}s/g" \
    "$1"
}

now_offset_ms() {
  python3 -c "import time; print((time.time_ns() - ${RUN_EPOCH}) / 1e6)"
}

echo "==> creating trial namespace ${NS}"
kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# No policy yet -- this is the inversion. The prober and watcher start
# exactly as they do for a real trial, but the victim comes up with
# nothing blocking it.
echo "==> deploying in-cluster prober (offset $(now_offset_ms)ms)"
render deploy/workloads/prober.yaml | kubectl apply -f - >/dev/null
kubectl wait --for=jsonpath='{.status.phase}'=Running "pod/${POD}" -n prober --timeout=60s >/dev/null
echo "==> prober Running and watching (offset $(now_offset_ms)ms)"

echo "==> starting watcher before the victim exists"
"${WATCHER_BIN}" \
  -kubeconfig "${KUBECONFIG_PATH}" \
  -namespace "${NS}" \
  -selector role=victim \
  -duration "${DURATION}s" \
  -run-epoch "${RUN_EPOCH}" \
  >"${RUN_DIR}/watcher.jsonl" 2>"${RUN_DIR}/watcher.log" &
WATCHER_PID=$!
sleep 1

echo "==> deploying victim workload, unblocked (offset $(now_offset_ms)ms)"
render deploy/workloads/victim.yaml | kubectl apply -f - >/dev/null

# Poll for candidate C (the victim's own self-report) rather than sleeping
# a fixed amount from kubectl apply returning: Pod scheduling and sandbox
# creation cost a variable ~1-2s (see the pilot logs), and DELAY_MS must
# be measured from actual t_ready, not from when the apply command
# returned. This is the one place this script's timing differs in kind
# from trial.sh: there, the policy already exists before t_ready and no
# such poll is needed.
echo "==> polling for candidate C (victim self-report)"
C_OFFSET_NS=""
POLL_DEADLINE=$(( $(date +%s) + 60 ))
while [ -z "${C_OFFSET_NS}" ]; do
  if [ "$(date +%s)" -ge "${POLL_DEADLINE}" ]; then
    echo "victim did not report t_ready within 60s" >&2
    exit 1
  fi
  RAW="$(kubectl logs -n "${NS}" deploy/victim 2>/dev/null || true)"
  if [ -n "${RAW}" ]; then
    C_OFFSET_NS="$(python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    if r.get("t_ready_method") == "C":
        print(r["offset_ns"])
        break
' <<<"${RAW}")"
  fi
  [ -z "${C_OFFSET_NS}" ] && sleep 0.1
done
echo "==> candidate C observed: offset_ns=${C_OFFSET_NS} (t=$(now_offset_ms)ms)"

# The deliberate delay: this is the instrument's known input. Applying
# the policy here, after the victim already accepts connections, is the
# late-policy / teardown-side case -- the opposite of what Experiment 1
# measures, and exactly what this script exists to check.
echo "==> sleeping ${DELAY_MS}ms before applying the policy"
python3 -c "import time; time.sleep(${DELAY_MS} / 1000)"

APPLY_OFFSET_MS="$(now_offset_ms)"
echo "==> applying default-deny policy (offset ${APPLY_OFFSET_MS}ms)"
render deploy/policies/baseline/default-deny-ingress.yaml | kubectl apply -f - >/dev/null

kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${POD}" -n prober --timeout="$((DURATION + 90))s" >/dev/null
kubectl logs "pod/${POD}" -n prober >"${RUN_DIR}/prober.jsonl"
if [ ! -s "${RUN_DIR}/prober.jsonl" ]; then
  echo "prober produced no observations" >&2
  exit 1
fi

echo "{\"offset_ns\": ${C_OFFSET_NS}, \"t_ready_method\": \"C\"}" >"${RUN_DIR}/victim.jsonl"

wait "${WATCHER_PID}" || true
WATCHER_PID=""

python3 - \
  "${RUN_EPOCH}" "${NS}" "${NODE_NAME}" "${DELAY_MS}" "${C_OFFSET_NS}" \
  >"${RUN_DIR}/trial.json" <<'PY'
import json
import sys

epoch, ns, node, delay_ms, c = sys.argv[1:6]
print(
    json.dumps(
        {
            "run_epoch_ns": int(epoch),
            "namespace": ns,
            "node": node,
            "instrument_check": "positive-control",
            "imposed_delay_ms": float(delay_ms),
            "t_ready_c_ns": int(c),
        }
    )
)
PY

echo "==> writing checksums"
(
  cd "${RUN_DIR}"
  files=(./*.jsonl ./trial.json)
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${files[@]}" >.checksums.sha256.tmp
  else
    shasum -a 256 "${files[@]}" >.checksums.sha256.tmp
  fi
  mv .checksums.sha256.tmp checksums.sha256
)

echo "==> positive control complete: ${RUN_DIR} (imposed delay ${DELAY_MS}ms)"
