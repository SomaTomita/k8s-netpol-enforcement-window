#!/usr/bin/env bash
# Thin wrapper around scripts/trial.sh for local end-to-end runs: builds
# the prober and victim images, brings up the kind cluster for $CNI, loads
# both images into it, runs exactly one trial, and tears the cluster down
# again. Everything that decides what the numbers mean — policy-first
# ordering, the per-trial namespace, the shared -run-epoch, the three
# t_ready candidates — lives in scripts/trial.sh; this script only
# supplies the cluster that trial needs and cleans it up afterwards.
#
# This is a developer convenience for exercising the harness end-to-end
# locally, and writes under data/raw/dev/ to keep its output out of the
# experiment-matrix tree. It is not the rigorous matrix runner (that's
# src/npw/runner.py), which drives trial.sh directly against a cluster it
# keeps up across many trials.
#
# Usage: [CNI=cilium|calico|antrea] scripts/collect.sh [duration-in-seconds]
set -euo pipefail

DURATION="${1:-60}"
CNI="${CNI:-cilium}"
CLUSTER_NAME="npw-${CNI}" # must match Taskfile.yml's CLUSTER_NAME
PROBER_IMAGE="netpol-window/prober:dev"
VICTIM_IMAGE="netpol-window/victim:dev"

# Lowercase, hyphen-separated timestamp rather than RFC3339's own
# spelling: trial.sh derives a namespace and a Pod name from RUN_ID, and
# the uppercase `T`/`Z` of `20260919T053000Z` are not legal in a DNS-1123
# label, so the apiserver would reject every object this run creates.
RUN_ID="dev-$(date -u +%Y%m%d-%H%M%S)"
RUN_DIR="data/raw/dev/${RUN_ID}"

cleanup() {
  task cluster:down CNI="${CNI}" || true
}
trap cleanup EXIT

echo "==> building prober and victim images"
docker build -q -t "${PROBER_IMAGE}" -f cmd/prober/Dockerfile .
docker build -q -t "${VICTIM_IMAGE}" -f cmd/victim/Dockerfile .

echo "==> bringing up cluster (CNI=${CNI})"
task cluster:up CNI="${CNI}"

echo "==> loading prober and victim images into kind"
kind load docker-image "${PROBER_IMAGE}" --name "${CLUSTER_NAME}"
kind load docker-image "${VICTIM_IMAGE}" --name "${CLUSTER_NAME}"

CNI="${CNI}" RUN_ID="${RUN_ID}" RUN_DIR="${RUN_DIR}" DURATION="${DURATION}" scripts/trial.sh

echo "==> run complete: ${RUN_DIR}"
ls -la "${RUN_DIR}"
