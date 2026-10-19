# Fakturama Image-to-Cash Automation

Order-first automation of **Fakturama 2.2.0** (Windows) with Microsoft UI
Automation. From one order image it produces a saved Order and a saved, paid
Invoice linked to that Order, verifying the persisted records as it goes.

**Start here:** [Design document (PDF)](docs/design.pdf) explains the system
architecture and workflow. Its editable source is [design.md](docs/design.md).
For the end-to-end walkthrough, watch the [live demo recording](#recording).

## Status at a glance

Several runs on real Fakturama reached a saved Order and a saved, paid, linked
Invoice with the Order still open: the supplied order on an empty database
(Order PO000001 / Invoice INV000001, Bank Transfer, paid 2026-07-18, EUR
678.30) and a new customer with a new product through the guided runner
(PO000005 / INV000005), and the order read from the image by a local vision model (PO000010 / INV000009). See [the live run record](docs/live-run.md) and the
[recording](#recording).

| Area | State |
| --- | --- |
| Order header, Debtor/Product selector existence checks, line Qty./Discount, Order verify + Save, Documents check, follow-up Invoice, Invoice deep check, payment status, Invoice Save, final Documents check | Automated and run live |
| Missing Debtor (open, fill, roles, delivery address, payment, Save) and missing Product (open, fill, gross price, Save once) | Automated and run live (second run) |
| Missing VAT record | `vat-check` reads Data > VATs; `vat-create` fills Name, Description, Value (code S), saves once and the record is confirmed in the list. Run live for VAT 7%; the Product that uses it was then created, and the Order and a paid Invoice (PO000009 / INV000008) were saved |
| Missing payment term | `payment-term-check` reads Data > terms of payment; `payment-term-create` fills the term (Name and Description, matching payment code, zero defaults), saves once and the term is confirmed in the list. Run live for Credit Card (the database row was checked); the runner now creates it before opening the New Debtor editor, because that editor reads its payment list only when it opens. Then a new customer, a paid Credit Card Invoice and the Documents checks followed (PO000011 / INV000010) |
| Extraction from the image | Run live: the order image was read by a local vision model (Ollama, `qwen3-vl:4b-instruct`), checked and confirmed, and the run produced a saved Order and a paid Invoice (PO000010 / INV000009). OCR cross-checks a reviewed JSON when one is supplied |
| One unattended command | `tools/run_order_first.py` is guided: it pauses only for the VAT check and the confirmations before creating a Debtor or Product. The simulator `run` command is intentionally blocked |

What was skipped, and why, is in [If I had three more hours](#if-i-had-three-more-hours).
What changed since the submitted version is listed in [CHANGES.md](CHANGES.md).

## Setup

**Prerequisites**

| Needed for | Requirement |
| --- | --- |
| Everything | Python 3.11 or later |
| Live automation | Windows 10/11, [Fakturama 2.2.0](https://www.fakturama.info/downloads/) with the **English** user interface, installed and started once so its workspace exists |
| OCR cross-check | [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) installed at `C:\Program Files\Tesseract-OCR	esseract.exe` |
| LLM extraction (optional) | `ANTHROPIC_API_KEY` for Claude, or a running local Ollama server with a downloaded vision model |

**Install** (PowerShell, from the folder that contains `pyproject.toml`):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -q   # 370+ tests, no Fakturama needed
```

Dependencies are pinned: Pydantic 2.13.5, Pillow 12.3.0 and, for the live
flow, pywinauto 0.6.9. Transitive dependencies are not locked or audited.

## Run the automation against Fakturama

1. Make sure Fakturama 2.2.0 is installed (the default folder under
   `C:/Program Files` is found automatically; otherwise set the `FAKTURAMA_EXE`
   environment variable to the full path of `Fakturama.exe`). You do not need to
   open it: the runner starts it if it is not running, maximises the window (or
   sets the size you choose with `--window`), and brings it to the front before
   every step. A workspace with existing records exercises the
   existing-records branches, an empty one the create-missing-records branches.
2. Start the guided flow from PowerShell:

```powershell
.\.venv\Scripts\python.exe tools\run_order_first.py --image examples/order.provided.png --input examples/order.provided.expected.json
```

3. Leave the mouse and keyboard alone. If Fakturama was already open, press
   Enter at the "Prepare" prompt once no New Order, New Debtor or New product
   editors are open. The run stops at the first step it cannot verify and
   prints the report path (`private/run-<timestamp>/NN-<step>.json`); it never
   retries a Save. Answer the prompts: confirm before a missing Debtor or
   Product is created, and do the VAT check by hand when asked.

| Option | Use |
| --- | --- |
| `--input FILE` | Reviewed order JSON; OCR cross-checks it against `--image` |
| (no `--input`) | Extract from the image with Claude by default; you confirm the result |
| `--provider ollama --model qwen3-vl:4b-instruct` | Use a downloaded local Ollama vision model instead of Claude; no Anthropic key needed |
| `--no-ocr-check` | Test orders that have no matching image, e.g. `examples/order.new-customer.json` |
| `--resume-number PO0000NN` | Continue with an already open, header-prepared New Order |
| `--resume-debtor CUSTNNNNNN` | Continue with an already open, unsaved New Debtor |
| `--resume-line N` | Debtor and products up to line N are already in the open Order; continue with that line's Qty./Discount |
| `--window maximize` | Window size: `maximize` (default) or `WIDTHxHEIGHT`, for example `1920x1080` |
| `--no-launch` | Do not start or resize Fakturama; use the window that is already open |
| `--order-saved` | The Order is already saved; continue with the Documents check and the Invoice |
| `--debtor-done` | The Debtor is already selected in the open Order; continue with the products |

When a step stops, the runner prints a "How to resume" line for that stage. Two rules matter: selecting a product
adds a line to the Order every time, so for a stop in the product stage count the Order's lines first
(`--resume-line N` if line N is already there, otherwise the flag one line earlier); and `--order-saved` invokes
the follow-up Invoice again, so check Data > Documents for an existing Invoice before using it.

Every live command is also available on its own, for example
`python -m fakturama_automation.cli selector-check --help`.

For local image extraction, [install Ollama for Windows](https://docs.ollama.com/windows).
For local-only operation, set `OLLAMA_NO_CLOUD=1` in your Windows user
environment and restart Ollama ([Ollama FAQ](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)).
Then download a local vision model once:

```powershell
ollama pull qwen3-vl:4b-instruct
.\.venv\Scripts\python.exe -m fakturama_automation.cli extract --provider ollama --model qwen3-vl:4b-instruct --image examples/order.provided.png --out private/ollama-extracted.json
```

Use the explicit `-instruct` tag: Ollama's `qwen3-vl:4b` tag selects the
thinking variant, which can exhaust the response budget before returning JSON.

The guided runner can use the same provider with
`--image examples/order.provided.png --provider ollama --model qwen3-vl:4b-instruct`
and no `--input`. Review every extracted field against the image before the
desktop steps; the model output is untrusted candidate data. The supplied
385×529 image failed strict schema validation with this local model in our
sample check, including misread financial fields. Use the reviewed `--input`
file for that image, or supply a clearer source image. A failed extraction
stops before any Fakturama action.

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

## Extraction from the image

`extract` turns an order image into the strict JSON the automation uses. It works with a local vision
model (Ollama, no API key, nothing leaves the machine) or with Claude vision (`ANTHROPIC_API_KEY`). The
output is normalised (a trailing `%` is dropped, a repeated or excessive `review_issues` list is trimmed),
its totals must reconcile, and the guided runner shows it for your confirmation before anything is typed.

```powershell
.\.venv\Scripts\python.exe tools\run_order_first.py --image examples/OrderImageProvided.png --provider ollama --model qwen3-vl:4b-instruct --compare examples/order.provided.expected.json
```

Measured with `qwen3-vl:4b-instruct` through Ollama (`tools/extraction_report.py` compares an extraction with a
reviewed JSON field by field):

| Image | Size | Essential fields | All fields | Time |
| --- | --- | --- | --- | --- |
| `examples/order.synthetic.png` | 1600x1750 | 37/37 | 47/48 (98%) | 28 s |
| `examples/OrderImageProvided.png` (a clearer rendering of the supplied order) | 615x847 | 37/37 | 46/53 (87%) | 11 s |
| `examples/order.demo-new-2.png` (a new form of the same layout, never used to tune the prompt) | 615x847 | 36/37 | 45/53 (85%) | 17 s |
| `examples/order.provided.png` (the supplied image) | 385x529 | stops: the answer fails the schema | | |

Essential fields are the order date and reference, payment, totals, the Debtor's company and names, both
addresses and every item's SKU, quantity, price, discount and VAT. The misses are optional detail fields that
the small model skipped (customer ID, e-mail, phone, address recipient names) or wrote in another case (`PCS`
for `pcs`). On the supplied 385x529 image the item text is about 6 px tall, so the local model cannot read it
and the run stops before touching Fakturama. A larger model such as `qwen3-vl:8b` should recover most of the
optional fields.

The never-seen image (`tools/make_form_image.py` renders it from `examples/order.demo-new-2.json`, which is the
ground truth) showed a real slip: the model wrote the delivery recipient line into the street field and dropped the
street. For that reason the runner now runs the OCR cross-check on the extracted data before asking for
confirmation, and the cross-check reads both address blocks (street, ZIP and city as one run); on that image it
flags `delivery_address`. OCR is an independent reader, not the source of the data, and the confirmation is still
the safeguard.

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
  with a date or UNPAID without one; an overall discount percentage (set after the Debtor and lines are in)
  or none; no shipping charge. Anything else stops for review.
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

Several full runs reached a saved Order and a saved, paid, linked Invoice on real Fakturama 2.2.0
([live run](docs/live-run.md)): the supplied order on an empty database, a new customer with a new product,
a new VAT rate, a new payment term, and an order read straight from the image by a local vision model. What is not done, in the
order I would tackle it:

**Hour 1 - the remaining checks and a cleaner reset.** Credit Card and SEPA Direct Debit terms were created live,
and an UNPAID order ran end to end (its Invoice shows as `COMMAND_ERROR` in Data > Documents). An order-level discount also ran live
(10%, PO000013 / INV000012). I would also verify every typed field in the saved record, not only in the editor, because
Fakturama's Company field kept the old value after a UIA write.

**Hour 2 - a second, deterministic extractor and wider measurements.** The local model read all essential
fields of the clearer image, but it skips optional ones (customer ID, e-mail, phone, recipient names) and it
cannot read the 385x529 original, where the item text is about 6 px tall. I would add a template reader
(Tesseract with label anchors, validated by patterns) as an independent second extractor, compare it field by
field with the model's answer and show only the disagreements, and measure more images and a larger model.

**Hour 3 - a run that recovers on its own.** The runner starts Fakturama, sizes the window and brings it to
the front, but a run that stops still leaves the application in whatever state it was in (a leftover error
dialog, a half-filled editor, a tab that must be selected by hand). I would detect and dismiss known Fakturama
error dialogs, close or restart a crashed instance and handle the first-run workspace dialog, so a run begins
from a known state. I would also connect the stages behind the single `run` command with the intent journal
(no repeated Save after an uncertain result) and replace the two flaky pieces I had to work around: tab
selection (UIA Select is not supported for these tabs, so there is an OCR fallback) and the Data > Documents
menu, which is currently a best-effort check.

**Also missing.** A nonzero shipping charge stops for review: shipping is a separate master record
(Data > Shippings) with its own VAT, which the brief gives no procedure for, so I would add its existence check and
creation the way the VAT and payment-term branches work. Only the delivery postal fields are verified, because Fakturama shows the
contact's company in the delivery heading instead of the source's warehouse recipient; I would decide and
document the expected heading.
