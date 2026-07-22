# Dependency Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate development and test dependencies from runtime while preserving every existing runtime dependency and all local changes.

**Architecture:** `project.dependencies` remains the runtime manifest. `test` owns the test runner and fixtures; `dev` owns development tooling and includes `test` so the default local sync continues to include both. The Makefile explicitly requests `test` when running the suite.

**Tech Stack:** Python 3.10, uv dependency groups, TOML, GNU Make.

## Global Constraints

- Do not remove dependencies merely because they appear unused.
- Do not create a `prod` group or move dependencies to one.
- Preserve the uncommitted additions of Guardian, RLS, PostHog and Rules.
- Do not modify the Dockerfile.

---

### Task 1: Separate dependency groups and regenerate the lockfile

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `Makefile`

**Interfaces:**
- Consumes: `uv sync` default `dev` group and `uv run --group test` group selection.
- Produces: a runtime-only `project.dependencies`, dedicated `test` group, and a `dev` group that includes `test`.

- [ ] **Step 1: Write the configuration assertion**

Define the expected group membership before editing: test owns `pytest`, `pytest-django`, `pytest-cov`, `factory-boy`, and `faker`; dev owns local Django diagnostics and tooling; runtime retains all remaining packages.

- [ ] **Step 2: Verify the pre-change assertion fails**

Run: `uv run --no-sync --group test python -c "import pytest"`

Expected: group selection cannot satisfy the request because `test` does not exist.

- [ ] **Step 3: Implement the minimal manifest changes**

Move the five test packages into `[dependency-groups].test`; move `django-debug-toolbar`, `django-extensions`, `django-cprofile-middleware`, `django-silk`, `django-zeal`, and `drf-api-logger` into `dev`; add `{ include-group = "test" }` to `dev`; and change the Make target to `uv run --group test pytest`.

- [ ] **Step 4: Regenerate and verify the lockfile**

Run: `UV_CACHE_DIR=/tmp/drf-base-api-project-uv-cache uv lock`

Then run: `UV_CACHE_DIR=/tmp/drf-base-api-project-uv-cache uv lock --check`

Expected: both commands exit 0 and `uv.lock` records the `dev` and `test` groups.

- [ ] **Step 5: Verify group behavior and project checks**

Run: `UV_CACHE_DIR=/tmp/drf-base-api-project-uv-cache uv sync --locked --group test`

Then run: `make test`

Expected: the test group resolves, and the test suite runs through the updated Make target.

- [ ] **Step 6: Commit**

Stage `pyproject.toml`, `uv.lock`, `Makefile`, and this plan, then commit with `build: separa dependências de dev e teste`.
