# Collector templates

Collector field and credential requirements come from
`collectors/connector-registry.yml`. The deployment CLI materialises selected
collector configuration and ignored secret files under the active runtime
deployment. Tracked templates never contain populated credentials.
