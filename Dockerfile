# syntax=docker/dockerfile:1.7

# ----------------------- builder stage -----------------------
FROM python:3.13-slim-bookworm AS builder

WORKDIR /src

COPY pyproject.toml ./
COPY pipeline_status/ ./pipeline_status/

RUN pip install --no-cache-dir --prefix=/install .

# ----------------------- runtime stage -----------------------
FROM python:3.13-slim-bookworm

ARG VERSION="0.1.0"
ARG REVISION=""
ARG CREATED=""

LABEL org.opencontainers.image.title="pipeline-status"
LABEL org.opencontainers.image.description="CLI inspector for the master-replicator multi-agent pipeline state directory."
LABEL org.opencontainers.image.source="https://github.com/asnapper/master-replicator"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${REVISION}"
LABEL org.opencontainers.image.created="${CREATED}"

RUN groupadd --system --gid 65532 pipeline \
 && useradd --system --create-home --uid 65532 --gid 65532 --shell /bin/bash pipeline

COPY --from=builder /install /usr/local

WORKDIR /repo

USER pipeline:pipeline

ENTRYPOINT ["pipeline-status"]
CMD []
