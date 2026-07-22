"""Framework configuration loading with strict environment expansion."""
import os
import re
from pathlib import Path

import yaml

ENVIRONMENT = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")
EXECUTIONS = {"central", "edge", "either"}


def _expand(value):
    if isinstance(value, dict): return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list): return [_expand(item) for item in value]
    if isinstance(value, str) and (match := ENVIRONMENT.match(value)):
        name = match.group(1)
        return os.getenv(name, "")
    return value


def load_config(path):
    try: value = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc: raise ValueError(f"invalid configuration {path}: {exc}") from exc
    if not isinstance(value, dict): raise ValueError("configuration must be a YAML mapping")
    value = _expand(value)
    collectors = value.get("collectors", {})
    if not isinstance(collectors, dict): raise ValueError("collectors configuration must be a mapping")
    for name, settings in collectors.items():
        if not isinstance(settings, dict): raise ValueError(f"collector {name} configuration must be a mapping")
        execution = settings.get("execution", "either")
        if execution not in EXECUTIONS:
            raise ValueError(f"collector {name} has unsupported execution placement: {execution}")
    return value
