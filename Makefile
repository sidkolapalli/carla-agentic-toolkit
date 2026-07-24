.PHONY: check format lint radon rust-check rust-clippy rust-fmt test type

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .
	cargo fmt --manifest-path sandbox-runner/Cargo.toml

type:
	uv run ty check .

radon:
	uv run python scripts/check_radon.py src tests

rust-fmt:
	cargo fmt --manifest-path sandbox-runner/Cargo.toml -- --check

rust-clippy:
	cargo clippy --manifest-path sandbox-runner/Cargo.toml -- -D warnings

rust-check: rust-fmt rust-clippy

check: lint type radon rust-check test
