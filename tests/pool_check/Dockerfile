# One Linux device for scripts/pool-check: the node built from this tree and the Python package installed from its own packaging
# (pyproject.toml, hatchling), nothing copied by hand. Stages cache on their own inputs. The distribution is a parameter:
#   docker build --build-arg DISTRO=ubuntu:24.04 ...     (the default; debian:bookworm-slim, ubuntu:22.04 and the like work too)
# Python 3.13 comes from uv's standalone build so every distribution runs the same interpreter the tests need.
ARG DISTRO=ubuntu:24.04

FROM rust:1-slim-bookworm AS node
WORKDIR /w/app
COPY app/ ./
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/w/app/target \
    cargo build --release -p poolhouse-node && cp target/release/poolhouse-node /poolhouse-node

FROM ghcr.io/astral-sh/uv:0.8.22 AS uv

FROM ${DISTRO} AS device
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN uv venv /opt/venv --python 3.13
WORKDIR /opt/poolhouse
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY contracts ./contracts
COPY patches ./patches
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv pip install . pytest
COPY scripts ./scripts
COPY --from=node /poolhouse-node /usr/local/bin/poolhouse-node
ENV PYTHONUNBUFFERED=1 POOLHOUSE_NODE_LAN=off
