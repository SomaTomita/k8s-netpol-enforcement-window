"""
Experiment 2 (secondary): semantic consistency across CNI implementations
(differential testing).

Quantity measured: apply the same NetworkPolicy set P to N CNI implementations,
collect a reachability matrix M_cni for every Pod pair, then count mismatched
cells across implementations and classify the root causes.

Positioning:
  - The *existence* of inconsistency has already been demonstrated by
    Cyclonus and the Kubernetes official blog (2021-04). However, that work
    is non-peer-reviewed and the data is five years old (Calico 3.18 /
    Cilium 1.9.5 / Antrea 0.13.1).
  - Cross-CNI comparisons on the performance axis are already peer-reviewed
    (Kim et al., IEEE Access 2025).
  - So this is not a "new discovery" but a tracked reproduction of a known
    deviation plus re-measurement on current versions, consistent with the
    "novelty not required" institutional constraint. It is collected as a
    by-product of Experiment 1 on the same infrastructure.
  - AdminNetworkPolicy (ANP/BANP) has zero academic papers and only one
    official conformance report -- the best-value third axis to add.

Regenerate with: python3 exp2_cni_consistency.py
Requires: pip install diagrams, and Graphviz (brew install graphviz).
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _style import (
    CLUSTER_ANALYSIS,
    CLUSTER_EXTERNAL,
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
from diagrams.k8s.compute import DaemonSet, Pod
from diagrams.onprem.analytics import Spark
from diagrams.programming.flowchart import Database, Decision, InputOutput
from diagrams.programming.language import Python

with Diagram(
    "Experiment 2: semantic consistency across CNI implementations (differential testing)",
    filename="out/exp2_cni_consistency",
    outformat="pdf",
    show=False,
    direction="LR",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    with Cluster("input: policy corpus", graph_attr=CLUSTER_EXTERNAL):
        corpus = Database("NetworkPolicy corpus\nCyclonus-generated + hand-written\n+ ANP / BANP")
        known = InputOutput("2021 known-deviation list\n(target for tracked reproduction)")

    generator = Python("truth-table generator\ncomputes expected reachability matrix")

    with Cluster("under test: same policy set, different CNIs", graph_attr=CLUSTER_TARGET):
        with Cluster("kind cluster A", graph_attr={"bgcolor": "#ffffff"}):
            cni_a = DaemonSet("Cilium (eBPF)")
            pods_a = Pod("probe pods")
            cni_a - Edge(**E_CONTROL) - pods_a

        with Cluster("kind cluster B", graph_attr={"bgcolor": "#ffffff"}):
            cni_b = DaemonSet("Calico")
            pods_b = Pod("probe pods")
            cni_b - Edge(**E_CONTROL) - pods_b

        with Cluster("kind cluster C", graph_attr={"bgcolor": "#ffffff"}):
            cni_c = DaemonSet("Antrea")
            pods_c = Pod("probe pods")
            cni_c - Edge(**E_CONTROL) - pods_c

    with Cluster("observed: actual reachability matrices", graph_attr=CLUSTER_MEASURE):
        m_a = Database("M_Cilium")
        m_b = Database("M_Calico")
        m_c = Database("M_Antrea")

    with Cluster("differential analysis", graph_attr=CLUSTER_ANALYSIS):
        diff = Decision("extract mismatched cells\nexpected vs observed / across implementations")
        rootcause = Spark(
            "root-cause classification\nprotocol defaults / port semantics\nipBlock / named ports\nSCTP unsupported, etc."
        )
        report = InputOutput("deviation catalog\n+ diff vs 2021")

    corpus >> Edge(**E_DATA) >> generator
    known >> Edge(label="specifies tracked targets", **E_CONTROL) >> generator

    for c in (cni_a, cni_b, cni_c):
        generator >> Edge(label="apply identical policy", **E_CONTROL) >> c

    pods_a >> Edge(label="actively probe all Pod pairs", **E_MEASURE) >> m_a
    pods_b >> Edge(**E_MEASURE) >> m_b
    pods_c >> Edge(**E_MEASURE) >> m_c

    for m in (m_a, m_b, m_c):
        m >> Edge(**E_DATA) >> diff

    diff >> Edge(**E_DATA) >> rootcause >> Edge(**E_DATA) >> report
