package record

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestRunRecord_Window(t *testing.T) {
	tests := []struct {
		name       string
		tReadyNs   int64
		tBlockedNs int64
		want       time.Duration
	}{
		{
			name:       "positive window: unprotected gap exists",
			tReadyNs:   1_000_000_000,
			tBlockedNs: 1_250_000_000,
			want:       250 * time.Millisecond,
		},
		{
			name:       "negative window: policy already enforced before ready",
			tReadyNs:   2_000_000_000,
			tBlockedNs: 1_900_000_000,
			want:       -100 * time.Millisecond,
		},
		{
			name:       "zero window: simultaneous",
			tReadyNs:   1_500_000_000,
			tBlockedNs: 1_500_000_000,
			want:       0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rec := RunRecord{TReadyNs: tt.tReadyNs, TBlockedNs: tt.tBlockedNs}
			if got := rec.Window(); got != tt.want {
				t.Errorf("Window() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestWriteJSONL_RoundTrip(t *testing.T) {
	rec := RunRecord{
		SchemaVersion:    SchemaVersion,
		RunID:            "run-001",
		CNIName:          "cilium",
		CNIVersion:       "1.15.0",
		PolicySet:        "deny-all-then-allow",
		ChurnRate:        0.5,
		Repetition:       3,
		ProbeInterval:    10 * time.Millisecond,
		SustainedK:       5,
		TReadyMethod:     "B",
		TReadyNs:         1_000_000_000,
		TBlockedNs:       1_050_000_000,
		ErrorRate:        0.01,
		ObservationCount: 200,
	}

	var buf bytes.Buffer
	if err := WriteJSONL(&buf, []RunRecord{rec}); err != nil {
		t.Fatalf("WriteJSONL() error = %v", err)
	}

	line := strings.TrimRight(buf.String(), "\n")
	var got RunRecord
	if err := json.Unmarshal([]byte(line), &got); err != nil {
		t.Fatalf("json.Unmarshal() error = %v", err)
	}

	if got.RunID != rec.RunID {
		t.Errorf("RunID = %q, want %q", got.RunID, rec.RunID)
	}
	if got.CNIName != rec.CNIName {
		t.Errorf("CNIName = %q, want %q", got.CNIName, rec.CNIName)
	}
	if got.CNIVersion != rec.CNIVersion {
		t.Errorf("CNIVersion = %q, want %q", got.CNIVersion, rec.CNIVersion)
	}
	if got.PolicySet != rec.PolicySet {
		t.Errorf("PolicySet = %q, want %q", got.PolicySet, rec.PolicySet)
	}
	if got.TReadyMethod != rec.TReadyMethod {
		t.Errorf("TReadyMethod = %q, want %q", got.TReadyMethod, rec.TReadyMethod)
	}
	if got.TReadyNs != rec.TReadyNs {
		t.Errorf("TReadyNs = %d, want %d", got.TReadyNs, rec.TReadyNs)
	}
	if got.TBlockedNs != rec.TBlockedNs {
		t.Errorf("TBlockedNs = %d, want %d", got.TBlockedNs, rec.TBlockedNs)
	}
	if got.SchemaVersion != rec.SchemaVersion {
		t.Errorf("SchemaVersion = %d, want %d", got.SchemaVersion, rec.SchemaVersion)
	}
	if got.ProbeInterval != rec.ProbeInterval {
		t.Errorf("ProbeInterval = %v, want %v", got.ProbeInterval, rec.ProbeInterval)
	}
}

func TestWriteJSONL_DefaultsSchemaVersion(t *testing.T) {
	rec := RunRecord{RunID: "run-002"} // SchemaVersion left zero.

	var buf bytes.Buffer
	if err := WriteJSONL(&buf, []RunRecord{rec}); err != nil {
		t.Fatalf("WriteJSONL() error = %v", err)
	}

	var got RunRecord
	if err := json.Unmarshal(buf.Bytes(), &got); err != nil {
		t.Fatalf("json.Unmarshal() error = %v", err)
	}
	if got.SchemaVersion != SchemaVersion {
		t.Errorf("SchemaVersion = %d, want default %d", got.SchemaVersion, SchemaVersion)
	}
	if rec.SchemaVersion != 0 {
		t.Errorf("input record was mutated: SchemaVersion = %d, want 0", rec.SchemaVersion)
	}
}

func TestWriteJSONL_OneRecordPerLine(t *testing.T) {
	recs := []RunRecord{
		{RunID: "run-a"},
		{RunID: "run-b"},
		{RunID: "run-c"},
	}

	var buf bytes.Buffer
	if err := WriteJSONL(&buf, recs); err != nil {
		t.Fatalf("WriteJSONL() error = %v", err)
	}

	got := strings.Count(buf.String(), "\n")
	want := len(recs)
	if got != want {
		t.Errorf("newline count = %d, want %d", got, want)
	}

	lines := strings.Split(strings.TrimRight(buf.String(), "\n"), "\n")
	if len(lines) != len(recs) {
		t.Fatalf("got %d lines, want %d", len(lines), len(recs))
	}
	for i, line := range lines {
		var rec RunRecord
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			t.Errorf("line %d: json.Unmarshal() error = %v", i, err)
		}
	}
}
