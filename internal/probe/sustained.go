package probe

import "time"

// Observation is a single timestamped probe result, expressed as an
// elapsed offset from the start of a run rather than a wall-clock time
// (see the "Clocks" section of docs/methodology.md for why this project
// uses monotonic offsets throughout).
type Observation struct {
	// Offset is the time elapsed since the run started when this
	// observation was recorded.
	Offset time.Duration

	// Outcome is the classified result of the probe attempt (see
	// Classify in outcome.go).
	Outcome Outcome
}

// FirstSustained scans obs in order and returns the offset of the first
// observation in the earliest unbroken run of k consecutive observations
// classified as want. It reports false if no such run exists anywhere in
// obs, including the degenerate cases k <= 0 and k > len(obs) — both are
// requests that can never be satisfied, so they are treated as "not
// found" rather than a panic or an error return.
//
// A run only counts observations classified exactly as want: any other
// classification — including Error — resets the run to zero rather than
// being skipped over or credited toward want. This is what stops a
// transient Error (or an Allowed) from being silently absorbed into what
// looks like sustained enforcement.
//
// This is the mechanism behind t_blocked (see the "t_blocked: definition"
// section of docs/methodology.md): a single Blocked observation is not
// enough to conclude a NetworkPolicy transition happened, because a lone
// dropped packet looks identical to the start of real enforcement. k
// trades robustness to that noise against how long the harness must wait
// before it can know enforcement has begun: k-1 further observations past
// the first match. It does not shift the value returned — this function
// reports obs[start].Offset, the run's *first* observation, not its last
// — so t_blocked is the first blocked attempt of the run whatever k is.
// What the wait costs instead is trial duration: a stream that ends
// part-way through the run yields no t_blocked at all. Per
// docs/methodology.md, k must therefore be frozen in the relevant
// experiment's pre-registration (experiments/*/preregistration.md) before
// any run whose numbers get reported — it is not a knob to tune ad hoc
// per-run after seeing the data.
func FirstSustained(obs []Observation, k int, want Outcome) (time.Duration, bool) {
	if k <= 0 || k > len(obs) {
		return 0, false
	}

	run := 0
	start := 0
	for i, o := range obs {
		if o.Outcome != want {
			run = 0
			continue
		}

		if run == 0 {
			start = i
		}
		run++

		if run == k {
			return obs[start].Offset, true
		}
	}

	return 0, false
}
