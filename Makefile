.PHONY: check format lint radon rust-build rust-check rust-clippy rust-fmt rust-test test type

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
	! uv run radon cc -n B -s src tests | grep .
	! uv run radon mi -n B -m -s src tests | grep .

rust-fmt:
	cargo fmt --manifest-path sandbox-runner/Cargo.toml -- --check

rust-clippy:
	cargo clippy --locked --manifest-path sandbox-runner/Cargo.toml -- -D warnings

rust-build:
	cargo build --locked --manifest-path sandbox-runner/Cargo.toml

rust-test:
	cargo test --locked --manifest-path sandbox-runner/Cargo.toml

rust-check: rust-fmt rust-clippy rust-build rust-test

check: lint type radon rust-check test
