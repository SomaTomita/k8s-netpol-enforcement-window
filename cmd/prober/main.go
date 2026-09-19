// Command prober is an in-cluster binary that repeatedly dials a target
// and emits one JSON line per attempt describing when the attempt was
// made (as a monotonic offset from process start) and how it was
// classified. It deliberately does not compute t_blocked itself: raw
// observations are kept so the sustained-run threshold k (see
// docs/methodology.md) can be recomputed later without re-running the
// experiment.
//
// The target can be given directly (-target host:port) or discovered
// internally via -target-selector: the prober watches the Kubernetes API
// for a Pod matching a namespace/label selector and starts dialing the
// instant it has an IP. The latter exists so the prober Pod can be
// deployed — and start watching — before the victim Pod even exists,
// rather than being created fresh once an orchestrator already knows the
// victim's IP. A freshly-created Pod's own scheduling and sandbox setup
// was itself found to cost 648-904ms in a live pilot (ADR 0003,
// "Correction, 2026-09-19") — time during which the prober cannot see
// anything at all, whatever the window turns out to be; see
// docs/threats-to-validity.md.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"

	"github.com/SomaTomita/netpol-window/internal/k8swatch"
	"github.com/SomaTomita/netpol-window/internal/probe"
)

// observation is one probe attempt: how long after process start the
// attempt began, and how it classified. Kept minimal and local to this
// command rather than reusing internal/record.RunRecord, since the
// prober's job is to emit the raw per-attempt stream, not the derived,
// per-run record that package produces.
type observation struct {
	// OffsetNs is time.Since(start), in nanoseconds, captured immediately
	// before the dial for this attempt begins. start is process start by
	// default, or the shared -run-epoch instant when that flag is set
	// (see docs/methodology.md's Clocks section).
	OffsetNs int64 `json:"offset_ns"`

	// Outcome is the CNI-agnostic classification of the attempt
	// (Allowed, Blocked, or Error). See internal/probe/outcome.go.
	Outcome string `json:"outcome"`
}

func main() {
	if err := run(os.Args[1:], os.Stdout); err != nil {
		log.Fatal(err)
	}
}

