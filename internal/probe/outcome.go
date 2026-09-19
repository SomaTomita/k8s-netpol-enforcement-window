// Package probe classifies the result of a single connection attempt into
// a CNI-agnostic Outcome.
//
// A raw connection error is not directly comparable across CNI
// implementations. eBPF-based CNIs (e.g. Cilium) drop the denied packet,
// which surfaces to the caller as a deadline/timeout; iptables-based CNIs
// reject the denied packet, which surfaces as connection refused or
// host/network unreachable. If the detection method left that difference
// unnormalized, the choice of CNI would become a confound in the measured
// window instead of a controlled variable. Classify is the single place
// this normalization happens — see docs/methodology.md for the
// measurement definition this feeds into.
package probe

import (
	"errors"
	"net"
	"syscall"
)

// Outcome is the CNI-agnostic result of a single connection attempt.
type Outcome int

const (
	// Unknown is the zero value: no classification has been made yet.
	Unknown Outcome = iota

	// Allowed means the connection attempt succeeded.
	Allowed

	// Blocked means the connection attempt failed in a way consistent
	// with a NetworkPolicy denying the traffic, whether the underlying
	// CNI dropped the packet (timeout) or rejected it (connection
	// refused, host unreachable, network unreachable).
	Blocked

	// Error means the connection attempt failed in a way not
	// attributable to policy enforcement.
	Error
)

// String returns the human-readable name of the Outcome.
func (o Outcome) String() string {
	switch o {
	case Allowed:
		return "Allowed"
	case Blocked:
		return "Blocked"
	case Error:
		return "Error"
	default:
		return "Unknown"
	}
}

// Classify normalizes a connection error into a CNI-agnostic Outcome.
//
// A nil error classifies as Allowed. A deadline/timeout (the eBPF-drop
// signature) or a connection-refused, host-unreachable, or
// network-unreachable error (the iptables-REJECT signature) all classify
// as Blocked. Any other non-nil error classifies as Error.
//
// Timeout detection goes through the net.Error interface rather than
// errors.Is(err, os.ErrDeadlineExceeded): a dial that fails because
// net.Dialer's own Timeout field fired (as opposed to an OS-level socket
// deadline) surfaces as an unexported *net.timeoutError wrapped in
// *net.OpError, which does not satisfy os.ErrDeadlineExceeded even though
// it is exactly the eBPF-drop signature this project needs to catch — this
// was confirmed against a live Cilium-blocked connection, where every
// dial-timeout observation was silently misclassified as Error until this
// check was switched to net.Error.Timeout(). errors.Is is still used for
// the syscall.Errno cases so wrapped errors — such as *os.SyscallError,
// which net dial errors are commonly wrapped in — still classify
// correctly.
func Classify(err error) Outcome {
	if err == nil {
		return Allowed
	}

	var netErr net.Error
	isTimeout := errors.As(err, &netErr) && netErr.Timeout()
	isConnRefused := errors.Is(err, syscall.ECONNREFUSED)
	isHostUnreachable := errors.Is(err, syscall.EHOSTUNREACH)
	isNetUnreachable := errors.Is(err, syscall.ENETUNREACH)
	if isTimeout || isConnRefused || isHostUnreachable || isNetUnreachable {
		return Blocked
	}

	return Error
}
