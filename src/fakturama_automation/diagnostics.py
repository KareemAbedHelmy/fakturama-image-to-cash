"""Read-only discovery and capture of Fakturama's windows for diagnostics. No launching, focus changes,
clicks or saves.
"""

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .errors import ReviewRequired
from .input_io import write_private
from .uia import discover_fakturama_windows, inspect_window, list_process_windows


def choose_process(rows: list[dict], requested_pid: int | None) -> int:
    candidates = {row["pid"] for row in rows}
    if requested_pid is not None:
        if requested_pid not in candidates:
            raise ReviewRequired("pid_not_identified_as_fakturama")
        return requested_pid
    if not candidates:
        raise ReviewRequired("fakturama_window_not_found")
    if len(candidates) > 1:
        raise ReviewRequired("multiple_fakturama_processes_choose_pid")
    return next(iter(candidates))


def capture_bundle(label: str, output_root: Path, requested_pid: int | None = None) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", label):
        raise ReviewRequired("invalid_capture_label")
    if requested_pid is None:
        pid = choose_process(discover_fakturama_windows(), None)
    else:
        if type(requested_pid) is not int or requested_pid <= 0:
            raise ReviewRequired("invalid_process_id")
        pid = requested_pid
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = output_root / f"{label}-{stamp}"
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    windows = list_process_windows(pid)
    captured = []
    for row in windows:
        if row["pid"] != pid:
            raise ReviewRequired("capture_window_process_mismatch")
        try:
            tree = inspect_window(pid, row["handle"])
        except ReviewRequired as exc:
            if exc.code == "window_not_visible":
                continue
            raise
        if not tree["tree"].get("visible", False):
            continue
        filename = f"window-{row['handle']}.json"
        write_private(folder / filename, json.dumps(tree, ensure_ascii=False, indent=2))
        captured.append({"name": row["name"], "handle": row["handle"], "file": filename})
    if not captured:
        raise ReviewRequired("no_visible_fakturama_windows_captured")
    manifest = {"type": "read-only-uia-diagnostics", "label": label, "pid": pid,
                "created_at_utc": stamp, "windows": captured,
                "note": "May contain visible synthetic/accounting data. Review before sharing. No screenshot or desktop mutation."}
    write_private(folder / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    bundle = output_root / f"{label}-{stamp}.zip"
    with zipfile.ZipFile(bundle, "x", zipfile.ZIP_DEFLATED) as archive:
        for item in [folder/"manifest.json", *(folder/row["file"] for row in captured)]:
            archive.write(item, item.name)
    bundle.chmod(0o600)
    return bundle
