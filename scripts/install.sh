#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
command -v docker >/dev/null 2>&1 || { echo "Docker is required" >&2; exit 1; }
docker compose version >/dev/null
[ -f .env ] || cp .env.example .env
[ -f discovery/config.yml ] || cp discovery/config.example.yml discovery/config.yml
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose run --rm collector python -m collectors validate
echo "ITP installation completed"
