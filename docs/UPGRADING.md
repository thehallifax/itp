# Upgrading ITP

Back up `.env`, `secrets/`, `runtime/inventory/`, and Docker volumes before a production
upgrade. Runtime files are intentionally not version-controlled.

```sh
./scripts/update.sh
```

The equivalent workflow is:

```sh
git pull --ff-only
docker compose config --quiet
docker compose build --pull
docker compose up -d --remove-orphans
docker compose run --rm collector python -m collectors validate
```

Review `VERSION`, `SCHEMA_VERSION`, release notes, configuration-template differences,
and collector permissions before upgrading. Schema version 1 uses additive dual writes
and requires no database migration.
