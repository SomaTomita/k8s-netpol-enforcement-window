"""
Experiment 1 (primary): measuring the "unprotected time window" at Pod startup.

Quantity measured: t_blocked - t_ready
  t_ready   = the time the Pod became Ready (API watch event)
  t_blocked = the time inbound traffic that should be blocked actually started
              being blocked (prober observation)
  A positive gap means the Pod is running but NetworkPolicy is not yet
  enforced -- an unprotected window.

Basis for the gap (threefold):
  1. Kubernetes docs, verbatim: "that pod may be started unprotected"
  2. CVE-2024-7598 (the teardown-side analogue of the same class of flaw,
     assigned an official CVE)
  3. Budigiri et al., TNSM 2025 -- a limitation the authors themselves note:
     they assume the window exists but never measure it

Regenerate with: python3 exp1_unprotected_window.py
Requires: pip install diagrams, and Graphviz (brew install graphviz).
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _style import (
    CLUSTER_ANALYSIS,
    CLUSTER_MEASURE,
    CLUSTER_TARGET,
    E_CONTROL,
    E_DATA,
    E_MEASURE,
    EDGE_ATTR,
    GRAPH_ATTR,
    NODE_ATTR,
)
from diagrams import Cluster, Diagram, Edge
from diagrams.k8s.clusterconfig import HorizontalPodAutoscaler
from diagrams.k8s.compute import DaemonSet, Deployment, Pod
from diagrams.k8s.controlplane import APIServer, Scheduler
from diagrams.k8s.network import NetworkPolicy
from diagrams.onprem.ci import GithubActions
from diagrams.programming.flowchart import Database, Decision
from diagrams.programming.language import Go, Python

with Diagram(
    "Experiment 1: measuring the unprotected window at Pod startup (t_blocked - t_ready)",
    filename="out/exp1_unprotected_window",
    outformat="pdf",
    show=False,
    direction="LR",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    harness = Python("test harness\n(orchestrator)")
    watcher = Go("cmd/watcher\ntimestamps on local receipt\n(not the apiserver's own clock)")

    with Cluster("load generation: Pod churn", graph_attr=CLUSTER_MEASURE):
        churn = Deployment("victim Deployment\nscale replicas up/down")
        hpa = HorizontalPodAutoscaler("HPA / direct scaling\nsteps the churn rate")
        churn - Edge(**E_CONTROL) - hpa

    with Cluster("cluster under test (kind, CNI swapped)", graph_attr=CLUSTER_TARGET):
        api = APIServer("kube-apiserver\nwatch: Pod Ready")
        sched = Scheduler("scheduler")
        cni = DaemonSet("CNI agent\nCilium / Calico / Antrea")
        netpol = NetworkPolicy("NetworkPolicy\ndefault-deny ingress")

        with Cluster("ns: target", graph_attr={"bgcolor": "#ffffff"}):
            victim = Pod("victim Pod\n(target of blocking)")

        with Cluster("ns: prober", graph_attr={"bgcolor": "#ffffff"}):
            prober = Pod("prober Pod\nconnection attempt every 1ms")

        api >> Edge(**E_CONTROL) >> sched >> Edge(**E_CONTROL) >> victim
        api >> Edge(**E_CONTROL) >> cni
        netpol >> Edge(label="applies to", **E_CONTROL) >> cni
        cni >> Edge(label="dataplane update", **E_CONTROL) >> victim

    with Cluster("measurement points", graph_attr=CLUSTER_MEASURE):
        t_ready = Decision("t_ready\nPod Ready event")
        t_blocked = Decision("t_blocked\nfirst observed block")

    with Cluster("collection & analysis", graph_attr=CLUSTER_ANALYSIS):
        jsonl = Database("JSONL output\n(internal/record.WriteJSONL)")
        analysis = Python("analysis\nsrc/npw/analysis\n(window.py, stats.py)")
        ci = GithubActions("CI\n(.github/workflows/ci.yml, smoke.yml)")

    # Control flow
    harness >> Edge(label="scale command", **E_CONTROL) >> churn
    churn >> Edge(label="Pod created", **E_CONTROL) >> api

    # Measurement flow (primary measured quantities highlighted in red)
    api >> Edge(label="Pod Ready event (watch)", **E_CONTROL) >> watcher
    watcher >> Edge(label="local receipt timestamp", **E_MEASURE) >> t_ready
    (
        prober
        >> Edge(label="continuous attempts at traffic\nthat should be blocked", **E_MEASURE)
        >> victim
    )
    prober >> Edge(label="first deny timestamp", **E_MEASURE) >> t_blocked

    t_ready >> Edge(**E_DATA) >> jsonl
    t_blocked >> Edge(**E_DATA) >> jsonl
    jsonl >> Edge(label="window = t_blocked - t_ready", **E_MEASURE) >> analysis
    analysis >> Edge(**E_DATA) >> ci
