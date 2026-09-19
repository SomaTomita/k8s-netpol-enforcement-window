# Related Work

This document is the canonical citation record for this project, alongside
`docs/methodology.md`. Quotation marks are used only around text that has
been checked directly against its source. Everything else in this document
is described in this project's own words, cited by title, venue, and year —
never presented as a verbatim quote unless it is one.

## The gap this project addresses

Three independent sources establish that this window exists, disagree on
its size by three orders of magnitude, and confirm nobody has measured it
directly under controlled, comparable conditions.

### 1. Kubernetes official documentation

Kubernetes' own documentation for `NetworkPolicy` states, verbatim:

> "If a pod that is affected by a NetworkPolicy is created before the
> network plugin has completed NetworkPolicy handling, that pod may be
> started unprotected, and isolation rules will be applied when the
> NetworkPolicy handling is completed."

This is an acknowledgment that the window exists by design. It states
neither how large the window is nor how it varies across CNI
implementations — that gap is what this project measures.

### 2. CVE-2024-7598 (teardown-side analog)

CVE-2024-7598, "Network restriction bypass via race condition during
namespace termination" (Kubernetes Security Response Committee advisory,
disclosed 2024-08-07; CVSS 3.1 base score 3.1, Low), is the teardown-side
counterpart of the defect this project measures. Its root cause: the
namespace controller does not define an order for deleting the resources
inside a namespace being torn down. If `NetworkPolicy` objects are deleted
before the `Pod` objects they were protecting, there is a window during
namespace termination where previously policy-protected pods are running
with no policy enforcing traffic restrictions against them. Reference:
kubernetes/kubernetes#126587 and the kubernetes-security-announce
advisory.

This matters for the present work for one specific reason: it establishes
that the Kubernetes Security Response Committee has treated *this class
of defect* — a race between Pod lifecycle and NetworkPolicy enforcement
lifecycle — as a genuine security issue worth a CVE, on the teardown side.
The startup-side window this project measures (`window = t_blocked -
t_ready`) is structurally the same class of race, but as of this writing
has no assigned CVE and no published measurement. The CVE is evidence that
the *pattern* is a recognized security concern, not evidence about the
size of the startup-side window — the CVE says nothing about magnitude,
and its scope (teardown) is a different code path from the one this
project measures (startup).

### 3. Budigiri et al., IEEE TNSM 22(2), 2025

Budigiri, Baumann, Truyen, and Joosen, "Elastic Cross-Layer Orchestration
of Network Policies in the Kubernetes Stack," *IEEE Transactions on
Network and Service Management*, 22(2), 2025, is the direct predecessor of
this project. Its own Limitations section states, verbatim:

> "the minimal performance impact of GH depends on the fact that SGs can
> be configured before a Pod becomes ready. Currently the tool has a lot
> of headroom in that respect, as we observed Pod readiness times of
> 3-4s ... or the readiness time of Pods shrinks dramatically in the
> future, a mechanism may be needed that halts Pods from starting before
> SG reconfiguration is complete"

