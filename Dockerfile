# syntax=docker/dockerfile:1

FROM rust:1.89-slim-bookworm AS sandbox
WORKDIR /build
COPY sandbox-runner/ ./
RUN cargo build --locked --release

FROM python:3.12-slim-bookworm
ARG CARLA_VERSION=0.9.16
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /usr/local/bin/

ENV CARLA_MCP_OUTPUT_DIR=/output \
    CARLA_MCP_SANDBOX=/usr/local/bin/carla-mcp-sandbox \
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
COPY --from=sandbox /build/target/release/carla-mcp-sandbox /usr/local/bin/

RUN useradd --create-home --uid 10001 carla-mcp \
    && mkdir /output \
    && chown carla-mcp:carla-mcp /output
USER carla-mcp

CMD ["carla-mcp"]
