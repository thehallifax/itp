#!/usr/bin/env sh
set -eu

repository=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
python_bin=${ITP_PYTHON:-}
if [ -z "$python_bin" ]; then
  if command -v python >/dev/null 2>&1; then
    python_bin=$(command -v python)
  elif [ -x "$repository/.venv/bin/python" ]; then
    python_bin="$repository/.venv/bin/python"
  else
    python_bin=$(command -v python3 || true)
  fi
fi
if [ -z "$python_bin" ]; then
  echo "Python 3.9 or later is required. Install Python, then rerun this validation." >&2
  exit 1
fi
workspace=$(mktemp -d "${TMPDIR:-/tmp}/itp-fresh-clone.XXXXXX")
trap 'rm -rf "$workspace"' EXIT HUP INT TERM

(
  cd "$repository"
  tar --exclude=.git --exclude=.venv --exclude=runtime -cf - .
) | (
  cd "$workspace"
  tar -xf -
)

cd "$workspace"
initial_python=$python_bin
"$initial_python" scripts/bootstrap-dev.py
if [ -x "$workspace/.venv/bin/python" ]; then
  python_bin="$workspace/.venv/bin/python"
elif [ -x "$workspace/.venv/Scripts/python.exe" ]; then
  python_bin="$workspace/.venv/Scripts/python.exe"
else
  echo "Fresh-clone developer bootstrap did not create a usable Python environment" >&2
  exit 1
fi
"$python_bin" -m pytest discovery/tests/test_telemetry_hardening.py -q
"$python_bin" scripts/itp.py init \
  --non-interactive \
  --deployment-name "Fresh Clone Validation" \
  --deployment-id fresh-clone-validation \
  --timezone UTC \
  --grafana-port 43000 \
  --influxdb-port 48000 \
  --no-start
"$python_bin" scripts/itp.py doctor --offline --platform-only
"$python_bin" scripts/itp.py dashboard generate
"$python_bin" -c '
from collectors.writer import InfluxWriter
captured = []
writer = InfluxWriter(
    delegate=lambda points: captured.extend(points) or len(points),
    deployment_id="fresh-clone-validation",
    customer_id="fresh-clone-validation",
    site_id="site:fresh-clone-validation")
assert writer.write([{
    "measurement": "performance",
    "tags": {"collector": "fixture"},
    "fields": {"cpu_percent": "17.0"}}]) == 1
assert captured[0]["tags"]["site_id"] == "site:fresh-clone-validation"
'
"$python_bin" -c '
import json
from pathlib import Path
root = Path("runtime/deployments/fresh-clone-validation/generated/dashboard/managed")
dashboards = [
    path for path in root.rglob("*.json")
    if path.name != "registry.json"]
assert dashboards
for path in dashboards:
    value = json.loads(path.read_text())
    assert isinstance(value.get("panels"), list), path
'
echo "Fresh-clone bootstrap, Doctor, collection contract, and dashboards: PASS"
