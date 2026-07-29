import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def contains_sensitive_identifier(text, identifier):
    """Match an identifier token without treating punctuation as word text."""
    pattern = rf"(?<![A-Za-z0-9]){re.escape(identifier)}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True,
        capture_output=True)
    return [
        ROOT / value.decode()
        for value in result.stdout.split(b"\0") if value
    ]


def test_tracked_repository_contains_no_site_or_generated_content():
    denylist = {
        "s" + "bc",
        "m" + "lc",
        "st-" + "brigids",
        "methodist " + "ladies",
    }
    denylist.update(
        value.strip().casefold()
        for value in os.getenv("ITP_HYGIENE_DENYLIST", "").split(",")
        if value.strip())
    findings = []
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        lowered_path = relative.casefold()
        if relative.startswith("runtime/") and relative != "runtime/README.md":
            findings.append(f"tracked runtime file: {relative}")
        if (
            lowered_path.startswith(("build/", "dist/"))
            or ".egg-info/" in lowered_path
            or "__pycache__/" in lowered_path
            or lowered_path.endswith((".pyc", ".db", ".sqlite", ".sqlite3"))
        ):
            findings.append(f"tracked build/runtime artefact: {relative}")
        if path.name == ".env":
            findings.append(f"tracked environment file: {relative}")
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lowered = text.casefold()
        for denied in sorted(denylist):
            if (
                contains_sensitive_identifier(lowered, denied)
                or contains_sensitive_identifier(lowered_path, denied)
            ):
                findings.append(f"denylisted identifier in {relative}")
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", text):
            findings.append(f"private key in {relative}")
        if re.search(
            r"(?im)^(?:password|token|api_key|client_secret)"
            r"\s*[:=]\s*[\"']?(?!change_me|replace_me|example|placeholder|\s*$)"
            r"[A-Za-z0-9+/_.-]{12,}",
            text,
        ) and not relative.endswith(".env.example"):
            findings.append(f"populated secret-like assignment in {relative}")
    assert not findings, "\n".join(sorted(set(findings)))


def test_sensitive_identifier_matching_uses_explicit_token_boundaries():
    assert not contains_sensitive_identifier("htmlcov/", "bc")
    assert contains_sensitive_identifier("BC", "bc")
    assert contains_sensitive_identifier("tenant/bc-config", "bc")
    assert contains_sensitive_identifier("tenant_bc.config", "bc")


def test_release_documentation_uses_public_placeholders():
    findings = []
    email_pattern = re.compile(
        r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
    github_pattern = re.compile(r"https://github\.com/([^/\s)]+)/([^/\s)]+)")

    for path in tracked_files():
        if path.suffix.casefold() != ".md":
            continue
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for match in github_pattern.finditer(text):
            if match.groups() != ("<organisation>", "<repository>"):
                findings.append(f"concrete GitHub repository URL in {relative}")
        for match in email_pattern.finditer(text):
            domain = match.group(1).casefold()
            if domain not in {"example.com", "example.invalid"}:
                findings.append(f"non-fictional email address in {relative}")

    assert not findings, "\n".join(sorted(set(findings)))
