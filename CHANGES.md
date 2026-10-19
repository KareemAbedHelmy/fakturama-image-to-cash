# Changes since the submitted version

Base: commit `f29928d` (tagged `submission`). To see everything that changed:

```powershell
git log --oneline submission..HEAD
git diff --stat submission
```

The submitted version already ran the guided flow end to end with the missing-Debtor and Product branches
and left VAT and payment-term creation to the operator. This round adds the following.

## New

| Area | What | Where |
| --- | --- | --- |
| Starts the application | Starts Fakturama if it is not running, maximises it (or sets a size) and brings it to the front before every step | `launcher.py`; `--window`, `--no-launch` in `tools/run_order_first.py` |
| Extraction from the image | Local vision model through Ollama (no API key); percent signs and padded review notes are cleaned up, field-mapping guidance added to the prompt; accuracy report | `extraction.py`, `tools/extraction_report.py`, `--provider`/`--model` |
| VAT records | `vat-check` reads Data > VATs and `vat-create` creates a missing rate (one Save) | `vat_ui.py`, `master_list_ui.py` |
| Payment terms | `payment-term-check` and `payment-term-create` (one Save); created before the Debtor editor is opened, because that editor reads its payment list once | `payment_term_ui.py`, `payment_ui.py` |
| Runner options | `--yes`, `--resume-number`, `--resume-debtor`, `--resume-line`, `--debtor-done`, `--order-saved`, `--no-ocr-check` | `tools/run_order_first.py` |
| Single steps | Run one guarded command against the open window | `tools/live_cmd.py` |
| Test data | A 7% VAT order, a Credit Card order, a SEPA Direct Debit UNPAID order, a 10% order-discount order, two new-customer/product/VAT orders for the demo, a clearer rendering of the supplied order | `examples/` |

## Fixed (Fakturama behaviour found while running it)

- Select All / Copy on an **empty grid** makes Fakturama throw. Emptiness is now judged from pixels (header bar
  plus its border, selected rows and faint grid lines handled), and only after the grid has stopped changing
  (`selector_live_ui.py`, `master_list_ui.py`).
- The Debtor's **Company** is saved only from real key events: a UIA write shows on screen but the saved contact
  keeps the old value, and Tab types a tab character. Company is now typed with escaped literal keys
  (`keyboard_text.py`, `debtor_live_ui.py`, `debtor_flow_ui.py`).
- The item grid has no scrollbar when the window is wide, which broke its detection (`order_items_ui.py`).
- A New Debtor editor reads its payment list only when it opens; the term is therefore created first.

## Tests and docs

- 399 tests (new: `test_launcher.py`, `test_vat_ui.py`, `test_runner_ollama.py`, grid and extraction cases, two
  captured grid images in `tests/fixtures/`).
- `vat-check` opens an existing VAT row and requires the code S (Standard rate) before reusing it (brief 3.5); the row is found from the grid's rules and the editor's Name is verified before it is closed unchanged.
- Order-level discount: set by `order-discount` after the Debtor and lines (selecting a contact resets it), read back as a negative percentage, totals computed per VAT rate; the Order and Invoice subtotal is the item sum before the discount. Run live: PO000013 / INV000012.
- `tools/make_form_image.py` renders a styled order image from a JSON (used to make `examples/order.demo-new-2.png`, an image the model had never seen). On it the model wrote the delivery recipient into the street field; the OCR cross-check now also reads both address blocks and the runner runs it after extraction, before the confirmation.
- Found by a break-it exercise: transparent PNG backgrounds were flattened to black (now composited onto white, with a test); the OCR cross-check's soft threshold for names and alias was too loose (0.75 -> 0.9).
- The runner prints a "How to resume" line for the stage that stopped (found when a recovery rehearsal resumed with the wrong flag and added a duplicate product line; the line check stopped it before any Save).
- Run six (SEPA Direct Debit term created live, UNPAID order: PO000012 / INV000011); Documents check maps the unpaid Invoice state `COMMAND_ERROR`.
- README rewritten (setup, run options, extraction results, three-more-hours answer), `docs/live-run.md` gains
  runs three to five, `docs/requirements.md` and `docs/design.md` updated to match.

## Still not done

A nonzero shipping charge stops for review (it is a separate master record the brief gives no procedure for); there
is no second, rule-based extractor to cross-check the model; the delivery recipient heading is not verified.
