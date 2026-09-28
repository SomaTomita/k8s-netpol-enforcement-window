package probe

import (
	"testing"
	"time"
)

func TestFirstSustained(t *testing.T) {
	const ms = time.Millisecond

	tests := []struct {
		name       string
		obs        []Observation
		k          int
		want       Outcome
		wantOffset time.Duration
		wantFound  bool
	}{
		{
			name: "single blip is not mistaken for a sustained run",
			obs: []Observation{
				{Offset: 0 * ms, Outcome: Allowed},
				{Offset: 10 * ms, Outcome: Allowed},
				{Offset: 20 * ms, Outcome: Blocked},
				{Offset: 30 * ms, Outcome: Allowed},
				{Offset: 40 * ms, Outcome: Allowed},
				{Offset: 50 * ms, Outcome: Blocked},
				{Offset: 60 * ms, Outcome: Blocked},
				{Offset: 70 * ms, Outcome: Blocked},
			},
			k:          3,
			want:       Blocked,
			wantOffset: 50 * ms,
			wantFound:  true,
		},
		{
			name: "blocked from the start is detected at offset zero",
			obs: []Observation{
				{Offset: 0 * ms, Outcome: Blocked},
				{Offset: 10 * ms, Outcome: Blocked},
			},
			k:          2,
			want:       Blocked,
			wantOffset: 0,
			wantFound:  true,
		},
		{
			name: "no sustained run when k is never satisfied returns not found",
			obs: []Observation{
				{Offset: 0 * ms, Outcome: Blocked},
				{Offset: 10 * ms, Outcome: Blocked},
				{Offset: 20 * ms, Outcome: Blocked},
				{Offset: 30 * ms, Outcome: Allowed},
				{Offset: 40 * ms, Outcome: Blocked},
				{Offset: 50 * ms, Outcome: Blocked},
			},
			k:         4,
			want:      Blocked,
			wantFound: false,
		},
		{
			name: "an Error breaks a run in progress",
			obs: []Observation{
				{Offset: 0 * ms, Outcome: Blocked},
				{Offset: 10 * ms, Outcome: Blocked},
				{Offset: 20 * ms, Outcome: Error},
				{Offset: 30 * ms, Outcome: Blocked},
				{Offset: 40 * ms, Outcome: Blocked},
				{Offset: 50 * ms, Outcome: Blocked},
			},
			k:          3,
			want:       Blocked,
			wantOffset: 30 * ms,
			wantFound:  true,
		},
		{
			name: "k=1 triggers immediately on the first match",
			obs: []Observation{
				{Offset: 0 * ms, Outcome: Allowed},
				{Offset: 10 * ms, Outcome: Blocked},
			},
			k:          1,
			want:       Blocked,
			wantOffset: 10 * ms,
			wantFound:  true,
		},
		{
			name: "k=0 is rejected as not found, not a panic",
			obs: []Observation{
				{Offset: 0 * ms, Outcome: Blocked},
				{Offset: 10 * ms, Outcome: Blocked},
			},
			k:         0,
			want:      Blocked,
			wantFound: false,
		},
		{
			name: "k greater than len(observations) is rejected as not found, not a panic",
			obs: []Observation{
				{Offset: 0 * ms, Outcome: Blocked},
				{Offset: 10 * ms, Outcome: Blocked},
			},
			k:         3,
			want:      Blocked,
			wantFound: false,
		},
		{
			name:      "empty observations with k=1 is not found, not a panic",
			obs:       nil,
			k:         1,
			want:      Blocked,
			wantFound: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotOffset, gotFound := FirstSustained(tt.obs, tt.k, tt.want)
			if gotFound != tt.wantFound {
				t.Fatalf("FirstSustained() found = %v, want %v", gotFound, tt.wantFound)
			}
			if gotFound && gotOffset != tt.wantOffset {
				t.Errorf("FirstSustained() offset = %v, want %v", gotOffset, tt.wantOffset)
			}
		})
	}
}
