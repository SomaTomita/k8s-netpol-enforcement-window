// Package record defines RunRecord, the JSON Lines schema shared between
// the Go measurement binaries (cmd/prober, cmd/watcher) and the Python
// analysis code (src/npw). Each RunRecord captures one measured run of
// the experiment: the conditions it ran under, the raw timestamps, and
// derived quality signals. One record is written per line so that a
// partially completed experiment (process killed mid-run) still leaves
// behind a file that can be parsed line by line.
//
// This package is the contract between the two languages: field names,
// types, and units on RunRecord must not change in a way that breaks an
// existing consumer without bumping SchemaVersion. Any breaking change
// (renaming a field, changing its type or unit, removing a field) must
// increment SchemaVersion so readers can detect and reject data written
// under an incompatible schema.
package record

import (
	"encoding/json"
	"fmt"
	"io"
	"time"
)

// SchemaVersion is the current version of the RunRecord JSON schema.
// Bump this constant whenever a breaking change is made to RunRecord's
// fields (rename, type change, removal) so that Python readers can
// detect incompatible data.
const SchemaVersion = 1

// RunRecord is one measured run of the netpol-window experiment: the
// conditions it ran under (CNI, policy set, churn rate, repetition), the
// raw t_ready/t_blocked timestamps, and quality signals from the probe
// loop.
//
// TReadyMethod records which of the candidate t_ready definitions
// (A/B/C, see docs/adr/0003) was used to produce TReadyNs. Which
// definition is correct is still an open research question, so it is
// captured per record rather than assumed fixed.
type RunRecord struct {
	// SchemaVersion is the RunRecord schema version this record was
	// written under. WriteJSONL fills this in with SchemaVersion if
	// left zero.
	SchemaVersion int `json:"schema_version"`

	// RunID uniquely identifies this run within an experiment.
	RunID string `json:"run_id"`

	// CNIName is the CNI plugin under test (e.g. "cilium", "calico").
	CNIName string `json:"cni_name"`

	// CNIVersion is the version of the CNI plugin under test.
	CNIVersion string `json:"cni_version"`

	// PolicySet identifies which NetworkPolicy set was applied.
	PolicySet string `json:"policy_set"`

	// ChurnRate is the rate of Pod churn applied during the run.
	ChurnRate float64 `json:"churn_rate"`

	// Repetition is the 1-based repetition number of this run within
	// its experiment condition.
	Repetition int `json:"repetition"`

	// ProbeInterval is the interval between probe attempts.
	ProbeInterval time.Duration `json:"probe_interval_ns"`

	// SustainedK is the number of consecutive matching probe outcomes
	// required before a state transition (ready/blocked) is accepted.
	SustainedK int `json:"sustained_k"`

	// TReadyMethod records which t_ready definition (A/B/C) was used
	// to produce TReadyNs.
	TReadyMethod string `json:"t_ready_method"`

	// TReadyNs is when the Pod became able to receive traffic, in
	// nanoseconds since the Unix epoch.
	TReadyNs int64 `json:"t_ready_ns"`

	// TBlockedNs is when traffic that should be blocked by the
	// NetworkPolicy was actually first blocked, in nanoseconds since
	// the Unix epoch.
	TBlockedNs int64 `json:"t_blocked_ns"`

	// ErrorRate is the fraction of probe attempts that classified as
	// Error (neither cleanly Allowed nor Blocked) during this run.
	ErrorRate float64 `json:"error_rate"`

	// ObservationCount is the total number of probe observations made
	// during this run.
	ObservationCount int `json:"observation_count"`
}

// Window returns the unprotected window: the time between the Pod
// becoming ready and the NetworkPolicy actually blocking traffic
// (t_blocked - t_ready).
//
// A positive Window means the Pod was reachable without policy
// enforcement for that long. A negative Window means enforcement was
// already active before the Pod became ready, so there was no
// unprotected gap. A zero Window means both events were observed at the
// same instant.
func (r RunRecord) Window() time.Duration {
	return time.Duration(r.TBlockedNs - r.TReadyNs)
}

// WriteJSONL writes recs to w as JSON Lines: one JSON object per line,
// in order. If a record's SchemaVersion is zero, WriteJSONL defaults it
// to SchemaVersion before writing; the input slice is left unmodified.
func WriteJSONL(w io.Writer, recs []RunRecord) error {
	enc := json.NewEncoder(w)
	for _, rec := range recs {
		if rec.SchemaVersion == 0 {
			rec.SchemaVersion = SchemaVersion
		}
		if err := enc.Encode(rec); err != nil {
			return fmt.Errorf("write record %q: %w", rec.RunID, err)
		}
	}
	return nil
}
