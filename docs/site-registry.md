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