// run parses flags, resolves the target (directly or by watching for it),
// opens the output, and runs the probe loop until -duration elapses or the
// process receives SIGINT/SIGTERM.
func run(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("prober", flag.ContinueOnError)
	target := fs.String("target", "", "target to dial, host:port (mutually exclusive with -target-selector)")
	targetNamespace := fs.String("target-namespace", "", "namespace to watch for the target Pod (required with -target-selector)")
	targetSelector := fs.String("target-selector", "", "label selector for the target Pod; when set, the prober watches the Kubernetes API for its IP internally instead of dialing a static -target")
	targetPort := fs.Int("target-port", 0, "port to dial once the target Pod's IP is known (required with -target-selector)")
	targetWaitTimeout := fs.Duration("target-wait-timeout", 60*time.Second, "how long to wait for the target Pod to get an IP, with -target-selector (separate from -duration, which only bounds the probe loop itself)")
	kubeconfig := fs.String("kubeconfig", os.Getenv("KUBECONFIG"), "path to kubeconfig file (used with -target-selector; falls back to in-cluster config when unset and running in a Pod)")
	interval := fs.Duration("interval", time.Millisecond, "interval between probe attempts")
	timeout := fs.Duration("timeout", 200*time.Millisecond, "per-attempt dial timeout")
	duration := fs.Duration("duration", 30*time.Second, "total duration to run the probe loop")
	out := fs.String("out", "-", `output path for JSON lines, or "-" for stdout`)
	runEpoch := fs.Int64("run-epoch", 0, "unix nanoseconds; when set, offset_ns is measured from this shared instant instead of process start, so this binary's stream becomes combinable with another process's (see docs/methodology.md's Clocks section)")
	if err := fs.Parse(args); err != nil {
		return err
	}

	if err := validateTargetFlags(*target, *targetSelector, *targetNamespace, *targetPort); err != nil {
		return err
	}

	start := time.Now()
	if *runEpoch != 0 {
		start = time.Unix(0, *runEpoch)
	}

	w := stdout
	if *out != "-" {
		f, err := os.Create(*out)
		if err != nil {
			return fmt.Errorf("open output %q: %w", *out, err)
		}
		defer func() {
			if cerr := f.Close(); cerr != nil {
				log.Printf("close output %q: %v", *out, cerr)
			}
		}()
		w = f
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	resolvedTarget := *target
	if *targetSelector != "" {
		clientset, err := buildClientset(*kubeconfig)
		if err != nil {
			return fmt.Errorf("build k8s clientset: %w", err)
		}
		// Bounded by -target-wait-timeout, not -duration: -duration's own
		// documented meaning ("total duration to run the probe loop")
		// must stay exactly that — resolving the target is a separate
		// phase with its own separate bound, so a target that never
		// appears fails after -target-wait-timeout instead of hanging
		// forever (ctx here is only canceled by SIGINT/SIGTERM).
		resolveCtx, cancelResolve := context.WithTimeout(ctx, *targetWaitTimeout)
		ip, err := waitForTargetIP(resolveCtx, clientset, *targetNamespace, *targetSelector)
		cancelResolve()
		if err != nil {
			return fmt.Errorf("wait for target pod ip: %w", err)
		}
		resolvedTarget = fmt.Sprintf("%s:%d", ip, *targetPort)
	}

	return probeLoop(ctx, w, resolvedTarget, *interval, *timeout, *duration, start)
}

// validateTargetFlags checks that exactly one target-resolution mode is
// configured, before any network or Kubernetes API call is attempted.
func validateTargetFlags(target, targetSelector, targetNamespace string, targetPort int) error {
	if target == "" && targetSelector == "" {
		return errors.New("either -target or -target-selector is required")
	}
	if target != "" && targetSelector != "" {
		return errors.New("-target and -target-selector are mutually exclusive")
	}
	if targetSelector != "" && targetPort == 0 {
		return errors.New("-target-port is required with -target-selector")
	}
	if targetSelector != "" && targetNamespace == "" {
		// clientset.CoreV1().Pods("") watches every namespace the
		// caller's credentials can see, not zero namespaces — an empty
		// value here would silently turn a scoped watch into a
		// cluster-wide one instead of failing fast.
		return errors.New("-target-namespace is required with -target-selector")
	}
	return nil
}

// buildClientset tries in-cluster config first (the prober's normal
// deployment mode, as a Pod with a mounted ServiceAccount token), falling
// back to kubeconfig for local/manual runs outside a cluster.
func buildClientset(kubeconfig string) (kubernetes.Interface, error) {
	cfg, err := rest.InClusterConfig()
	if err != nil {
		cfg, err = clientcmd.BuildConfigFromFlags("", kubeconfig)
		if err != nil {
			return nil, fmt.Errorf("build config (tried in-cluster and kubeconfig %q): %w", kubeconfig, err)
		}
	}
	return kubernetes.NewForConfig(cfg)
}

// waitForTargetIP watches Pods in namespace matching selector and returns
// the IP of the first one that has one assigned. It does not wait for the
// Pod to become Ready — an assigned IP is the earliest addressable signal,
// which is what lets the prober start dialing before the CNI has finished
// enforcing policy against it, rather than only after.
//
// ctx bounds the whole wait (the caller derives it from -target-wait-timeout,
// not -duration): if no matching Pod gets an IP before ctx is done,
// waitForTargetIP returns ctx.Err().
func waitForTargetIP(ctx context.Context, clientset kubernetes.Interface, namespace, selector string) (string, error) {
	var ip string
	err := k8swatch.PodEvents(ctx, clientset, namespace, selector, func(pod *corev1.Pod) (bool, error) {
		if pod.Status.PodIP == "" {
			return false, nil
		}
		ip = pod.Status.PodIP
		return true, nil
	})
	if err != nil {
		return "", err
	}
	if ip == "" {
		// PodEvents returned nil without onEvent ever signaling done: the
		// watch channel closed on its own with ctx still live, which
		// k8swatch.PodEvents already retried by re-establishing — reaching
		// here with no IP means ctx ended the wait in the same instant.
		return "", ctx.Err()
	}
	return ip, nil
}

// probeLoop dials target every interval, classifies each attempt, and
// writes one JSON-encoded observation per line to w. It returns when
// duration has elapsed or ctx is done (SIGINT/SIGTERM). Each observation's
// OffsetNs is time.Since(start); start is either process start or a shared
// -run-epoch instant, depending on how the caller set it up.
func probeLoop(ctx context.Context, w io.Writer, target string, interval, timeout, duration time.Duration, start time.Time) error {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	deadline := time.After(duration)
	enc := json.NewEncoder(w)
	dialer := net.Dialer{Timeout: timeout}

	for {
		select {
		case <-ctx.Done():
			return nil
		case <-deadline:
			return nil
		case <-ticker.C:
			// Offset is captured before dialing, so it reflects when
			// this attempt was initiated rather than when it resolved
			// (a Blocked/timeout attempt can itself take up to
			// -timeout to resolve).
			offset := time.Since(start)

			conn, err := dialer.DialContext(ctx, "tcp", target)
			if conn != nil {
				_ = conn.Close()
			}

			obs := observation{
				OffsetNs: offset.Nanoseconds(),
				Outcome:  probe.Classify(err).String(),
			}
			if err := enc.Encode(obs); err != nil {
				return fmt.Errorf("encode observation: %w", err)
			}
		}
	}
}