Read carefully, this is the authors assuming a readiness budget (3-4
seconds) sufficient to configure security groups (SGs) *before* the Pod
becomes ready, based on Pod readiness times they observed — not a
measurement of the window between Pod-Ready and policy enforcement
itself. Their own text flags this as provisional ("currently... a lot of
headroom") and conditional on future readiness times not shrinking. This
project is the direct empirical follow-up to that limitation: it measures
the window Budigiri et al. assumed rather than measured.

## Existing numbers disagree by three orders of magnitude

| Source | Value | What it actually measured |
|---|---|---|
| Cilium official scalability report | < 600 ms | Time from policy *add* to enforcement convergence — not from Pod-Ready to enforcement. A different starting point than `t_ready`. |
| Budigiri et al., TNSM 2025 | Pod readiness 3-4 s | Cited as the *budget* available for SG configuration before Pod readiness; not a measurement of the enforcement window itself. |
| k3s issue #947 | < 1 minute | A single anecdotal user report; no methodology, no repetition, no quantitative rigor. |

These figures are not directly comparable to each other or to this
project's target quantity — each measures a different thing, under
different (or undocumented) conditions. That disagreement, and the
absence of a study measuring `t_blocked - t_ready` directly, is the
motivation for this harness.

## Broader literature

The following are described in this project's own words. No verbatim
quotations are attributed to them beyond title, venue, and year — no
verbatim text from these sources has been checked against source for this
repository.

- Budigiri, Baumann, Mühlberg, Truyen, Joosen, "Network Policies in
  Kubernetes: Performance Evaluation and Security Analysis," EuCNC 2021 —
  the earliest paper in this research lineage; establishes the
  performance/security evaluation approach this project's direct
  predecessor (TNSM 2025) builds on.
- Budigiri et al., "Zero-Cost In-Depth Enforcement of Network
  Policies...," IEEE CLOUD 2023 — an intermediate paper in the same
  lineage, between the 2021 EuCNC paper and the 2025 TNSM paper.
- Qi, Kulkarni, Ramakrishnan, "Assessing Container Network Interface
  Plugins," IEEE TNSM 18(1), 2021 — the primary source for the
  approximately-4-second Pod readiness figure that later work (including
  Budigiri et al.) draws on. Evaluates CNI plugin performance broadly, not
  the enforcement window specifically.
- Kim, Kim, Lee, "Exploring Security Enhancements in Kubernetes CNI,"
  IEEE Access 13, 2025 — a cross-CNI comparison, but on a performance
  axis. Does not address whether enforcement is *correct* or how long it
  takes relative to Pod readiness, which is the axis this project
  occupies.
- Bufalino et al., "Inside Job: Defending Kubernetes Clusters Against
  Network Misconfigurations," Proc. ACM Netw. (CoNEXT) 2025 — addresses
  NetworkPolicy *misconfiguration* detection. The authors explicitly scope
  CNI implementation differences and enforcement timing as out of scope
  for that work, which is precisely the space this project occupies
  instead.
- Bufalino et al., "Analyzing Microservice Connectivity with Kubesonde,"
  ESEC/FSE 2023 — a methodological precedent for active probing to
  observe network behavior from inside a cluster; informs this project's
  prober design, not its research question.
- Cyclonus (open source project), and the Kubernetes blog post "Defining
  Network Policy Conformance for CNI providers" (2021-04) — prior work on
  NetworkPolicy conformance across CNI providers (a reachability-matrix
  question: does a given rule behave the same across CNIs). Relevant as a
  methodological precedent, but non-peer-reviewed and, as of this
  project's writing, several years old. Does not address enforcement
  *timing*, which is this project's question.
- Li, Hu, Jia, Wang, Li, "Kano: Efficient Cloud Native Network Policy
  Verification," IEEE TNSM 20(3), 2023, pp. 3747-3764 — a different layer
  again: *static* verification that a declared NetworkPolicy set matches
  its intended reachability semantics within a single CNI. Neither a
  runtime measurement nor a cross-CNI comparison; noted here so it is not
  mistaken for either.
- Kermabon-Bobinnec et al., "ProSPEC," CODASPY 2022 — operates at the
  admission-control layer (OPA/Gatekeeper), i.e. policy validation before
  a resource is admitted to the cluster. This is a categorically
  different layer from the data-plane enforcement timing this project
  measures; noted here explicitly so the two are not conflated.

## Citation audit log

- **2026-08-09**: All three primary verbatim quotes re-checked directly
  against source. Kubernetes NetworkPolicy docs ("Pod lifecycle" section):
  exact match. Budigiri et al., TNSM 2025 (Limitations section, via the
  open-access copy at an institutional repository): exact match,
  including the ellipsis correctly marking the omitted middle clause.
  CVE-2024-7598: title, CVSS 3.1/Low score, and the 2024-08-07 disclosure
  date confirmed directly against `kubernetes/kubernetes#126587`'s
  creation timestamp (the later 2025-03-20 NVD/GHSA publish date some
  aggregators show is a database-indexing lag, not the original
  disclosure — not what this document cites). The Cilium scalability
  report's `<600ms` figure and k3s issue #947's characterization
  (single anecdotal report, race condition, `<1 minute`) were also
  independently reconfirmed.

## Summary

No existing source measures `t_blocked - t_ready` directly, under
controlled and comparable conditions, across CNI implementations. The
three primary sources above establish that the window is real (Kubernetes
docs), that this class of race condition is a recognized security concern
in an adjacent code path (CVE-2024-7598), and that the direct predecessor
to this work assumed a budget for it without measuring it (Budigiri et
al., 2025). The broader literature either measures a different quantity
(Qi et al.; the Cilium scalability report), a different axis (Kim et al.
— performance, not correctness/timing), a different layer (ProSPEC —
admission control, not data plane), or explicitly excludes this question
from its own scope (Bufalino et al., 2025).
