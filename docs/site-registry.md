# Canonical Site Registry

The Canonical Site Registry resolves collector-specific site labels before asset
fusion. Infrastructure State, operational findings, dashboard summaries, and
future APIs therefore use stable IDs such as `site:example-campus` while still
displaying a friendly name.

## Configuration

Define sites in `config/sites.yml`:

```yaml
sites:
  - id: example-campus
    display_name: Example College, Campus
    aliases:
      - EC
      - Example College
    timezone: Australia/Perth   # optional future metadata
```

IDs are explicit and stable. Changing an alias does not change identity. Optional
`timezone`, `region`, `address`, and `notes` metadata is preserved without being
required.

Tracked registries contain only anonymised defaults. For a legacy/root
deployment, create a complete local replacement and select it through the
existing Compose path:

```sh
cp config/sites.yml config/sites.local.yml
# Edit display_name and aliases in config/sites.local.yml.
# Set ITP_SITES_CONFIG=./config/sites.local.yml in the ignored .env.
```

`config/*.local.yml` is ignored by Git. Do not add customer display names or
aliases to tracked examples.

Profiles use the same convention:

```sh
cp profiles/<profile>/sites.yml profiles/<profile>/sites.local.yml
# Edit only the ignored sites.local.yml.
./itp profile validate <profile>
```

Profile activation automatically selects `sites.local.yml` when it exists next
to the tracked `sites.yml`. The selected file is exposed as
`ITP_SITES_CONFIG` and mounted as the single active registry. This is a full
replacement, not a merge, so resolution is deterministic and customer aliases
cannot leak from one profile into another. Demo mode explicitly uses the
tracked anonymised registry under its isolated runtime.

## Resolution strategy

Resolution normalizes Unicode, case, whitespace, apostrophes, common punctuation,
and configured abbreviations. It performs exact lookup after normalization and
never uses fuzzy matching. An explicit configured `site_id` wins. An alias that
maps to multiple sites is ambiguous and is not resolved.

Every asset contains a site object with `site_id`, `display_name`, and all original
`source_values`. Expected alias differences are not fusion conflicts. Unknown or
ambiguous values remain validation findings rather than operational warnings.

## Runtime estate

`python -m collectors sites generate` writes:

- `runtime/sites/sites.json`
- `runtime/sites/sites.csv`
- `runtime/dashboard/site-summary.json`

Counts are derived from canonical assets and operational outputs. Site JSON is
naturally suitable for future `GET /api/sites` and `GET /api/sites/{id}` handlers;
OPS-005 does not implement an API.

Validation reports duplicate aliases, cross-site alias ambiguity, unused aliases,
and assets referencing unknown sites in stable order.

## Dashboard filtering

Infrastructure Overview exposes a `$site` variable whose values are canonical
site IDs. The generated dashboard refreshes its options from canonical state.
Vendor dashboards keep their existing vendor-specific variables.

Operations Wallboard uses the same convention. All Sites has the internal value
`all`; a selected site uses its canonical `site_id`. Panels without authoritative
site ownership, such as collector health, remain safely estate-wide.

## Extension guide

Future adapters continue emitting their original site value. They must not embed
alias logic. Add new canonical sites and exact aliases to `config/sites.yml`, then
restart the collector and regenerate state. This keeps collector behavior
independent from estate naming policy.

For deployment-specific metadata, edit the ignored local registry instead of
the tracked default. Regenerate every projection after changing it:

```sh
docker compose -p itp-<profile> exec collector \
  python -m collectors --profile <profile> infrastructure generate
./itp profile operations <profile>
./itp profile services <profile>
./itp profile wallboard <profile>
./itp profile dashboards <profile>
./itp profile restart <profile>
```

For legacy/root mode, run `docker compose exec collector python -m collectors`
with `infrastructure generate`, `operations generate`, `services generate`,
`wallboard generate`, and `dashboards generate`, in that order, then run
`./itp restart`. These commands replace generated files under the selected
runtime; no telemetry database reset is required.
