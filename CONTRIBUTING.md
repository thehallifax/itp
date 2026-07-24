# Contributing to ITP

ITP is Alpha software. Keep changes focused, deterministic, and compatible with
the canonical platform contracts.

## Local setup

ITP supports Python 3.9 or later.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest
```

For stack-level validation:

```sh
cp .env.example .env
cp discovery/config.example.yml discovery/config.yml
docker compose config --quiet
```

Never use production credentials or customer evidence in development fixtures.

## Branches and pull requests

Use a short descriptive branch such as `feature/name`, `fix/name`, or
`docs/name`. Keep each pull request scoped to one milestone or correction.

Before opening a pull request:

```sh
python -m pytest
git diff --check
```

Describe behavior changes, tests, configuration effects, and any remaining
limitations. Do not include unrelated formatting or generated runtime state.

## Architecture expectations

A collector owns authentication, discovery, collection, and adaptation.
Collector output must map into canonical assets, sites, signals, measurements,
and virtualisation objects. Vendor-specific conditions must not leak into
Operations rules, Service Health, or generic dashboards.

Operational behavior must be deterministic and explainable. Do not introduce
LLM decisions, probabilistic state, or hidden service-impact inference.

Tests must run offline without secrets, live infrastructure, Docker services,
or external collectors. Use sanitized deterministic fixtures, assert stable
ordering, and keep generated evidence outside profile runtime directories.
