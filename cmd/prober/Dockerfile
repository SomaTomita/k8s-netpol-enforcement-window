# Builds cmd/prober as a static binary for in-cluster use. Loaded directly
# into kind via `kind load docker-image` — no registry involved, since the
# binary only ever needs to exist inside the disposable kind cluster it was
# built for (see docs/adr/0002-single-node-kind.md).
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/prober ./cmd/prober
COPY internal ./internal
RUN CGO_ENABLED=0 go build -o /out/prober ./cmd/prober

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/prober /prober
ENTRYPOINT ["/prober"]
