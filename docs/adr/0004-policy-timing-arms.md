# ADR 0004: Vary when the policy is applied, not the cluster's size

## Status

Accepted

## Context

Experiment 1 found the window left-censored on every trial with the
policy created ~2.4 s before readiness. The Kubernetes documentation's
hazard needs the network plugin's policy handling to still be incomplete
when the Pod starts. There are two ways to reach that regime:

1. Lengthen the plugin's handling time — this project's own hypothesis is
   that more nodes, more policies, and more endpoints would do this in a
   real cluster (not itself established by the citation below). Real
   clusters can see dataplane programming lag by minutes:
   projectcalico/calico #9706 is a single, unresolved user report (closed
   for inactivity; `kind/support`; Calico 3.27.4) of pods starting with no
   *outbound* connectivity for up to two minutes — with no NetworkPolicies
   applied at all, the opposite polarity from this project's unprotected
   window — traced to calico-node missing a `WorkloadEndpointUpdate`. The
   reporter correlates the episodes with pod-creation bursts and
   control-plane memory pressure, not node count; a Calico maintainer
   replies in-thread that "50 nodes is a small cluster." The issue
   supports "dataplane programming can lag by minutes in real clusters,"
   not a claim that policy handling scales with node count. Reaching a
   longer `L` this way needs a multi-node cluster and therefore cross-host
   clock synchronisation, which ADR 0002 rules out for this harness.
2. Shorten the head start the plugin gets — apply the policy later,
   relative to the Pod, under the harness's control.

## Decision

Option 2, as a `policy_at` factor with two arms beyond Experiment 1's
`before`: `with-victim` (the realistic same-instant race) and `at-ready`
(policy after readiness, isolating enforcement latency `L`). The apply
instant is recorded per trial so the ordering is measured, not assumed.

## Consequences

- Single-node stays valid: every timestamp is still one host's clock.
- `L` becomes directly measurable with n = 30 per CNI instead of the
  positive control's n = 1.
- If *every* `with-victim` trial is left-censored, the result is that
  the hazard was not reachable at this scale with this ordering — a
  bound on the regime, not a null, and one that bounds each trial's
  window from above rather than showing it is zero. Observing the hazard
  itself is then explicitly deferred to a multi-node follow-up that must
  first revisit ADR 0002. Pre-registered H2 is the weaker and separate
  prediction that ≥ 20 of 30 trials per CNI censor: H2 can hold with
  uncensored trials in it, and each of those is a direct observation of
  the hazard — the more significant outcome of the two, and the
  measurement Experiment 1 could not make. See the Experiment 1b
  pre-registration, H2, for what each outcome licenses.
- `scripts/positive-control.sh` becomes a special case of
  `POLICY_AT=at-ready POLICY_DELAY_MS=2000`; it is kept unchanged as the
  instrument that produced the Addendum 3 data.
