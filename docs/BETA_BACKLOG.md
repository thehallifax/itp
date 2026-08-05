This document is maintained only in the development repository and is intentionally excluded from the public release repository.

# ITP Beta Backlog

This document tracks improvements discovered during beta deployments.

Criteria:
- Not release blockers.
- Safe to defer until after production validation.
- Focused on usability, operator experience, dashboard polish, and workflow improvements.

---

# High Priority

## BETA-026 — Prerequisite Bootstrap

**Status:** Implemented

## BETA-027 — Deployment Configuration Editor

**Status:** Foundation implemented; full acceptance pending

Canonical metadata, ports, timezone and enabled collectors can be validated,
previewed and atomically edited with rollback copies. Connector endpoints,
intervals, and dashboard options still need to be incorporated into the same
section-based editor before BETA-027 is complete.

## BETA-028 — Sanitised Support Bundle

**Status:** Foundation implemented; full acceptance pending

Deployment-scoped ZIP evidence includes a manifest and fail-closed credential
scan. Standard and stable high-privacy modes are available. Stopped/partial
deployment fixtures and complete Docker volume/network inventory remain.

## BETA-029 — Interactive Connector Onboarding

**Status:** Foundation implemented; full acceptance pending

Palo Alto, FortiGate, PaperCut and Mist use guided setup. Palo Alto and FortiGate can
select discovered WAN interfaces while retaining canonical interface identity.
Product-mismatch confirmation and the complete diagnostic/re-run acceptance
matrix remain.

## BETA-030 — Guided Recovery

**Status:** Foundation implemented; full acceptance pending

Structured recovery inspection presents safe commands and marks destructive
telemetry reset explicitly without selecting it by default. Interactive action
execution, phase checkpoints, and scheduler suppression for deterministic
configuration failures remain.

## BETA-017 — Interactive WAN Configuration

**Status:** Planned

### Objective

Move Palo Alto WAN interface configuration into the deployment onboarding workflow.

### Scope

- Prompt for WAN interfaces during deployment.
- Collect:
  - Interface name (e.g. `ethernet1/5`)
  - Role (Primary, Secondary, Backup, Cellular, etc.)
  - Friendly display name
- Validate duplicate interfaces.
- Validate a single Primary interface.
- Generate `wan_interfaces` automatically.
- Preserve manual editing for advanced deployments.

---

## BETA-018 — Deployment Configuration Editor

**Status:** Superseded by BETA-027

### Objective

Allow deployments to be modified without rebuilding from scratch.

### Candidate command

```text
./itp deployment edit --deployment <deployment>
```

### Editable settings

- Deployment display name
- Site ID
- Site display name
- Listening address
- Ports
- Timezone
- Enabled collectors
- Collector configuration
- Dashboard options

---

## BETA-019 — Credentials UX

**Status:** Planned

### Objective

Improve the output of:

```text
./itp credentials grafana
```

### Current

Displays:

```text
http://127.0.0.1:3000
```

even when Grafana is listening on:

```text
0.0.0.0
```

### Desired

Display information that accurately reflects deployment configuration without attempting to guess network addresses.

---

# Medium Priority

## BETA-020 — WAN Dashboard Polish

Review after several production deployments.

Areas:

- Panel layout
- Legend positioning
- Responsive behaviour
- Gap rendering
- Axis scaling
- Colour consistency
- Multi-WAN presentation
- Empty-state wording

---

## BETA-021 — Dashboard Layout Review

Perform a complete review of every generated dashboard after multiple customer deployments.

Goals:

- Consistent spacing
- Consistent panel heights
- Logical grouping
- Better use of horizontal space
- Improved readability on 1080p and ultrawide displays

---

## BETA-022 — Operations Dashboard Refinement

Continue refining operational dashboards based on real-world deployments.

Potential improvements include:

- Better service grouping
- Improved status wording
- Risk prioritisation
- Reduced visual clutter
- Operator workflow improvements

---

# Low Priority

## BETA-023 — CLI Experience

Review the overall command-line experience.

Potential improvements:

- Clearer progress reporting
- Better recovery guidance
- Improved error messages
- More consistent help output
- Command discoverability

---

## BETA-024 — Documentation Polish

Review all operator documentation after beta.

Focus on:

- First deployment experience
- Windows walkthrough
- macOS walkthrough
- Collector onboarding
- Troubleshooting flow
- Cross-link consistency

---

# Future Ideas

Ideas that should not distract from beta but are worth remembering.

- Additional collectors
- Deployment templates
- Collector profiles
- Scheduled reporting improvements
- Advanced dashboard customisation
- Multi-tenant deployment enhancements

---

# Notes

Anything discovered during beta that is not a release blocker should be added here rather than interrupting deployment work.

The goal is to batch usability improvements into dedicated housekeeping sprints once multiple real-world deployments have validated the core platform.

## Post-deployment UX follow-up

- Improve `credentials grafana` output when bind address is `0.0.0.0`.
- Display local URL separately from listening address.
- Review configuration edit discoverability after real deployments.
- Review support-bundle redaction with real support cases.
- Review onboarding after Palo Alto, FortiGate and PaperCut deployments.
- Improve dashboard layout and responsive behaviour.
- Review semantic empty states and panel density.
- Add atomic `credentials grafana change --deployment <id>` rotation. Prompt
  without echo, update Grafana first, persist the ITP secret only after success,
  verify login, and roll back safely. Passwords must never enter command
  history, logs, JSON, or support bundles. This follows a live case where the
  stored ITP password diverged from Grafana's persisted administrator password.
- Summarize default human CLI output. Keep detailed capability, measurement,
  and pipeline evidence behind `--verbose`, with full structured output behind
  `--json`.

## Collector onboarding follow-up

- Extend guided setup adapters to Aruba Central, Mist, VMware, Hyper-V,
  Proxmox and SNMP.
- Add vendor-specific permission validation where APIs expose it.
- Add richer FortiGate SD-WAN health mapping after field validation.
- Review automatic WAN recommendations across several real sites.
- Consider optional Git installation after explicit consent.
- Consider Docker installation guidance; do not blindly install Docker Desktop
  on Windows Server.

## Deferred operations work

Estate roll-ups, multi-customer operations, executive multi-site reporting,
new service domains, new collectors, notification expansion, broad dashboard
redesign, topology visualisation, and scheduled PDF/email reporting remain
deferred. Operations expansion should wait until beta UX has been exercised at
multiple real sites.
