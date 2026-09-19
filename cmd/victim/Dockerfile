# Builds cmd/victim as a static binary for in-cluster use. Loaded directly
# into kind via `kind load docker-image`, same as cmd/prober's Dockerfile.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/victim ./cmd/victim
RUN CGO_ENABLED=0 go build -o /out/victim ./cmd/victim

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/victim /victim
ENTRYPOINT ["/victim"]
