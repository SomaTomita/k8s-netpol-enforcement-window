package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	k8sfake "k8s.io/client-go/kubernetes/fake"
)

func TestIsPodReady(t *testing.T) {
	tests := []struct {
		name string
		pod  *corev1.Pod
		want bool
	}{
		{
			name: "PodReady condition True",
			pod: &corev1.Pod{Status: corev1.PodStatus{Conditions: []corev1.PodCondition{
				{Type: corev1.PodReady, Status: corev1.ConditionTrue},
			}}},
			want: true,
		},
		{
			name: "PodReady condition False",
			pod: &corev1.Pod{Status: corev1.PodStatus{Conditions: []corev1.PodCondition{
				{Type: corev1.PodReady, Status: corev1.ConditionFalse},
			}}},
			want: false,
		},
		{
			name: "no PodReady condition present",
			pod: &corev1.Pod{Status: corev1.PodStatus{Conditions: []corev1.PodCondition{
				{Type: corev1.PodInitialized, Status: corev1.ConditionTrue},
			}}},
			want: false,
		},
		{
			name: "no conditions at all",
			pod:  &corev1.Pod{},
			want: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := isPodReady(tt.pod); got != tt.want {
				t.Errorf("isPodReady() = %v, want %v", got, tt.want)
			}
		})
	}
}

// testNamespace is the namespace every readyPod fixture and
// TestWatchPodReady_RecordsEachPodOnce use.
const testNamespace = "target"

// readyPod builds a Pod in testNamespace matching selector role=victim,
// with a PodReady condition of the given status.
func readyPod(name string, ready bool) *corev1.Pod {
	status := corev1.ConditionFalse
	if ready {
		status = corev1.ConditionTrue
	}
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: testNamespace,
			Labels:    map[string]string{"role": "victim"},
		},
		Status: corev1.PodStatus{
			Conditions: []corev1.PodCondition{
				{Type: corev1.PodReady, Status: status},
			},
		},
	}
}

func TestWatchPodReady_RecordsEachPodOnce(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	var out bytes.Buffer
	done := make(chan error, 1)
	go func() {
		done <- watchPodReady(ctx, clientset, testNamespace, "role=victim", &out, time.Now())
	}()

	// Give watchPodReady time to establish its watch before pods are
	// created, so the Create events are actually observed on the channel.
	time.Sleep(50 * time.Millisecond)

	ctxBg := context.Background()
	pods := clientset.CoreV1().Pods(testNamespace)
	mustCreate(ctxBg, t, pods, readyPod("victim-1", true))
	mustCreate(ctxBg, t, pods, readyPod("victim-2", false))

	// Modify victim-2 to Ready, and re-modify victim-1 (already Ready) to
	// confirm it is not recorded twice.
	time.Sleep(50 * time.Millisecond)
	mustUpdate(ctxBg, t, pods, readyPod("victim-2", true))
	mustUpdate(ctxBg, t, pods, readyPod("victim-1", true))

	cancel()
	if err := <-done; err != nil {
		t.Fatalf("watchPodReady returned error: %v", err)
	}

	var events []readyEvent
	scanner := bufio.NewScanner(&out)
	for scanner.Scan() {
		var ev readyEvent
		if err := json.Unmarshal(scanner.Bytes(), &ev); err != nil {
			t.Fatalf("unmarshal line %q: %v", scanner.Text(), err)
		}
		events = append(events, ev)
	}

	if len(events) != 2 {
		t.Fatalf("got %d ready events, want 2: %+v", len(events), events)
	}

	seen := map[string]bool{}
	for _, ev := range events {
		if ev.TReadyMethod != tReadyMethodA {
			t.Errorf("event for pod %q: t_ready_method = %q, want %q", ev.PodName, ev.TReadyMethod, tReadyMethodA)
		}
		if ev.OffsetNs < 0 {
			t.Errorf("event for pod %q: offset_ns = %d, want >= 0", ev.PodName, ev.OffsetNs)
		}
		if seen[ev.PodName] {
			t.Errorf("pod %q recorded more than once", ev.PodName)
		}
		seen[ev.PodName] = true
	}
	if !seen["victim-1"] || !seen["victim-2"] {
		t.Errorf("expected events for victim-1 and victim-2, got %+v", events)
	}
}

func TestWatchPodReady_OffsetIsMeasuredFromGivenStart(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	start := time.Now().Add(-1 * time.Second)

	var out bytes.Buffer
	done := make(chan error, 1)
	go func() {
		done <- watchPodReady(ctx, clientset, testNamespace, "role=victim", &out, start)
	}()

	time.Sleep(50 * time.Millisecond)
	mustCreate(context.Background(), t, clientset.CoreV1().Pods(testNamespace), readyPod("victim-1", true))

	cancel()
	if err := <-done; err != nil {
		t.Fatalf("watchPodReady returned error: %v", err)
	}

	scanner := bufio.NewScanner(&out)
	if !scanner.Scan() {
		t.Fatal("expected at least one ready event")
	}
	var ev readyEvent
	if err := json.Unmarshal(scanner.Bytes(), &ev); err != nil {
		t.Fatalf("unmarshal %q: %v", scanner.Text(), err)
	}

	if ev.OffsetNs < int64(900*time.Millisecond) {
		t.Errorf("offset_ns = %d, want >= ~1s (measured from the given start, not time.Now() inside watchPodReady)", ev.OffsetNs)
	}
}

func mustCreate(ctx context.Context, t *testing.T, pods interface {
	Create(context.Context, *corev1.Pod, metav1.CreateOptions) (*corev1.Pod, error)
}, pod *corev1.Pod) {
	t.Helper()
	if _, err := pods.Create(ctx, pod, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create pod %q: %v", pod.Name, err)
	}
}

func mustUpdate(ctx context.Context, t *testing.T, pods interface {
	Update(context.Context, *corev1.Pod, metav1.UpdateOptions) (*corev1.Pod, error)
}, pod *corev1.Pod) {
	t.Helper()
	if _, err := pods.Update(ctx, pod, metav1.UpdateOptions{}); err != nil {
		t.Fatalf("update pod %q: %v", pod.Name, err)
	}
}
