#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose config --quiet
docker compose build --pull
docker compose up -d --remove-orphans
docker compose run --rm collector python -m collectors validate
docker compose ps
