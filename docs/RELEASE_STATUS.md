# ATOS-X Release Status

Date: 2026-08-09
Target branch: `main`
Release line: `v1.0.0`

## Scope lock

ATOS-X is scoped to Binance Global USDⓈ-M Futures. The release baseline must not introduce execution adapters for other exchanges.

## Release gate

The repository contains a GitHub Actions quality gate at `.github/workflows/quality-gate.yml` with the following checks:

1. Python 3.11 environment setup.
2. Editable backend installation with development dependencies.
3. `compileall` validation for `app` and `tests`.
4. Import smoke validation for the AI gate and operational dashboard view.
5. Full `pytest` unit/regression suite with `ATOS_TEST_MODE=1`.

The workflow runs on pushes to `main`, pull requests targeting `main`, and manual dispatch.

## Backend package

`backend/pyproject.toml` declares ATOS-X version `1.0.0`, Python `>=3.11`, FastAPI/runtime dependencies, Binance integration dependencies, data/scientific dependencies, observability dependencies, and a dedicated development toolchain.

`backend/README.md` documents the package installation and test command required by the build metadata.

## Final acceptance rule

A release is considered technically green only when the GitHub Actions quality gate completes successfully on the release commit. A commit existing on `main` is not by itself evidence that the automated test suite passed.

## Operational safety

Live trading must remain disabled until runtime credentials, Binance Futures permissions, leverage/risk limits, position sizing, persistence/recovery, and testnet/dry-run checks are explicitly validated in the deployment environment.

## Current release objective

This file is the final release marker for the current repository hardening cycle and intentionally triggers the `main` quality-gate workflow through a repository push.
