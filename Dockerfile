# syntax=docker/dockerfile:1

FROM rust:1.89-slim-bookworm AS sandbox
WORKDIR /build
COPY sandbox-runner/ ./
RUN cargo build --locked --release

FROM python:3.12-slim-trixie AS runtime-build
ARG CARLA_VERSION=0.9.16
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /usr/local/bin/

# Base-image refreshes can lag Debian security updates.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

ENV CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR=/output \
    CARLA_AGENTIC_TOOLKIT_STATE_DIR=/tmp/carla-trusted-state \
    CARLA_AGENTIC_TOOLKIT_SANDBOX=/usr/local/bin/carla-agentic-toolkit-sandbox \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --locked --no-dev \
    && uv pip install --python .venv "carla==$CARLA_VERSION"
COPY --from=sandbox /build/target/release/carla-agentic-toolkit-sandbox /usr/local/bin/

COPY scripts/assemble_runtime.py /assemble_runtime.py
RUN python /assemble_runtime.py && mkdir /output

FROM gcr.io/distroless/cc-debian13:nonroot
COPY --from=runtime-build /opt/runtime-root/ /
COPY --from=runtime-build --chown=10001:10001 /output/ /output/
ENV CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR=/output \
    CARLA_AGENTIC_TOOLKIT_STATE_DIR=/tmp/carla-trusted-state \
    CARLA_AGENTIC_TOOLKIT_SANDBOX=/usr/local/bin/carla-agentic-toolkit-sandbox \
    PATH=/app/.venv/bin:/usr/local/bin \
    LD_LIBRARY_PATH=/usr/local/lib \
    HOME=/tmp/carla-home \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001

CMD ["carla-agentic-toolkit"]
