package probe

import (
	"errors"
	"net"
	"os"
	"syscall"
	"testing"
)

// dialTimeoutError simulates the error net.Dialer's own Timeout field
// produces on a dial timeout: an unexported type satisfying net.Error with
// Timeout() == true, wrapped in *net.OpError. This is distinct from
// os.ErrDeadlineExceeded (an OS-level socket deadline error) and is the
// exact shape confirmed against a live Cilium-blocked connection — see
// Classify's doc comment in outcome.go.
type dialTimeoutError struct{}

func (dialTimeoutError) Error() string { return "i/o timeout" }
func (dialTimeoutError) Timeout() bool { return true }

func TestClassify(t *testing.T) {
	tests := []struct {
		name     string
		err      error
		expected Outcome
	}{
		{
			name:     "nil error is allowed",
			err:      nil,
			expected: Allowed,
		},
		{
			name:     "deadline exceeded is blocked",
			err:      os.ErrDeadlineExceeded,
			expected: Blocked,
		},
		{
			name:     "connection refused is blocked",
			err:      syscall.ECONNREFUSED,
			expected: Blocked,
		},
		{
			name:     "host unreachable is blocked",
			err:      syscall.EHOSTUNREACH,
			expected: Blocked,
		},
		{
			name:     "network unreachable is blocked",
			err:      syscall.ENETUNREACH,
			expected: Blocked,
		},
		{
			name:     "unrelated error is error",
			err:      errors.New("boom"),
			expected: Error,
		},
		{
			name: "wrapped syscall error is blocked",
			err: &os.SyscallError{
				Syscall: "connect",
				Err:     syscall.ECONNREFUSED,
			},
			expected: Blocked,
		},
		{
			name: "net.Dialer's own Timeout firing (not an OS deadline) is blocked",
			err: &net.OpError{
				Op:  "dial",
				Net: "tcp",
				Err: dialTimeoutError{},
			},
			expected: Blocked,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := Classify(tt.err)
			if got != tt.expected {
				t.Errorf("Classify(%v) = %v, want %v", tt.err, got, tt.expected)
			}
		})
	}
}
