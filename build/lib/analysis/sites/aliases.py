"""Explainable site-alias normalization; deliberately not fuzzy matching."""
from __future__ import annotations

import re
import unicodedata


def normalize_alias(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.replace("’", "'").replace("‘", "'")
    value = re.sub(r"['\"]", "", value)
    value = re.sub(r"[.,;:()\[\]{}]", " ", value)
    value = re.sub(r"[-_/\\]+", " ", value)
    return " ".join(value.split()) or None


def canonical_key(value):
    value = str(value or "").strip().lower()
    if value.startswith("site:"): value = value[5:]
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or None
