// Command watcher is the out-of-cluster half of the netpol-window
// measurement harness: it watches the Kubernetes API for Pod Ready
// transitions and records t_ready under candidate definition A
// (watch-local timestamp; see docs/methodology.md's "t_ready: three
// candidate definitions" section).
//
// Why not the API's own lastTransitionTime: a Pod's PodReady condition
// carries a LastTransitionTime, but that field is a metav1.Time, which
// serializes as RFC3339 and is therefore only second-precision. The
// window this project measures may be millisecond-scale, so a
// second-precision timestamp is useless for it — reading
// LastTransitionTime would round away exactly the signal being measured.
// Instead, this watcher stamps a monotonic clock (time.Since(start)) the
// instant it receives the watch event carrying PodReady=True, before any
// further decoding beyond what's needed to check the condition. That
// instant is the apiserver's notification time plus watcher-side receive
// latency, not the true readiness instant — a documented, conservative
// bias (see docs/methodology.md) — but it is at least measurable at the
// precision this project needs, which LastTransitionTime is not.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"

	"github.com/SomaTomita/netpol-window/internal/k8swatch"
)

// tReadyMethodA identifies candidate A (watch-local timestamp) as the
// t_ready definition this binary implements. See docs/methodology.md.
const tReadyMethodA = "A"

// readyEvent is one Pod's first observed Ready transition, as seen by
// this watcher. OffsetNs is time.Since(start) at the moment the watch
// event was received, in nanoseconds — never derived from the Pod's own
// status.conditions[].lastTransitionTime (see package doc comment). start
// is process start by default, or the shared -run-epoch instant when that
// flag is set (see docs/methodology.md's Clocks section).
type readyEvent struct {
	PodName      string `json:"pod_name"`
	OffsetNs     int64  `json:"offset_ns"`
	TReadyMethod string `json:"t_ready_method"`
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

// run parses flags, builds the Kubernetes client, and runs the watch loop
// until -duration elapses. Kept separate from main so the deferred
// context cancel always runs before exit (log.Fatal from within a
// function that also defers cleanup would skip that cleanup).
func run() error {
	kubeconfig := flag.String("kubeconfig", os.Getenv("KUBECONFIG"), "path to kubeconfig file")
	namespace := flag.String("namespace", "target", "namespace to watch for Pod Ready transitions")
	selector := flag.String("selector", "role=victim", "label selector for Pods to watch")
	duration := flag.Duration("duration", 60*time.Second, "how long to watch before exiting")
	runEpoch := flag.Int64("run-epoch", 0, "unix nanoseconds; when set, offset_ns is measured from this shared instant instead of process start, so this binary's stream becomes combinable with another process's (see docs/methodology.md's Clocks section)")
	flag.Parse()

	config, err := clientcmd.BuildConfigFromFlags("", *kubeconfig)
	if err != nil {
		return fmt.Errorf("load kubeconfig %q: %w", *kubeconfig, err)
	}

	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		return fmt.Errorf("build clientset: %w", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), *duration)
	defer cancel()

	start := time.Now()
	if *runEpoch != 0 {
		start = time.Unix(0, *runEpoch)
	}

	return watchPodReady(ctx, clientset, *namespace, *selector, os.Stdout, start)
}

// watchPodReady watches Pods in namespace matching selector and writes one
// JSON-encoded readyEvent per line to out for each pod's first observed
// PodReady=True transition. Each pod name is recorded at most once: once
// seen, later Ready events for the same pod (e.g. a subsequent Modified
// event) are ignored. Each event's OffsetNs is time.Since(start); start is
// either process start or a shared -run-epoch instant, depending on how the
// caller set it up. watchPodReady returns nil when ctx is done (the
// -duration timeout elapsed, or SIGINT/SIGTERM upstream) — that's this
// binary's normal, expected way to stop, not a failure — or when an event
// fails to encode.
func watchPodReady(ctx context.Context, clientset kubernetes.Interface, namespace, selector string, out io.Writer, start time.Time) error {
	enc := json.NewEncoder(out)
	seen := make(map[string]bool)

	err := k8swatch.PodEvents(ctx, clientset, namespace, selector, func(pod *corev1.Pod) (bool, error) {
		// Stamp the offset the instant this event reaches here — the
		// earliest point after k8swatch.PodEvents has already ruled out
		// Deleted/Bookmark events, which are never a valid Ready
		// observation (see that package's doc comment). This is t_ready
		// candidate A — see this file's package doc comment.
		offset := time.Since(start)

		if seen[pod.Name] || !isPodReady(pod) {
			return false, nil
		}
		seen[pod.Name] = true

		if err := enc.Encode(readyEvent{
			PodName:      pod.Name,
			OffsetNs:     offset.Nanoseconds(),
			TReadyMethod: tReadyMethodA,
		}); err != nil {
			return false, fmt.Errorf("encode ready event for pod %q: %w", pod.Name, err)
		}
		return false, nil
	})

	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		return nil
	}
	return err
}

// isPodReady reports whether pod's PodReady condition is currently True.
// This checks the condition's Status only, never its LastTransitionTime
// (see package doc comment for why).
func isPodReady(pod *corev1.Pod) bool {
	for _, cond := range pod.Status.Conditions {
		if cond.Type == corev1.PodReady {
			return cond.Status == corev1.ConditionTrue
		}
	}
	return false
}
