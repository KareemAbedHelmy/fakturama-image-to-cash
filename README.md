# Fakturama Image-to-Cash Automation

Python implementation of a guarded Order-first workflow, a four-page design document, local image/OCR tooling, and Windows UIA inspection primitives.

**Current status: foundation milestone, not submission-ready desktop automation.** The complete business workflow runs against an in-memory simulator. The supplied image has a visually reviewed test transcription; its totals and separate delivery recipient pass core checks. The live Fakturama adapter, automatic extraction accuracy, and desktop screenshots/recording are pending. `run` exits with `live_adapter_not_implemented`; it never silently substitutes the simulator. No live Fakturama records have been created or verified by this repository. The user confirmed Windows Fakturama 2.2.0.

The assignment explicitly permits partial implementation in a five-hour timebox. This README distinguishes what is implemented from what is proposed so reviewers can assess the work honestly. Track actual time spent separately; do not present a later extended implementation as five hours of work.

## Deliverables and layout

| Path | Purpose |
| --- | --- |
| `docs/design.pdf` | Part 1: four-page design, no code |
| `docs/design.md` | Editable design source |
| `docs/requirements.md` | Requirement coverage and remaining acceptance tests |
| `docs/security.md` | Threat model, implemented safeguards, and open risks |
| `docs/windows-validation.md` | Windows setup, inspection, and evidence checklist |
| `docs/written-answer.md` | Answer to “If you had 3 more hours…” for the current milestone |
| `src/fakturama_automation/` | Schema, finance, matching, workflow, journal, extraction, UIA helpers, simulator, CLI |
| `tests/` | Automated core, failure-injection, input, and UIA-helper tests |
| `examples/order.synthetic.json` | Synthetic two-line, two-VAT-rate paid order; not the employer's image |
| `examples/order.synthetic.png` | Synthetic image for local OCR smoke checks; not Fakturama evidence |
| `examples/order.provided.expected.json` | Visually reviewed transcription of the supplied image, used only as a test oracle |
| `docs/provided-order.md` | Source values, interpretation decisions, and extraction limitations |
| `docs/start-on-windows.md` | Next steps and read-only UIA capture commands |
| `docs/verification.md` | Recorded checks and their limits |

## Setup on Windows (PowerShell)

Use Python 3.11 or later; the target user environment is Python 3.11. You do not need the Windows `py` launcher. Extract the archive and enter the repository folder.

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Using the venv interpreter directly avoids changing the PowerShell execution policy. Core dependencies are Pydantic 2.13.5 and Pillow 12.3.0; Windows inspection uses pywinauto 0.6.9. Transitive dependencies are not yet locked/audited; capture them after validating a fresh Windows installation. Linux can install `-e .` and run all platform-independent tests.

