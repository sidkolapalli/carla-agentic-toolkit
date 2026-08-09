# syntax=docker/dockerfile:1

FROM rust:1.89-slim-bookworm AS sandbox
WORKDIR /build
COPY sandbox-runner/ ./
RUN cargo build --locked --release

FROM python:3.12-slim-bookworm
ARG CARLA_VERSION=0.9.16
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /usr/local/bin/

ENV CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR=/output \
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
# Perl is unused at runtime, and Debian has no patched package for its current CVEs.
RUN apt-get purge -y --allow-remove-essential perl-base
COPY --from=sandbox /build/target/release/carla-agentic-toolkit-sandbox /usr/local/bin/

RUN useradd --create-home --uid 10001 carla-agentic-toolkit \
    && mkdir /output \
    && chown carla-agentic-toolkit:carla-agentic-toolkit /output
USER carla-agentic-toolkit

CMD ["carla-agentic-toolkit"]
