"""Human and JSON rendering without secret-bearing tracebacks."""
import json


LABELS = {
    "pass": "PASS", "warn": "WARN", "fail": "FAIL",
    "skip": "SKIP", "unavailable": "UNAVAILABLE",
}


def render_human(report, strict=False):
    lines = ["Infrastructure Telemetry Platform Doctor"]
    categories = (
        "Platform", "Services", "Connectors", "State History",
        "Scheduler", "Operations Engine")
    for category in categories:
        checks = [value for value in report.checks if value.category == category]
        if not checks:
            continue
        lines.extend(("", category))
        for check in checks:
            lines.append(
                f"[{LABELS[check.status]}] {check.subject} — {check.summary}")
            if check.detail and check.status != "pass":
                lines.append(f"       Detail: {check.detail}")
            if check.remediation:
                lines.append(f"       Remediation: {check.remediation}")
            if check.command:
                lines.append(f"       Command: {check.command}")
    summary = report.summary
    lines.extend(("", "Summary: " + ", ".join(
        f"{key}={summary[key]}" for key in
        ("pass", "warn", "fail", "skip", "unavailable"))))
    lines.append(f"Exit code: {report.exit_code(strict)}")
    return "\n".join(lines)


def render_json(report, strict=False):
    return json.dumps(report.to_dict(strict), indent=2, sort_keys=True)
