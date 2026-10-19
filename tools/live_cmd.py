"""Run one guarded live command against the open Fakturama window.

  .\.venv\Scripts\python.exe tools\live_cmd.py vat-check --input examples/order.reduced-vat.json --sku BKS-NOTE-07

The window is found automatically and brought to the front; --pid, --window-handle,
--reviewed-input and --out are filled in. Any other option is passed through to the command.
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fakturama_automation import launcher  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    command, rest = sys.argv[1], sys.argv[2:]
    window = launcher.main_window()
    if window is None:
        sys.exit("No Fakturama main window found; start Fakturama first (tools/run_order_first.py does this).")
    launcher.bring_to_front(window["handle"])
    time.sleep(2)
    out = ROOT / "private" / f"{command}-{datetime.now():%H%M%S}.json"
    code = subprocess.run([sys.executable, "-m", "fakturama_automation.cli", command, *rest, "--reviewed-input",
                           "--pid", str(window["pid"]), "--window-handle", str(window["handle"]),
                           "--out", str(out)], cwd=ROOT).returncode
    print(f"report: {out}")
    sys.exit(code)


if __name__ == "__main__":
    main()
