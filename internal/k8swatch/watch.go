// Package k8swatch is a thin, shared wrapper around watching Pods for a
// condition, used by both cmd/watcher (candidate A's t_ready) and
// cmd/prober (resolving the victim's IP internally rather than being told
// a static address). Both need the same watch mechanics — establish a
// watch, retry on a transient establishment error, skip events that are
// never a valid observation, re-establish on an unexpected channel close —
// and previously duplicated them; a bug fixed in one place but not the
// other is exactly the failure mode this package exists to remove.
package k8swatch

import (
	"context"
	"fmt"
	"log"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/kubernetes"
)

// watchRetryDelay is how long to wait before retrying to establish a watch
// after a transient error (e.g. the apiserver briefly unreachable, or a
// ServiceAccount token not yet propagated when a Pod has just started).
const watchRetryDelay = 250 * time.Millisecond

// OnPod is called for every Added or Modified Pod event PodEvents
// observes. Returning done=true stops the watch and PodEvents returns nil;
// a non-nil err stops the watch and PodEvents returns it.
type OnPod func(pod *corev1.Pod) (done bool, err error)

// PodEvents watches Pods in namespace matching selector, calling onEvent
// for each Added or Modified one, until ctx is done or onEvent signals
// it's done.
//
// Deleted and Bookmark events are never delivered to onEvent: a Deleted
// event's Object is the Pod's last-known state before removal, and fields
// like status.PodIP or a Ready condition are typically still populated at
// that point — treating a deletion as a fresh observation of either would
// be wrong for every caller of this package, not a case-by-case judgment.
//
// A transient error establishing the watch is retried (bounded by ctx); an
// unexpected channel close (e.g. a relist boundary) re-establishes a fresh
// watch rather than failing.
//
// PodEvents returns ctx.Err() when ctx is the reason it stopped — the
// caller decides whether that's expected (e.g. a watcher meant to run for
// a fixed duration) or an error (e.g. a target that never appeared).
func PodEvents(ctx context.Context, clientset kubernetes.Interface, namespace, selector string, onEvent OnPod) error {
	for {
		w, err := clientset.CoreV1().Pods(namespace).Watch(ctx, metav1.ListOptions{
			LabelSelector: selector,
		})
		if err != nil {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(watchRetryDelay):
				continue
			}
		}

		done, consumeErr := consume(ctx, w, onEvent)
		w.Stop()
		if consumeErr != nil {
			return consumeErr
		}
		if done {
			return nil
		}
		// ctx isn't done and onEvent never signaled done: the channel
		// closed on its own. Re-establish and keep watching.
	}
}

// consume reads from w until ctx is done, onEvent signals done, onEvent
// returns an error, or the channel closes on its own (done=false, err=nil
// — the caller re-establishes a fresh watch in that case).
func consume(ctx context.Context, w watch.Interface, onEvent OnPod) (done bool, err error) {
	for {
		select {
		case <-ctx.Done():
			return true, ctx.Err()
		case event, ok := <-w.ResultChan():
			if !ok {
				return false, nil
			}
			if event.Type == watch.Error {
				log.Printf("k8swatch: received watch error event: %v", event.Object)
				continue
			}
			if event.Type != watch.Added && event.Type != watch.Modified {
				continue
			}
			pod, ok := event.Object.(*corev1.Pod)
			if !ok {
				continue
			}
			d, err := onEvent(pod)
			if err != nil {
				return true, fmt.Errorf("onEvent for pod %q: %w", pod.Name, err)
			}
			if d {
				return true, nil
			}
		}
	}
}
