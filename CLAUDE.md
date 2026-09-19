# netpol-window

Measurement harness for the unprotected window between a Kubernetes Pod
becoming Ready and its NetworkPolicy actually being enforced by the CNI.
This is a master's thesis research artifact, from a master's programme in
cybersecurity, not a product. Read [`docs/methodology.md`](docs/methodology.md)
before touching any measurement code — it defines what the numbers mean.

## What this measures

```
window = t_blocked - t_ready
```

- `t_ready`: when a Pod became able to receive traffic
- `t_blocked`: when traffic that should be blocked by a NetworkPolicy was
  actually first blocked
- `window > 0` means the Pod ran unprotected for that long

The gap is supported by three independent sources (see
`docs/methodology.md` and `docs/related-work.md` for verbatim quotes):
Kubernetes' own docs acknowledging the window exists, CVE-2024-7598 (the
teardown-side analog, which *is* a CVE), and Budigiri et al. (IEEE TNSM
2025) assuming a readiness budget without measuring the window directly.
This project is the direct empirical follow-up to that limitation.

## Repository layout

| Path | Purpose |
|---|---|
| `internal/probe/` | Pure Go logic: outcome classification, sustained-window detection. No network I/O — table-driven tested. |
| `internal/record/` | JSONL schema — the contract between Go and Python. |
| `cmd/prober/` | In-cluster binary: dials the victim repeatedly, emits raw observations. |
| `cmd/watcher/` | Out-of-cluster binary: watches the API for Pod-Ready, timestamps on receipt (not from the API's own second-precision timestamp). |
| `deploy/` | kind cluster config, CNI Helm values, victim workload, baseline NetworkPolicy. |
| `src/npw/` | Python: experiment matrix expansion, window/statistics analysis. |
| `experiments/` | Experiment matrices and pre-registration docs — data, not code. |
| `docs/methodology.md` | The measurement definition. Read first. |
| `docs/adr/` | Decisions and why (e.g. why single-node kind, why Go for the hot path). |
| `docs/related-work.md` | Literature basis — verbatim quotes only, no paraphrasing presented as quotes. |

## Ground rules

- **Never fabricate measurement data.** If a live cluster run hasn't
  happened, results are absent, not estimated or invented. This applies to
  ADR 0003 (t_ready definition choice) and any pre-registration numbers —
  they get filled in only after a real pilot run.
- **Citations must be checked against the actual source.** No paraphrases
  presented as verbatim quotes. `docs/related-work.md` and
  `docs/methodology.md` are the canonical citation record.
- **`metav1.Time` is second-precision (RFC3339).** Never use the
  Kubernetes API's own `lastTransitionTime` for millisecond-scale timing —
  see `cmd/watcher/main.go` for why we timestamp on local receipt instead.
- **Outcome classification (Blocked/Allowed/Error) must stay CNI-agnostic.**
  eBPF-based CNIs drop packets (timeout); iptables-based CNIs REJECT
  (connection refused). The classifier in `internal/probe/outcome.go` is
  the single place this gets normalized — don't duplicate that logic
  elsewhere.
- **Single-node kind by design** (ADR 0002). Multi-node needs clock sync
  (PTP), which is out of scope; don't add multi-node support without
  revisiting that ADR.
- **`data/raw/` is immutable.** Derived data goes to `data/interim/` or
  `data/processed/`, never overwrite raw.

## Workflow

- One GitHub issue per unit of work, one PR per issue, squash merge onto
  `main`. Branch naming: `<type>/<issue-number>-<short-desc>`.
- TDD: failing test first, confirm the failure, minimal implementation,
  confirm the pass, commit.
- Commit messages: `<type>: <description> (#<issue-number>)`. No AI
  attribution (`Co-Authored-By`, `Generated with...`) in commits or PRs —
  ever.
- PR body always starts with `Closes #<N>`.

## Testing

```bash
go test ./... -race     # Go unit tests
go vet ./...
uv run pytest -v        # Python unit tests
uv run ruff check .
task smoke              # kind cluster + one end-to-end pass (needs Docker; see #10)
```

## GenAI disclosure

This repository uses AI coding assistance under the university's thesis Code of
Conduct. See [`AI-USAGE.md`](AI-USAGE.md) — keep it current as scope
changes. Local Claude Code skill installs (`.claude/`, `.agents/`,
`skills-lock.json`) are gitignored — they are development tooling, not
part of the research artifact.
