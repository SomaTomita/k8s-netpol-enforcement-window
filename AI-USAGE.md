# GenAI Usage Disclosure

Disclosure under the university's Master's Thesis Code of Conduct.
**Omitting this document results in "not taken" status for the thesis.**
Update it as implementation progresses. It is tracked in git and published
with the artifact: `README.md` and `CLAUDE.md` both link to it, and a
disclosure a reader cannot open is not a disclosure.

## Tools used

| Tool | Model / version | Period |
|---|---|---|
| Claude Code | Claude Sonnet 5 (`claude-sonnet-5`) | 2026-08-08 – present |

## Scope of use

| Area | Extent of use | How verified |
|---|---|---|
| `internal/probe/`, `internal/record/`, `internal/k8swatch/` (Go) | Generated, then reviewed and covered by table-driven tests | Unit tests (`go test -race`), `go vet`, `golangci-lint` |
| `cmd/prober/`, `cmd/watcher/`, `cmd/victim/` (Go binaries) | Generated, then reviewed, tested (unit tests with fake Kubernetes clientsets), and run against a live kind+Cilium cluster | Unit tests + repeated live pilot runs (`scripts/collect.sh`) |
| `src/npw/` (Python: matrix expansion, analysis, statistics) | Generated, then reviewed and covered by tests | Unit tests (`pytest`), `ruff` |
| `deploy/` (Kubernetes manifests: kind config, CNI values, RBAC, workloads) | Generated, then reviewed | Applied to and verified against a live kind+Cilium cluster |
| `scripts/*.sh`, `scripts/*.py` (orchestration) | Generated, then reviewed | Syntax-checked (`bash -n`) and run end-to-end against a live cluster |
| Measurement methodology (`docs/methodology.md`, `docs/adr/*.md`, `docs/threats-to-validity.md`) | Drafted with AI assistance; specific definitions and decisions (e.g. ADR 0003's choice of `t_ready` candidate) grounded in live pilot data collected during the same sessions | Cross-checked against pilot measurement output; citations checked against primary sources (verbatim quotes verified, not paraphrased) |
| `docs/related-work.md` | Drafted with AI assistance | Every verbatim quotation checked directly against its cited source; a follow-up review pass found and corrected 3 citation errors in a related document (`experiments/exp1-window/preregistration.md`) |
| Experiment design / statistical method choices (bootstrap CI, sustained-`k` threshold, pre-registration parameters) | Discussed with AI assistance; frozen choices and their justification recorded in `experiments/exp1-window/preregistration.md` | Cross-checked against advisor feedback (pending); reasoning for each frozen parameter is written out in that document for independent review |
| Experiment 1b design (`policy_at` arms, enforcement latency `L`, host-sleep discard rule) and its pre-registration | Discussed and drafted with AI assistance; hypotheses H1–H3 and all frozen parameters written before the pilot | Cross-checked against Experiment 1's recorded data (`docs/results/exp1.md`) and the positive control; the frozen text is in `experiments/exp1b-ordering/preregistration.md` for independent review |
| Code review before merging | An automated multi-agent review pass (correctness + cleanup angles, independent verification) was run against every substantive pull request before merging; findings were fixed, not just noted | GitHub PR history (inline review comments) for this repository |
| `docs/architecture/` diagrams (generated via `mingrammer/diagrams`) | Generator scripts written with AI assistance | Visually reviewed against `internal/probe`, `internal/record`, and `cmd/watcher`/`cmd/prober` to confirm the depicted components match the implementation |
| Experiment 1b (`policy_at` arms, enforcement latency, host-sleep detector) and its pre-registration | Generated and drafted with AI assistance under a plan reviewed before execution; each task reviewed by a separate agent pass, with findings fixed rather than noted | Unit tests, live three-arm smoke, and a 180-trial run whose checksums are committed; every figure in `docs/results/exp1b.md` traces to `data/processed/exp1b/`, and the hypotheses were frozen in `experiments/exp1b-ordering/preregistration.md` before the pilot |
| Upstream report to `kubernetes/website` | Drafted with AI assistance; the decision to file, and the decision *not* to file Experiment 1's weaker result, are the author's | Filed as kubernetes/website#57642; the reasoning for both decisions is recorded in `docs/results/exp1.md` and `exp1b.md` |
| Thesis text (chapters, narrative prose for submission) | Not yet started | (fill in once drafting begins) |

## Not used for

- Final acceptance of what a measurement result means for the research
  question — e.g., whether a given `window` value counts as evidence of an
  unprotected window, and what that implies, is the author's judgment,
  not an AI-generated conclusion.
- Deciding which findings from a code review are worth fixing versus
  acceptable trade-offs — every reported finding was reviewed by the
  author before a fix was accepted.
- (extend as needed)