Download Fakturama from its official [downloads page](https://www.fakturama.info/downloads/). The page currently lists 2.2.0 and a Windows installer with Java included. Record the actual version/language under Help/About. Use a separate synthetic-data workspace; do not test against business accounting data. This repository does not download, launch, or reconfigure Fakturama automatically.

## Validate and simulate

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation.cli validate examples/order.synthetic.json
.\.venv\Scripts\python.exe -m fakturama_automation.cli simulate examples/order.synthetic.json --seed missing --journal runs/missing.sqlite
.\.venv\Scripts\python.exe -m fakturama_automation.cli simulate examples/order.synthetic.json --seed existing --journal runs/existing.sqlite
.\.venv\Scripts\python.exe -m fakturama_automation.cli simulate examples/order.synthetic.json --seed mixed --journal runs/mixed.sqlite
.\.venv\Scripts\python.exe -m fakturama_automation.cli journal runs/missing.sqlite
```

These commands create only simulator records and a local journal. Re-running the same input with the same journal is intentionally blocked. Separate journals above are **isolated simulator scenarios**, not a way to bypass duplicate prevention in live use. A real deployment must use one stable journal path and workspace ID for the same Fakturama database. An interrupted/review run retains its lease and requires reconciliation, not deleting the journal and trying again.

The simulator enforces Order-first selection, retaining the same Order, VAT-before-Product creation, reselecting saved masters, one save per document, follow-up Invoice creation, and persisted readback. Financial calculations use Decimal and ROUND_HALF_UP. VAT currently aggregates rounded line nets by rate and rounds each rate's tax; this needs empirical comparison to Fakturama.

## Image extraction

No external API key is needed. The local OCR command requires an independently installed Tesseract executable:

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation.cli ocr --image examples/order.synthetic.png --tesseract "C:\Program Files\Tesseract-OCR\tesseract.exe" --out private/ocr.txt
```

OCR produces text, not structured order data. For layout-aware extraction, an optional client calls an already-running Ollama server at fixed loopback address `127.0.0.1:11434`. You must supply an already-installed local vision model supporting images and structured outputs. No model is downloaded automatically. Configure Ollama to disable cloud features; a loopback endpoint alone does not guarantee that the server never sends data elsewhere. Cloud-labelled model names are rejected, but this is not attestation of server behavior.

```powershell
# Replace LOCAL_VISION_MODEL with an installed local model name.
.\.venv\Scripts\python.exe -m fakturama_automation.cli extract --image private/order.png --model LOCAL_VISION_MODEL --out private/candidate.json
.\.venv\Scripts\python.exe -m fakturama_automation.cli validate private/candidate.json
```

The output is an **untrusted candidate**. Review each value against the image, including any missing field or `review_issues`, before desktop use. Structured output and matching totals do not prove OCR/model accuracy. The actual supplied image was visually reviewed and tried with local OCR; OCR was inaccurate at its 385x529 resolution. A live vision model has not been tested here. Prompt text in the image cannot request tools; the model has no shell or desktop access. The reviewed example is test data, not an image-hash lookup or hardcoded extraction path.

Inputs: PNG/JPEG, single frame, at most 10 MiB and 20 million pixels. JSON at most 1 MiB; up to 100 lines. Money/percent/quantity are decimal strings, with up to six decimals, no exponent or thousands separator. Dates use `YYYY-MM-DD`. Exact data fields reject unknown keys, control characters, duplicate JSON keys, negative/non-finite numbers, and invalid percentages. Optional absent text is null. Source line totals must be explicitly net; gross-only or unclear line totals need review.

Initially supported: EUR; Bank Transfer, Credit Card, SEPA Direct Debit; PAID with payment date or UNPAID without date; zero order-level discount and shipping. Unknown/partial payment, other currencies, nonzero shipping/order discounts, and conflicting repeated-SKU definitions stop for review. These are documented limitations, not silent defaults. Separate billing/delivery addresses are represented and tested in the core; live address-role handling remains to be implemented.

## Windows UI inspection

For the current next step, open Fakturama's New Order and capture its accessible controls without finding process IDs manually:

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation.cli capture --label new-order
```

The command detects a single visible Fakturama process and captures only that process's visible windows into a ZIP under `private/ui/`. It does not focus, click, type, save, or take screenshots. JSON includes readable field values and supported UIA patterns, so inspect it before sharing. If more than one Fakturama process exists, run `discover` and supply the observed `--pid`. Detailed staged instructions are in `docs/start-on-windows.md`.

Find the actual Fakturama process ID in Task Manager (Details). List only that process's windows, then use the returned native window handle:

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation.cli windows --pid 1234
.\.venv\Scripts\python.exe -m fakturama_automation.cli inspect --pid 1234 --window-handle 5678 --out private/order-uia.json
```

Replace both numeric examples with actual values. Inspection is read-only and does not click controls. Dumps may contain names/addresses; use synthetic data and inspect/redact before sharing. Capture separate dumps for the main window, New Order, address/product selectors, Debtor/Payment, VAT, Product, Documents, and linked Invoice. Some controls may be inaccessible; the design describes a future OCR grounding fallback. No absolute-coordinate fallback is implemented.

The reusable UIA helpers require one exact, visible, enabled control within a caller-provided scope, use value/invoke patterns, verify value readback, and never retry an uncertain invocation. They do **not** yet supply the application-specific `DesktopPort` methods. Accessibility and control patterns must be measured on the installed Fakturama, not assumed from labels in the brief.

## Recovery and security

All automation operations log durable intent before execution. On a failure, the job enters REVIEW and keeps its workspace lease. A save may already have succeeded. Inspect the journal, generated document numbers, current editors, Documents list, and saved masters before any recovery. There is no automatic resume/reset/delete operation. Never delete accounting records as rollback.

Default terminal output omits source names and addresses. Private candidate files/OCR/UI dumps are local and excluded from Git. POSIX output permissions are restrictive; on Windows files inherit the directory's ACL, so choose a user-private folder. The journal is a recovery aid, not tamper-proof storage or a cross-machine lock. Refer to `docs/security.md` for unresolved risks. No claim of zero vulnerabilities or healthcare compliance is made.

## Completion work

1. Inspect actual Windows controls and implement `DesktopPort` with scoped observed locators and stable selector results.
2. Validate the employer's image extraction and Fakturama rounding, locale, saved document state, and relationship.
3. Record one complete live Order-to-Invoice flow, then existing/missing/ambiguous master and failure scenarios.
4. Add the verified image-to-live orchestration, recovery reconciliation, fresh Windows install/dependency audit, and actual screenshots/recording.

Until these steps pass, do not submit the simulator as working Fakturama automation. The proposed design intentionally describes a broader system than the currently verified implementation.

References: [Fakturama downloads](https://www.fakturama.info/downloads/), [pywinauto inspection guide](https://pywinauto.readthedocs.io/en/latest/getting_started.html), [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [Pydantic configuration](https://docs.pydantic.dev/latest/api/config/).
