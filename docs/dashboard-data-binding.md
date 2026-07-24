# Canonical Dashboard Data Binding

Canonical dashboards do not read runtime JSON files directly. The supported data
path is:

```text
canonical runtime JSON
  → deterministic dashboard renderer
  → embedded typed CSV in generated classic dashboard JSON
  → provisioned Grafana TestData datasource (`itp-runtime-values`)
  → `csv_content` query
  → optional canonical-site filter and field organisation
  → stat, table, or node-graph panel
```

Grafana cannot query arbitrary local JSON merely because the files are mounted.
The files remain the canonical source, while the embedded CSV frame is a
presentation projection. No browser code, `file://` URL, external service, or
third-party plugin is involved.

`csv_content` is required for named tabular fields. The similarly named
`csv_metric_values` scenario accepts a numeric `stringInput` sequence and must not
be used with `csvContent`.

Infrastructure Overview is generated under
`runtime/dashboard/grafana/infrastructure-overview.json`. Operations Wallboard is
generated under `runtime/dashboard/operations/operations-wallboard.json`. Grafana
file providers watch both directories every 30 seconds.

Site-aware wallboard frames contain a `scope` column. `all` is an explicit estate
scope row—not an SQL wildcard. Selecting a canonical site filters to its exact
`site_id`. Collector health remains unfiltered because collector-to-site ownership
is not authoritative.

Topology uses two CSV frames. Node fields are `id`, `title`, `subTitle`,
`mainStat`, `secondaryStat`, and `color`; edge fields are `id`, `source`, and
`target`. A scope column is filtered before Grafana renders the node graph.
