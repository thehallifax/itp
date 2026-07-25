"""Read-only Hyper-V transport contracts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class HyperVCommandRunner:
    def run(self):
        raise NotImplementedError


class LocalPowerShellRunner(HyperVCommandRunner):
    def __init__(self, script):
        self.script = Path(script)

    def run(self):
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(self.script)],
            check=True, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        return json.loads(completed.stdout)


class PowerShellRemotingRunner(HyperVCommandRunner):
    """Contract placeholder; remoting policy must be supplied by a Windows host."""
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def run(self):
        raise NotImplementedError(
            "PowerShell remoting must run on an explicitly authorised Windows "
            "management host; ITP does not enable or weaken remoting policy.")


class FixtureHyperVRunner(HyperVCommandRunner):
    def __init__(self, path):
        self.path = Path(path)

    def run(self):
        return json.loads(self.path.read_text())
