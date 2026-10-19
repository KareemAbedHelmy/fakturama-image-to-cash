# Fakturama Image-to-Cash Automation: design

## 1. Scope and success criteria

Turn one order image into one saved Order and one linked Invoice in Fakturama
2.2.0 on Windows, through its user interface. Success means the persisted
records were read back and match the source, not that the clicks succeeded. The
Order must remain open; the Invoice must carry the source payment method and,
only for a PAID order, its payment date and full value. No Delivery, Correction
or Dunning document is ever created.

Supported scope: EUR; Bank Transfer, Credit Card or SEPA Direct Debit; PAID with
a date or UNPAID without one; an order-level discount percentage or none, and no shipping charge. Anything
else (another currency, a partial payment, a nonzero overall charge, unreadable
fields) stops for review. The design is a verified vertical slice first, then
the missing-master branches, then multi-line orders and recovery.

## 2. Architecture and extraction

Four layers, so business rules can be tested without Fakturama:

- **Extraction.** Local, bounded PNG/JPEG input (size, dimensions and frame
  checks, orientation applied, re-encoded to strip metadata). Tesseract OCR
  gives local text; an optional vision model (Claude, or a local Ollama model)
  returns data constrained by a JSON schema. Remote use is opt-in and sends only
  the order image. The supplied image is 385x529 px, so OCR reads the header,
  payment row and printed totals but not the item table; OCR is therefore used
  as an independent cross-check of a candidate extraction, not as the source of
  the item lines.
- **Validation.** Strict immutable schema (unknown fields, control characters,
  non-finite or negative amounts and impossible dates are rejected) and decimal
  finance: line net is quantity x unit net x (1 - discount%); VAT is aggregated
  by rate and rounded per rate; the Product master gross price is unit net x
  (1 + VAT%), rounded to cents, and never includes the line discount. Totals
  must reconcile with the image before anything is typed. A reconciled total
  does not prove a correct name or SKU, so a human confirms the extraction.
- **Workflow.** Order-first sequence with exact, unique matching (Company, First
  name, Name, ZIP, City for a Debtor; SKU for a Product). Missing records are
  created only after an exact selector check returns nothing.
- **Windows adapter.** Python with pywinauto (UI Automation), plus Win32 calls
  for window identity and the clipboard.

Image text is data, never instructions: the extractor has no tools and its output
is parsed against the schema before use.

## 3. Desktop grounding and the continuous workflow

The adapter binds to the Fakturama process found by its executable name, requires
it in the foreground, and re-checks identity before each action. Controls are
found by their UIA type, label and position in the tree, scoped to the current
editor or dialog, and must match exactly once. Fakturama's SWT item and result
grids expose no UIA rows or cells, so the adapter reads them with Select All /
Copy from a clipboard freshly owned by Fakturama (two identical copies), and
locates grid headers with OCR on a fresh capture of the control. No screen
coordinate is stored. UIA patterns (value, invoke, toggle) are preferred over
keystrokes; where a widget needs them (the segmented date field, a click to give
a grid or a popup focus) only fixed navigation keys or clicks computed from live
bounds are used.

1. Open New Order first, keep its proposed number, set Date, Cust.Ref., Net and
   With VAT.
2. In the Order's address selector, search the company and copy the result rows.
   One exact row is selected; none means the Debtor is missing; several stop the
   run. An empty grid is recognised from its pixels, because copying from an
   empty grid makes Fakturama throw.
3. For a missing Debtor, open New Contact while the Order stays open, keep the
   proposed customer ID, fill the fields, address roles, separate delivery
   address, alias, Net and the payment method, save once, return to the same
   Order and re-select the saved Debtor. A missing payment term is created first, before
   the Debtor editor is opened (that editor reads its payment list only once), and a missing VAT record before
   its Product, each with exact names and values and one Save.
4. For every line, search the Product selector by exact SKU. Fakturama closes the
   dialog and adds the line itself when exactly one product matches. A missing
   Product is created with the source SKU and description and the computed gross
   price, saved once, and re-selected from the same Order. Quantity, unit net and
   discount are then set and read back per line.
5. Verify both address pages, all lines and the totals. The Invoice address tab
   must be selected before saving: Fakturama 2.2.0 throws while saving an Order
   with the Delivery tab selected. Save once and confirm the Order in
   Data > Documents (open state, date, reference, total).
6. From the saved Order's follow-up area create the Invoice (never the toolbar).
   Verify the copied Order Date, both addresses, every item and the totals,
   apply the payment method, `paid`, the payment date and the Value for a PAID
   order, save once, and confirm in Data > Documents that the Invoice is paid and
   the Order is still open.

## 4. Recovery, security and validation

Every live command writes an intent file before acting and a report afterwards.
Save, Invoke and creation actions run once and are never retried: only
observations are retried. A stop raises a coded review error with the progress
flags, and recovery from an uncertain result is manual; accounting records are
never deleted or rolled back automatically. The simulator workflow additionally
uses a SQLite journal with a hashed business key and a single-writer lease to
block duplicate or altered replays.

Credentials come from the environment. Only the opt-in extraction call leaves the
machine. Fakturama's database is never written to; its log is read only to
cross-check persistence. See docs/security.md.

Unit tests cover decimal rounding, multi-rate and multi-line totals, invalid
extraction, matching conflicts and the payment mappings (Bank Transfer to Credit
transfer, Credit Card to Credit card, SEPA Direct Debit to SEPA direct debit).
Recorded UIA fixtures cover the Windows bindings, and a simulator runs
success and failure scenarios (missing, existing, mixed and duplicate masters, a
failure after the Order Save, a corrupted total). Simulator and fixture results
do not prove compatibility with Fakturama; that is shown by the live runs
recorded in docs/live-run.md.
