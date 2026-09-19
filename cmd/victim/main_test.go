package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"strconv"
	"testing"
	"time"
)

func TestRun_RunEpochAnchorsOffset(t *testing.T) {
	epoch := time.Now().Add(-500 * time.Millisecond)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	pr, pw := io.Pipe()
	args := []string{
		"-addr", "127.0.0.1:0",
		"-run-epoch", strconv.FormatInt(epoch.UnixNano(), 10),
	}

	done := make(chan error, 1)
	go func() { done <- run(ctx, args, pw) }()

	ev := readReadyEvent(t, pr)
	if ev.OffsetNs < int64(400*time.Millisecond) {
		t.Errorf("offset_ns = %d, want >= ~500ms (measured from -run-epoch, not process start)", ev.OffsetNs)
	}
	if ev.TReadyMethod != tReadyMethodC {
		t.Errorf("t_ready_method = %q, want %q", ev.TReadyMethod, tReadyMethodC)
	}

	cancel()
	if err := <-done; err != nil {
		t.Fatalf("run() error: %v", err)
	}
}

func TestRun_DefaultsToProcessStartWithoutRunEpoch(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	pr, pw := io.Pipe()
	args := []string{"-addr", "127.0.0.1:0"}

	done := make(chan error, 1)
	go func() { done <- run(ctx, args, pw) }()

	ev := readReadyEvent(t, pr)
	if ev.OffsetNs > int64(200*time.Millisecond) {
		t.Errorf("offset_ns = %d, want a small offset from process start (no -run-epoch given)", ev.OffsetNs)
	}

	cancel()
	if err := <-done; err != nil {
		t.Fatalf("run() error: %v", err)
	}
}

func TestRun_StopsCleanlyOnContextCancel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())

	pr, pw := io.Pipe()
	args := []string{"-addr", "127.0.0.1:0"}

	done := make(chan error, 1)
	go func() { done <- run(ctx, args, pw) }()
	readReadyEvent(t, pr)

	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("run() error on shutdown: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("run() did not return within 2s of context cancellation")
	}
}

func TestAcceptLoop_ServesAndClosesConnections(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}

	done := make(chan error, 1)
	go func() { done <- acceptLoop(ln) }()

	conn, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	buf := make([]byte, 1)
	if _, err := conn.Read(buf); !errors.Is(err, io.EOF) {
		t.Errorf("Read() error = %v, want io.EOF (server should close immediately)", err)
	}
	_ = conn.Close()

	if err := ln.Close(); err != nil {
		t.Fatalf("close listener: %v", err)
	}
	if err := <-done; err != nil {
		t.Errorf("acceptLoop() error after listener closed = %v, want nil", err)
	}
}

func readReadyEvent(t *testing.T, r io.Reader) readyEvent {
	t.Helper()
	scanner := bufio.NewScanner(r)
	if !scanner.Scan() {
		t.Fatal("expected a ready event line")
	}
	var ev readyEvent
	if err := json.Unmarshal(scanner.Bytes(), &ev); err != nil {
		t.Fatalf("unmarshal %q: %v", scanner.Text(), err)
	}
	return ev
}
