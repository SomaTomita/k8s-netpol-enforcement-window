# ADR 0004: Vary when the policy is applied, not the cluster's size

## Status

Accepted

## Context

Experiment 1 found the window left-censored on every trial with the
policy created ~2.4 s before readiness. The Kubernetes documentation's
hazard needs the network plugin's policy handling to still be incomplete
when the Pod starts. There are two ways to reach that regime:

1. Lengthen the plugin's handling time — more nodes, more policies, more
   endpoints. This is what real clusters do (projectcalico/calico #9706
   reports startup connectivity gaps of up to two minutes at 50+ nodes),
   but it needs a multi-node cluster and therefore cross-host clock
   synchronisation, which ADR 0002 rules out for this harness.
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
- If `with-victim` is left-censored (pre-registered H2), the result is
  that the hazard is unreachable at this scale — a bound on the regime,
  not a null. Observing the hazard itself is then explicitly deferred to
  a multi-node follow-up that must first revisit ADR 0002.
- `scripts/positive-control.sh` becomes a special case of
  `POLICY_AT=at-ready POLICY_DELAY_MS=2000`; it is kept unchanged as the
  instrument that produced the Addendum 3 data.
