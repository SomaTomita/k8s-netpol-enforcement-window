// Command victim is the target of measurement: it opens a TCP listener and
// records t_ready candidate C — the instant its own listener starts
// accepting connections, which is the definition conceptually closest to
// "the workload can actually receive traffic" (see docs/methodology.md's
// "t_ready: three candidate definitions" section). It then accepts and
// immediately closes every connection, which is all the prober needs to
// classify Allowed vs Blocked.
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
)

// tReadyMethodC identifies candidate C (victim self-report) as the t_ready
// definition this binary implements. See docs/methodology.md.
const tReadyMethodC = "C"

// readyEvent is this victim's own observation of when it became ready to
// receive traffic. OffsetNs is time.Since(start) at the moment Listen
// returned successfully. start is process start by default, or the shared
// -run-epoch instant when that flag is set (see docs/methodology.md's
// Clocks section), matching cmd/prober and cmd/watcher.
type readyEvent struct {
	OffsetNs     int64  `json:"offset_ns"`
	TReadyMethod string `json:"t_ready_method"`
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	err := run(ctx, os.Args[1:], os.Stdout)
	stop()
	if err != nil {
		log.Fatal(err)
	}
}

// run parses flags, opens the listener, emits this process's readyEvent the
// instant the listener is accepting connections, then serves connections
// until ctx is done, at which point it closes the listener and returns nil.
func run(ctx context.Context, args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("victim", flag.ContinueOnError)
	addr := fs.String("addr", ":8080", "address to listen on")
	runEpoch := fs.Int64("run-epoch", 0, "unix nanoseconds; when set, offset_ns is measured from this shared instant instead of process start, so this binary's stream becomes combinable with another process's (see docs/methodology.md's Clocks section)")
	if err := fs.Parse(args); err != nil {
		return err
	}

	start := time.Now()
	if *runEpoch != 0 {
		start = time.Unix(0, *runEpoch)
	}

	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		return fmt.Errorf("listen %s: %w", *addr, err)
	}

	go func() {
		<-ctx.Done()
		_ = ln.Close()
	}()

	// The instant Listen returns successfully, this socket is accepting
	// connections at the OS level — this is candidate C's t_ready: the
	// moment this workload can actually receive traffic, not when the
	// control plane or an external watcher later learns about it.
	offset := time.Since(start)

	enc := json.NewEncoder(stdout)
	if err := enc.Encode(readyEvent{OffsetNs: offset.Nanoseconds(), TReadyMethod: tReadyMethodC}); err != nil {
		return fmt.Errorf("encode ready event: %w", err)
	}

	return acceptLoop(ln)
}

// acceptLoop accepts and immediately closes every connection, forever,
// until ln is closed (which run does when ctx is done). A closed listener
// is treated as a clean shutdown, not a failure.
func acceptLoop(ln net.Listener) error {
	for {
		conn, err := ln.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return nil
			}
			return fmt.Errorf("accept: %w", err)
		}
		_ = conn.Close()
	}
}
