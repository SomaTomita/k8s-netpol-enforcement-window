package k8swatch

import (
	"context"
	"errors"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/watch"
	k8sfake "k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"
)

func victimPod(name, ip string) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: "target",
			Labels:    map[string]string{"role": "victim"},
		},
		Status: corev1.PodStatus{PodIP: ip},
	}
}

func TestPodEvents_DeliversAddedEvents(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	var seen []string
	done := make(chan error, 1)
	go func() {
		done <- PodEvents(ctx, clientset, "target", "role=victim", func(p *corev1.Pod) (bool, error) {
			seen = append(seen, p.Name)
			return p.Name == "victim-2", nil
		})
	}()

	time.Sleep(50 * time.Millisecond)
	pods := clientset.CoreV1().Pods("target")
	mustCreatePod(t, pods, victimPod("victim-1", ""))
	time.Sleep(50 * time.Millisecond)
	mustCreatePod(t, pods, victimPod("victim-2", "10.244.0.1"))

	if err := waitDone(t, done); err != nil {
		t.Fatalf("PodEvents() error: %v", err)
	}
	if len(seen) != 2 || seen[0] != "victim-1" || seen[1] != "victim-2" {
		t.Errorf("seen = %v, want [victim-1 victim-2]", seen)
	}
}

func TestPodEvents_SkipsDeletedEvents(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	var calls int
	done := make(chan error, 1)
	go func() {
		done <- PodEvents(ctx, clientset, "target", "role=victim", func(p *corev1.Pod) (bool, error) {
			calls++
			return p.Name == "victim-2", nil
		})
	}()

	time.Sleep(50 * time.Millisecond)
	pods := clientset.CoreV1().Pods("target")
	// victim-1's IP is set so a Deleted event for it (which still carries
	// this same last-known status) would look like a valid observation if
	// event.Type weren't checked.
	mustCreatePod(t, pods, victimPod("victim-1", "10.244.0.9"))
	time.Sleep(50 * time.Millisecond)
	if err := pods.Delete(context.Background(), "victim-1", metav1.DeleteOptions{}); err != nil {
		t.Fatalf("delete victim-1: %v", err)
	}
	time.Sleep(50 * time.Millisecond)
	mustCreatePod(t, pods, victimPod("victim-2", "10.244.0.1"))

	if err := waitDone(t, done); err != nil {
		t.Fatalf("PodEvents() error: %v", err)
	}
	// Exactly 2 calls: Added for victim-1, Added for victim-2. The Deleted
	// event for victim-1 must not have produced a third call.
	if calls != 2 {
		t.Errorf("onEvent called %d times, want 2 (Deleted event must be skipped)", calls)
	}
}

func TestPodEvents_WrapsOnEventError(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	wantErr := errors.New("boom")
	done := make(chan error, 1)
	go func() {
		done <- PodEvents(ctx, clientset, "target", "role=victim", func(_ *corev1.Pod) (bool, error) {
			return false, wantErr
		})
	}()

	time.Sleep(50 * time.Millisecond)
	mustCreatePod(t, clientset.CoreV1().Pods("target"), victimPod("victim-1", "10.244.0.1"))

	err := waitDone(t, done)
	if !errors.Is(err, wantErr) {
		t.Errorf("PodEvents() error = %v, want it to wrap %v", err, wantErr)
	}
}

func TestPodEvents_ReturnsContextErrorOnCancel(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()
	ctx, cancel := context.WithCancel(context.Background())

	done := make(chan error, 1)
	go func() {
		done <- PodEvents(ctx, clientset, "target", "role=victim", func(_ *corev1.Pod) (bool, error) {
			return false, nil
		})
	}()

	time.Sleep(50 * time.Millisecond)
	cancel()

	err := waitDone(t, done)
	if !errors.Is(err, context.Canceled) {
		t.Errorf("PodEvents() error = %v, want context.Canceled", err)
	}
}

func TestPodEvents_RetriesTransientWatchEstablishmentError(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()

	var attempts int
	clientset.PrependWatchReactor("pods", func(_ k8stesting.Action) (bool, watch.Interface, error) {
		attempts++
		if attempts == 1 {
			return true, nil, errors.New("transient apiserver error")
		}
		return false, nil, nil // not handled: fall through to the default fake watch reactor
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	done := make(chan error, 1)
	go func() {
		done <- PodEvents(ctx, clientset, "target", "role=victim", func(_ *corev1.Pod) (bool, error) {
			return true, nil
		})
	}()

	time.Sleep(watchRetryDelay + 100*time.Millisecond)
	mustCreatePod(t, clientset.CoreV1().Pods("target"), victimPod("victim-1", "10.244.0.1"))

	if err := waitDone(t, done); err != nil {
		t.Fatalf("PodEvents() error: %v", err)
	}
	if attempts < 2 {
		t.Errorf("watch establishment attempts = %d, want >= 2 (first fails, second succeeds)", attempts)
	}
}

func mustCreatePod(t *testing.T, pods interface {
	Create(context.Context, *corev1.Pod, metav1.CreateOptions) (*corev1.Pod, error)
}, pod *corev1.Pod) {
	t.Helper()
	if _, err := pods.Create(context.Background(), pod, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create pod %q: %v", pod.Name, err)
	}
}

func waitDone(t *testing.T, done <-chan error) error {
	t.Helper()
	select {
	case err := <-done:
		return err
	case <-time.After(3 * time.Second):
		t.Fatal("PodEvents() did not return within 3s")
		return nil
	}
}
