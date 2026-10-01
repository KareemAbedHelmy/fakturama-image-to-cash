"""Bounded JSON parsing with duplicate-key rejection and private output files."""

import json
import os
from pathlib import Path

from pydantic import ValidationError

from .domain import OrderInput
from .errors import ReviewRequired

MAX_JSON_BYTES = 1024 * 1024


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ReviewRequired("duplicate_json_key")
        obj[key] = value
    return obj


def parse_json(data: bytes | str):
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise ReviewRequired("json_too_large")
    try:
        return json.loads(data, object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ReviewRequired("invalid_json_constant")))
    except (ValueError, UnicodeError, RecursionError):
        raise ReviewRequired("invalid_json") from None


def parse_order(data: bytes | str) -> OrderInput:
    value = parse_json(data)
    try:
        return OrderInput.model_validate(value)
    except ValidationError:
        # Pydantic errors can echo the input; don't send those into logs/terminal.
        raise ReviewRequired("invalid_order_schema") from None


def read_order(path: Path) -> OrderInput:
    with path.open("rb") as f:
        return parse_order(f.read(MAX_JSON_BYTES + 1))


def write_private(path: Path, data: str | bytes):
    """No overwrite/symlink following. Windows permissions inherit parent ACLs."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
