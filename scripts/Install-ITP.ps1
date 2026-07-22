$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is required" }
docker compose version | Out-Null
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path discovery/config.yml)) { Copy-Item discovery/config.example.yml discovery/config.yml }
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose run --rm collector python -m collectors validate
Write-Host "ITP installation completed"
