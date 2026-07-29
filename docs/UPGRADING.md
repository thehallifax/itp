# Upgrading ITP

For customer-profile deployments:

```sh
git pull --ff-only
./itp profile validate example-school
./itp profile restart example-school
./itp profile status example-school
```

Repeat for each local profile. Runtime and credentials are ignored, so routine
generation does not block `git pull --ff-only`. See
`docs/deployment-profiles.md` for rollback and profile-volume backup guidance.

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
