# Collector capability manifests

ITP declares what each collector can support separately from what its latest run
actually collected. This prevents an absent field from being interpreted as a
healthy feature or a collector failure.

## Contract

Support is `supported`, `conditional`, `unsupported`, or `unknown`. Conditional
capabilities state their device/configuration requirement; unsupported
capabilities state why they are unavailable.

Runtime collection is `collected`, `not_yet_collected`, `disabled`,
`unavailable`, `failed`, `partial`, or `not_applicable`. Static support is
authoritative: runtime success cannot change `unsupported` to `supported`.
Unsupported capabilities do not degrade service health. Failed required
capabilities do.

Entries identify measurements, fields, canonical services and dashboard panels.
They never contain endpoints, credentials, tokens, payloads or raw exceptions.

## Generate and inspect

```bash
python -m collectors --profile example-school capabilities generate
python -m collectors --profile example-school capabilities inspect --json
./itp profile capabilities example-school
```

Outputs are deterministic runtime artefacts:

```text
runtime/<profile>/capabilities/
  collectors.json
  paloalto.json
  papercut.json
  snmp.json
```

Generation runs before infrastructure, service health, wallboard and dashboard
rendering.

## Extend

Add a stable declaration in `collectors/capabilities.py` and a fixture-based
test. Never infer support from a dashboard query or from a field being present
in one response.

Aruba Central declares its static contract in
`collectors/aruba/capabilities.py`. Its inventory, health, firmware and client
capabilities are conditional on authoritative device/API evidence; group, site
and account discovery remain separately explainable.
