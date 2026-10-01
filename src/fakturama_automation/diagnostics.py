"""Read-only app discovery/capture for an operator's Windows machine.

Does not launch an application, focus it, click/type/save, or change accounting
data. Captures only visible top-level windows in the chosen application PID.
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
    discovered = discover_fakturama_windows()
    pid = choose_process(discovered, requested_pid)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = output_root / f"{label}-{stamp}"
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    windows = list_process_windows(pid)
    # Handles and PID come from observed process windows, never image/model output.
    captured = []
    for row in windows:
        if row["pid"] != pid:
            raise ReviewRequired("capture_window_process_mismatch")
        tree = inspect_window(pid, row["handle"])
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
    # Exclusive creation. Include only files created by this capture, never an arbitrary directory.
    with zipfile.ZipFile(bundle, "x", zipfile.ZIP_DEFLATED) as archive:
        for item in [folder/"manifest.json", *(folder/row["file"] for row in captured)]:
            archive.write(item, item.name)
    bundle.chmod(0o600)
    return bundle
