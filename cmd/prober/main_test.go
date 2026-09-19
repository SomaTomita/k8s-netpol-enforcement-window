package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"net"
	"strconv"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	k8sfake "k8s.io/client-go/kubernetes/fake"
)

// unreachableAddr returns a host:port that refuses connections immediately:
// a port nothing is listening on, freed right before returning it.
func unreachableAddr(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	addr := l.Addr().String()
	if err := l.Close(); err != nil {
		t.Fatalf("close listener: %v", err)
	}
	return addr
}

func TestRun_RunEpochAnchorsOffsets(t *testing.T) {
	epoch := time.Now().Add(-500 * time.Millisecond)

	var out bytes.Buffer
	args := []string{
		"-target", unreachableAddr(t),
		"-interval", "10ms",
		"-timeout", "50ms",
		"-duration", "20ms",
		"-run-epoch", strconv.FormatInt(epoch.UnixNano(), 10),
	}
	if err := run(args, &out); err != nil {
		t.Fatalf("run() error: %v", err)
	}

	obs := firstObservation(t, &out)
	if obs.OffsetNs < int64(400*time.Millisecond) {
		t.Errorf("offset_ns = %d, want >= ~500ms (measured from -run-epoch, not process start)", obs.OffsetNs)
	}
}

func TestRun_DefaultsToProcessStartWithoutRunEpoch(t *testing.T) {
	var out bytes.Buffer
	args := []string{
		"-target", unreachableAddr(t),
		"-interval", "10ms",
		"-timeout", "50ms",
		"-duration", "20ms",
	}
	if err := run(args, &out); err != nil {
		t.Fatalf("run() error: %v", err)
	}

	obs := firstObservation(t, &out)
	if obs.OffsetNs > int64(200*time.Millisecond) {
		t.Errorf("offset_ns = %d, want a small offset from process start (no -run-epoch given)", obs.OffsetNs)
	}
}

func TestRun_RequiresTargetOrTargetSelector(t *testing.T) {
	var out bytes.Buffer
	err := run([]string{"-duration", "1ms"}, &out)
	if err == nil {
		t.Fatal("run() error = nil, want an error when neither -target nor -target-selector is set")
	}
}

func TestRun_TargetAndTargetSelectorMutuallyExclusive(t *testing.T) {
	var out bytes.Buffer
	err := run([]string{
		"-target", unreachableAddr(t),
		"-target-selector", "role=victim",
		"-target-port", "8080",
	}, &out)
	if err == nil {
		t.Fatal("run() error = nil, want an error when both -target and -target-selector are set")
	}
}

func TestRun_TargetSelectorRequiresTargetPort(t *testing.T) {
	var out bytes.Buffer
	err := run([]string{"-target-selector", "role=victim"}, &out)
	if err == nil {
		t.Fatal("run() error = nil, want an error when -target-selector is set without -target-port")
	}
}

func TestRun_TargetSelectorRequiresTargetNamespace(t *testing.T) {
	var out bytes.Buffer
	err := run([]string{"-target-selector", "role=victim", "-target-port", "8080"}, &out)
	if err == nil {
		t.Fatal("run() error = nil, want an error when -target-selector is set without -target-namespace (an empty namespace would watch cluster-wide, not fail fast)")
	}
}

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

func TestWaitForTargetIP_ReturnsIPOnceAssigned(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	type result struct {
		ip  string
		err error
	}
	done := make(chan result, 1)
	go func() {
		ip, err := waitForTargetIP(ctx, clientset, "target", "role=victim")
		done <- result{ip, err}
	}()

	// Give waitForTargetIP time to establish its watch before the Pod is
	// created, mirroring cmd/watcher's own test (the watch must be live
	// before the event fires, or a plain Watch() call — not an Informer —
	// won't see it).
	time.Sleep(50 * time.Millisecond)

	pods := clientset.CoreV1().Pods("target")
	// Created without an IP yet, matching a real Pod between scheduling
	// and network sandbox setup.
	if _, err := pods.Create(context.Background(), victimPod("victim-1", ""), metav1.CreateOptions{}); err != nil {
		t.Fatalf("create pod: %v", err)
	}

	time.Sleep(50 * time.Millisecond)
	if _, err := pods.Update(context.Background(), victimPod("victim-1", "10.244.0.5"), metav1.UpdateOptions{}); err != nil {
		t.Fatalf("update pod: %v", err)
	}

	select {
	case r := <-done:
		if r.err != nil {
			t.Fatalf("waitForTargetIP() error: %v", r.err)
		}
		if r.ip != "10.244.0.5" {
			t.Errorf("waitForTargetIP() = %q, want %q", r.ip, "10.244.0.5")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("waitForTargetIP() did not return within 2s of the IP being assigned")
	}
}

// Deleted-event filtering (a Deleted event's last-known status must never
// be mistaken for a live observation) is implemented and regression-tested
// in internal/k8swatch, which waitForTargetIP delegates to entirely — see
// TestPodEvents_SkipsDeletedEvents there.

func TestWaitForTargetIP_ReturnsContextErrorOnCancel(t *testing.T) {
	clientset := k8sfake.NewSimpleClientset()
	ctx, cancel := context.WithCancel(context.Background())

	done := make(chan error, 1)
	go func() {
		_, err := waitForTargetIP(ctx, clientset, "target", "role=victim")
		done <- err
	}()

	time.Sleep(50 * time.Millisecond)
	cancel()

	select {
	case err := <-done:
		if err == nil {
			t.Error("waitForTargetIP() error = nil, want context.Canceled after cancellation")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("waitForTargetIP() did not return within 2s of context cancellation")
	}
}

func firstObservation(t *testing.T, out *bytes.Buffer) observation {
	t.Helper()
	scanner := bufio.NewScanner(out)
	if !scanner.Scan() {
		t.Fatal("expected at least one observation line")
	}
	var obs observation
	if err := json.Unmarshal(scanner.Bytes(), &obs); err != nil {
		t.Fatalf("unmarshal %q: %v", scanner.Text(), err)
	}
	return obs
}
