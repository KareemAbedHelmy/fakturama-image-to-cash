# Fakturama Image-to-Cash Automation

Order-first automation of **Fakturama 2.2.0** (Windows) with Microsoft UI
Automation. From one order image it produces a saved Order and a saved, paid
Invoice linked to that Order, verifying the persisted records as it goes.

**Start here:** [Design document (PDF)](docs/design.pdf) explains the system
architecture and workflow. Its editable source is [design.md](docs/design.md).
For the end-to-end walkthrough, watch the [live demo recording](#recording).

## Status at a glance

Two runs on real Fakturama reached a saved Order and a saved, paid, linked
Invoice with the Order still open: the supplied order on an empty database
(Order PO000001 / Invoice INV000001, Bank Transfer, paid 2026-07-18, EUR
678.30) and a new customer with a new product through the guided runner
(PO000005 / INV000005). See [the live run record](docs/live-run.md) and the
[recording](#recording).

| Area | State |
| --- | --- |
| Order header, Debtor/Product selector existence checks, line Qty./Discount, Order verify + Save, Documents check, follow-up Invoice, Invoice deep check, payment status, Invoice Save, final Documents check | Automated and run live |
| Missing Debtor (open, fill, roles, delivery address, payment, Save) and missing Product (open, fill, gross price, Save once) | Automated and run live (second run) |
| Missing VAT record, missing payment term | Field entry exists; creation is still an operator step and was not exercised |
| Extraction from the image | OCR cross-check runs live; LLM extraction (`extract`, needs `ANTHROPIC_API_KEY`) is implemented but the live runs used a reviewed transcription |
| One unattended command | `tools/run_order_first.py` is guided: it pauses only for the VAT check and the confirmations before creating a Debtor or Product. The simulator `run` command is intentionally blocked |

What was skipped, and why, is in [If I had three more hours](#if-i-had-three-more-hours).

## Setup

**Prerequisites**

| Needed for | Requirement |
| --- | --- |
| Everything | Python 3.11 or later |
| Live automation | Windows 10/11, [Fakturama 2.2.0](https://www.fakturama.info/downloads/) with the **English** user interface, installed and started once so its workspace exists |
| OCR cross-check | [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) installed at `C:\Program Files\Tesseract-OCR	esseract.exe` |
| LLM extraction (optional) | An `ANTHROPIC_API_KEY` environment variable, only if you run `extract` or omit `--input` |

**Install** (PowerShell, from the folder that contains `pyproject.toml`):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -q   # 370+ tests, no Fakturama needed
```

Dependencies are pinned: Pydantic 2.13.5, Pillow 12.3.0 and, for the live
flow, pywinauto 0.6.9. Transitive dependencies are not locked or audited.

## Run the automation against Fakturama

1. Start Fakturama 2.2.0 (any workspace; an empty one exercises the
   create-missing-records branches, a populated one the existing-records
   branches). Close any open New Order, New Debtor or New product editors.
   Leave the main window visible: it must stay in the foreground while a step
   runs, and the tool never uses stored screen coordinates.
2. Start the guided flow from PowerShell. It finds the Fakturama window
   itself (no process ID or handle needed) and counts down 5 seconds before
   each live step so you can click into Fakturama:

```powershell
.\.venv\Scripts\python.exe tools
un_order_first.py --image examples/order.provided.png --input examples/order.provided.expected.json
```

3. Press Enter at the "Prepare" prompt, then leave the mouse and keyboard
   alone. The run stops at the first step it cannot verify and prints the
   report path (`private/run-<timestamp>/NN-<step>.json`); it never retries a
   Save. Answer the prompts: confirm before a missing Debtor or Product is
   created, and do the VAT check by hand when asked.

| Option | Use |
| --- | --- |
| `--input FILE` | Reviewed order JSON; OCR cross-checks it against `--image` |
| (no `--input`) | Extract with Claude vision (`ANTHROPIC_API_KEY`); you confirm the result |
| `--no-ocr-check` | Test orders that have no matching image, e.g. `examples/order.new-customer.json` |
| `--resume-number PO0000NN` | Continue with an already open, header-prepared New Order |
| `--resume-debtor CUSTNNNNNN` | Continue with an already open, unsaved New Debtor |
| `--order-saved` | The Order is already saved; continue with the Documents check and the Invoice |

Every live command is also available on its own, for example
`python -m fakturama_automation.cli selector-check --help`.

Every live command writes an intent file before acting and a report after,
verifies before and after, invokes Save at most once and never retries an
uncertain action. Source text is data: it is never sent as keystrokes.

The flow follows the brief in order:

1. Extract and validate (decimal totals must reconcile), open New Order, set
   date, Cust.Ref., Net and With VAT.
2. Search the Order's Debtor selector. One exact row is selected; none starts
   the missing-Debtor branch; several stop for review.
3. For each line, search the Product selector by exact SKU, create the Product
   only if missing, return to the same Order, then set Qty./discount and
   verify every line.
4. Verify addresses and totals, Save the Order once, confirm it in
   Data > Documents, create the Invoice from the Order's follow-up area.
5. Verify the Invoice copy (Order Date, both addresses, items, totals), apply
   the payment status, Save once and confirm both rows (Invoice paid, Order
   still open) in Data > Documents.

## Reviewer demo (no Fakturama needed)

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation.cli demo examples/order.provided.expected.json --reviewed-input --out-dir private/reviewer-demo
```

Runs eight scenarios in an in-memory simulator (missing/existing/mixed masters,
unpaid input, duplicate Debtor or Product, a failure after Order Save, a
corrupted total) and writes `report.html`. It is simulator evidence only; an
example is in [docs/demo-report.html](docs/demo-report.html).

## Repository layout

| Path | Contents |
| --- | --- |
| `src/fakturama_automation/domain.py`, `input_io.py`, `finance.py`, `matching.py` | Strict schema, bounded parsing, decimal finance, exact matching |
| `workflow.py`, `ports.py`, `journal.py`, `simulator.py`, `demo.py` | Order-first workflow, desktop contract, intent journal, simulator |
| `extraction.py`, `ocr_verify.py` | Claude/Ollama vision extraction, Tesseract cross-check |
| `uia.py`, `native_ui.py`, `windows_clipboard.py`, `*_ui.py` | Guarded Windows bindings, one module per screen/step |
| `cli.py` | Every command (`python -m fakturama_automation.cli --help`) |
| `tools/` | `run_order_first.py` (guided live flow), plus helpers that render the design and the synthetic example |
| `tests/` | 370+ tests with recorded UIA fixtures |
| `docs/` | [Requirement coverage](docs/requirements.md), [live run record](docs/live-run.md), [security notes](docs/security.md) |

## Limits worth knowing

- Supported scope: EUR; Bank Transfer, Credit Card or SEPA Direct Debit; PAID
  with a date or UNPAID without one; zero overall discount and shipping.
  Anything else stops for review.
- Fakturama 2.2.0 crashes saving an Order while the Delivery address tab is
  selected; the automation selects the Invoice address tab first.
- The supplied image is 385x529 px: OCR reads the header, payment and totals
  but not the item table or street lines, so those rely on the extractor and
  the printed totals. A passing total check does not prove a name or SKU.
- Fakturama may show the contact's company in the delivery heading instead of
  the source's warehouse recipient. Reports verify the delivery street, ZIP,
  city and country separately from that heading.
- No accounting data is ever deleted and the Fakturama database is never
  edited; recovery from an uncertain Save is manual.

## Recording

Watch the [full live demo run of the supplied order image](https://drive.google.com/file/d/174enlL_cwoWCa_b4-dUBYlfU03Vu3B5w/view?usp=sharing).

## If I had three more hours

Two full runs reached a saved Order and a saved, paid, linked Invoice on real
Fakturama 2.2.0 ([live run](docs/live-run.md)): the supplied order on an empty
database, and a new customer with a new product through the guided runner,
including automatic Debtor and Product creation, line entry, the deep Invoice
check and the Documents-list check. What is not done, in the order I would
tackle it:

**Hour 1 - the remaining master-data branches.** VAT and payment-term creation
are still operator steps (the runner prints the exact instructions). Their
editors are bound only as far as field entry. I would automate "open, fill,
verify, Save once, re-read" for both with the same pattern as the Product, and
test each against an empty database, plus the mismatch cases (a conflicting
VAT value or payment code must stop, not be overwritten).

**Hour 2 - extraction and the image-only run.** The `extract` command (Claude
vision with a forced JSON schema, then decimal reconciliation) is implemented
but was not used live because no API key was available. The supplied image is
385x529 px, so local OCR reads the header, payment and totals but not the item
table or street lines; the live runs used a human-reviewed transcription
cross-checked by OCR. I would run extraction on the image, diff it against the
reviewed JSON, record the field-level accuracy, and capture one run that starts
from the image alone.

**Hour 3 - one command that owns the application.** Today I open Fakturama
by hand before each run and clean up after a stop (leftover error dialogs, a
half-filled editor, a tab that must be selected manually). I would make the
tool launch Fakturama itself: find the installed executable, start it, wait for
the main window, deal with the first-run workspace dialog, and restart it after
a crash or a Fakturama error dialog, so a run begins from a known state. On top
of that I would connect the stages behind the single `run` command with the
intent journal (no repeated Save after an uncertain result), and replace the
two flaky pieces I had to work around: tab selection (UIA Select is not
supported for these tabs, so there is an OCR fallback) and the Data > Documents
menu, which is currently a best-effort check.

**Also missing.** Nonzero shipping and order-level discounts stop for review; I
would implement them after checking Fakturama's rounding live. Only the
delivery postal fields are verified, because Fakturama shows the contact's
company in the delivery heading instead of the source's warehouse recipient; I
would decide and document the expected heading. The existing/missing/mixed/
duplicate scenarios are proven in the simulator but only some of them live; I
would run the rest against Fakturama and record them.
