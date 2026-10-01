# Next Windows step: inspect Fakturama 2.2.0

The automation needs the actual UIA controls on your machine. These commands inspect the app; they do not type, click, create records, or save accounting data. Use a synthetic-data workspace and inspect the resulting JSON before sharing it.

## 1. Install the updated package

Extract the updated ZIP. In VS Code, open the inner `fakturama-image-to-cash` folder containing `pyproject.toml`, then open a PowerShell terminal there:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Expected core result: 82 tests pass. The native Windows helpers still require on-machine validation; the test suite exercises their safe selection/diagnostic logic with doubles.

## 2. Capture New Order

Launch Fakturama and complete its first-run workspace setup if needed. Set the application language to English if available so it matches the supplied brief. Click the top toolbar's Order button yourself, leaving the New Order editor open.

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation.cli capture --label new-order
```

The command prints a ZIP path under `private/ui/`. This bundle includes accessible names, control types, parent hierarchy, current values, and supported UIA patterns from visible windows in the detected Fakturama process. It does not take a screenshot.

## 3. Capture the address selector

In the still-open Order, click the upper existing-contact icon beside Addresses (not the green +). Leave the Select the address dialog open. Run:

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation.cli capture --label address-selector
```

Upload the two generated ZIPs. A screenshot of each of these views is also useful to connect accessible controls to their labels/icons. If either command reports an error, send the printed error code and a screenshot instead of changing permissions or retrying saves.

## Troubleshooting discovery

If multiple Fakturama instances exist:

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation.cli discover
.\.venv\Scripts\python.exe -m fakturama_automation.cli capture --label new-order --pid 1234
```

Replace 1234 only with the observed PID belonging to the intended Fakturama window. Do not run as administrator by default. If UIA exposes little useful detail, that is evidence for adding an OCR/visual grounding adapter; it is not a reason to invent automation IDs or hardcode positions.
