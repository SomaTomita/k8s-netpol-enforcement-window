# netpol-window

Measures how long a new Kubernetes Pod is reachable before its
NetworkPolicy takes effect, on Cilium, Calico and Antrea.

```
window = t_blocked - t_ready
```

## Results

Single-node kind cluster, 30 trials per condition.

**Experiment 1 (270 trials).** Policy applied before the Pod starts, at
1, 10 and 60 Pod creations per minute. Traffic was already blocked when
the Pod became Ready in every trial, so no window was observed. The
harness can detect windows down to about 6-9 ms.

**Experiment 1b (180 trials).** Policy applied after the Pod is Ready,
to measure how long each CNI takes to enforce it.

| CNI | Median enforcement latency | 95% CI |
|---|---|---|
| Antrea 2.7.0 | 51.1 ms | 50.5-52.7 |
| Calico v3.32.2 | 55.9 ms | 55.2-56.4 |
| Cilium 1.20.0 | 115.9 ms | 104.4-137.0 |

When the policy was created at the same time as the Pod, the policy was
in place before the Pod became Ready in all 90 trials.

## Usage

Requires Docker, kind, kubectl, Helm, Go, uv and Task.

```bash
task setup                      # check dependencies
task cluster:up CNI=cilium      # or calico / antrea
task exp1b:run  CNI=cilium
task exp1b:analyze
task cluster:down CNI=cilium
```

`task --list` shows the rest (`exp1:run`, `smoke`, `test`, `lint`).

Raw data is in `data/raw/`, with a `checksums.sha256` per trial.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
