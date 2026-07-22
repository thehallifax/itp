$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
git pull --ff-only
docker compose config --quiet
docker compose build --pull
docker compose up -d --remove-orphans
docker compose run --rm collector python -m collectors validate
docker compose ps
