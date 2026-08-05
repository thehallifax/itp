"""Conservative static validation for dashboard SQL measurement contracts."""
from __future__ import annotations

import re
from dataclasses import dataclass

_SOURCE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_CTE = re.compile(r"(?:\bWITH|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", re.IGNORECASE)
_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_ALIAS = re.compile(r"\bAS\s+(?:\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))", re.IGNORECASE)
_FUNCTION = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_STRING = re.compile(r"'(?:''|[^'])*'")
_GRAFANA = re.compile(r"\$\{[^}]+\}|\$__[A-Za-z0-9_]+")
_KEYWORDS = {
    "all", "and", "as", "asc", "by", "case", "cast", "desc", "distinct",
    "else", "end", "false", "from", "group", "inner", "interval", "is",
    "join", "left", "like", "limit", "not", "null", "on", "or", "order",
    "over", "partition", "select", "then", "true", "union", "varchar",
    "when", "where", "with",
}


@dataclass(frozen=True)
class MeasurementContract:
    tags: frozenset[str]
    fields: frozenset[str]

    @property
    def columns(self):
        return {"time", *self.tags, *self.fields}


@dataclass(frozen=True)
class QueryContractError:
    dashboard: str
    panel: str
    measurement: str
    missing: str
    reason: str


def contracts_from_points(points, extras=None):
    """Build deterministic measurement contracts from representative points."""
    values = {}
    for point in points:
        name = str(point.get("measurement") or "")
        if not name:
            continue
        current = values.setdefault(name, {"tags": set(), "fields": set()})
        current["tags"].update(point.get("tags") or {})
        current["fields"].update(point.get("fields") or {})
    for name, value in (extras or {}).items():
        current = values.setdefault(name, {"tags": set(), "fields": set()})
        current["tags"].update(value.get("tags") or ())
        current["fields"].update(value.get("fields") or ())
    return {name: MeasurementContract(
        frozenset(value["tags"]), frozenset(value["fields"]))
        for name, value in sorted(values.items())}


def dashboard_queries(dashboard):
    """Yield panel and variable SQL using stable operator-facing names."""
    for panel in dashboard.get("panels", []):
        for target in panel.get("targets", []):
            sql = target.get("rawSql")
            if sql:
                yield str(panel.get("title") or "Untitled panel"), str(sql)
    for variable in dashboard.get("templating", {}).get("list", []):
        sql = variable.get("query")
        if isinstance(sql, str) and re.search(r"\bSELECT\b", sql, re.IGNORECASE):
            yield f"Variable: {variable.get('name') or 'unnamed'}", sql


def query_measurements(sql):
    """Return physical measurements referenced by straightforward SQL."""
    cleaned = _GRAFANA.sub(" ", _STRING.sub(" ", str(sql)))
    ctes = {match.group(1) for match in _CTE.finditer(cleaned)}
    return tuple(sorted({match.group(1) for match in _SOURCE.finditer(cleaned)}
                        - ctes))


def _references(sql):
    cleaned = _GRAFANA.sub(" ", _STRING.sub(" ", sql))
    aliases = {next(value for value in match.groups() if value)
               for match in _ALIAS.finditer(cleaned)}
    functions = {match.group(1) for match in _FUNCTION.finditer(cleaned)}
    ctes = {match.group(1) for match in _CTE.finditer(cleaned)}
    sources = {match.group(1) for match in _SOURCE.finditer(cleaned)}
    ignored = {value.casefold() for value in (
        aliases | functions | ctes | sources)} | _KEYWORDS
    references = {value for value in _TOKEN.findall(cleaned)
                  if value.casefold() not in ignored}
    return set(query_measurements(sql)), references


def validate_dashboard_contract(dashboard, contracts):
    """Return schema drift without attempting to implement a full SQL parser."""
    errors = []
    title = str(dashboard.get("title") or dashboard.get("uid") or "dashboard")
    known_columns = {column for value in contracts.values()
                     for column in value.columns}
    known_columns.update({
        "customer_id", "customer_name", "site_id", "site_name",
        "deployment_id"})
    for panel, sql in dashboard_queries(dashboard):
        sources, references = _references(sql)
        missing_measurements = sorted(sources - set(contracts))
        for measurement in missing_measurements:
            errors.append(QueryContractError(
                title, panel, measurement, measurement,
                "measurement is not emitted by the collector"))
        emitted = sources & set(contracts)
        if not emitted:
            continue
        available = {column for measurement in emitted
                     for column in contracts[measurement].columns}
        for column in sorted((references & known_columns) - available):
            errors.append(QueryContractError(
                title, panel, ",".join(sorted(emitted)), column,
                "column is absent from the emitted measurement contract"))
    return errors


def query_outcome_state(*, succeeded, row_count, empty_state):
    """Keep execution failures distinct from successful empty query results."""
    if not succeeded:
        return "Query failed"
    if row_count == 0:
        return str(empty_state)
    return "Populated"
