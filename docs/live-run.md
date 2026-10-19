# Live run on Fakturama 2.2.0 (Windows, 2026-10-04)

The supplied order image was carried through to a **saved Order and a saved,
paid, linked Invoice** in a real Fakturama 2.2.0 installation, starting from
an empty database. Every value below was read back from the Fakturama editor
by the automation and cross-checked against Fakturama's own HSQLDB transaction
log (`Database/Database.log`, read-only; the database was never edited directly).

Source (reviewed extraction JSON, `examples/order.provided.expected.json`):
WEB-2026-0714-A17, 2026-07-14, Northstar Office GmbH / Marta Klein, two lines,
EUR 570.00 net + 108.30 VAT = 678.30, Bank Transfer, PAID on 2026-07-18.

## Persisted result

| Record | Persisted values |
| --- | --- |
| Debtor CUST000001 | Company Northstar Office GmbH, Marta Klein, alias NORTHSTAR-BERLIN; Main address Friedrichstrasse 88 10117 Berlin role BILLING; Northstar Office Warehouse, Beusselstrasse 44 10553 Berlin role DELIVERY; payment Bank Transfer |
| Payment method | Bank Transfer (Credit transfer code), zero discount/days |
| VAT | `VAT 19%`, value 0.19, code S |
| Product CHR-ERG-01 | Ergonomic Desk Chair, net 250.00 (gross 297.50 entered), VAT 19% |
| Product MAT-DESK-02 | Anti-Fatigue Desk Mat, net 40.00 (gross 47.60 entered), VAT 19% |
| Order PO000001 | Date 2026-07-14, Cust.Ref. WEB-2026-0714-A17, items CHR-ERG-01 qty 2 @250 −10%, MAT-DESK-02 qty 3 @40 0%, total 678.30, open, linked to the Invoice |
| Invoice INV000001 | Created from the Order's follow-up area; Cust.Ref. and order date 2026-07-14 copied; Bank Transfer; **paid**, pay date 2026-07-18, paid value 678.30; total 678.30 |

## Second run: new customer through the guided runner (PO000005 / INV000005)

`tools/run_order_first.py --input examples/order.new-customer.json --no-ocr-check`
(an invented customer, Harbor Books GmbH, with a separate delivery address and
a new product BKS-LAMP-03; there is no image for it, so the OCR cross-check is
skipped). Every step below was run by the tool with read-back verification and
one Save per record:

| Step | Result |
| --- | --- |
| Order header | New Order PO000005, Date, Cust.Ref., Net, With VAT |
| Debtor selector check | Empty result detected from the grid pixels, no Copy sent, Cancel invoked |
| Missing Debtor | New Debtor CUST000002 opened with the Order retained; Main fields, address roles, separate delivery address, Miscellaneous/payment filled; saved |
| Product selector check | Exact SKU; Fakturama auto-adds a unique match and closes the dialog |
| Missing Product | BKS-LAMP-03 created (gross 35.70) and re-selected from the same Order |
| Line Qty./Discount | Applied and verified from two stable copies of the item grid |
| Order | Verified against the source, saved once, row confirmed in Data > Documents (open) |
| Invoice | Created from the Order's follow-up area; copied Order Date, both addresses, the item and totals verified; paid status and date applied; saved once |
| Final check | Data > Documents shows the Invoice paid and the Order still open |

Not automated in this run: the VAT 19% record already existed and no new payment
term was needed (Bank Transfer already existed), so those two creation branches
were not exercised. The one live problem during this run was the Debtor's first
Save: the e-mail `.test` domain is rejected by Fakturama's validator, so it
persisted without Company/E-Mail; the existing record was corrected and saved
again (no duplicate), and the test file now uses an `example.com` address.

## Seventh run: order-level discount (PO000013 / INV000012)

`examples/order.order-discount.json`: Lakeside Stationers, BKS-LAMP-03 4 x 30.00 at 19%, 10% order discount, Credit
Card, PAID. Fakturama resets the overall Discount to the contact's value when the Debtor is selected, so it is set
after the lines and reads back as -10%. Totals: subtotal 120,00 (items before the discount), VAT 20,52, total
128,52; the Order and Invoice were saved and both Documents rows verified. One stop on the way: the line-grid
rectangle moved after the quantity commit; the line was already correct and the run was resumed at that line.

## Sixth run: new customer, new SEPA Direct Debit term, UNPAID (PO000012 / INV000011)

`examples/order.sepa-unpaid.json` (Rheinblick Verlag GmbH, BKS-NOTE-07 at 7%, SEPA Direct Debit, UNPAID). The
SEPA term was missing and was created with only the Order open, then the Debtor (CUST000004) was created and
selected from the Order. The Invoice was saved with `paid` clear and no date or Value; the saved Invoice was
re-read. In Data > Documents the Order is `COMMAND_ORDER_PENDING` (107.0) and the Invoice `COMMAND_ERROR` (107.0),
which is how Fakturama shows an unpaid Invoice. Two operator slips (an alt-tab, Fakturama closed before the Order
was saved) were recovered without duplicates; after the term Save the saved term editor stayed in front, so the
runner now returns to the Order after creating a term.

## Fifth run: new customer paying by Credit Card (PO000011 / INV000010)

`tools/run_order_first.py --input examples/order.credit-card.json --no-ocr-check --yes` (Lakeside Stationers GmbH,
existing product `BKS-LAMP-03`, Credit Card, PAID). The Credit Card term of payment did not exist:
`payment-term-create` created it (Name and Description `Credit Card`, payment code Credit card, zero discount and
days; the database row was checked) and `payment-term-check` then confirmed it in the list. The new Debtor was
opened after that, so its Payment dropdown offered the term; the fields, address roles and delivery address were
filled, the Debtor saved once and re-selected from the Order, and the Order, Invoice, payment and both Documents
checks followed.

Fakturama behaviour found on the way: a New Debtor editor reads its payment list once, when it opens, so a term
created afterwards does not appear; the Company field takes its value only from real key events (a UIA write
shows on screen but the saved contact keeps the old value, and Tab types a tab character into it), so Company
is now typed with real keystrokes; and the empty-grid detector had to learn each grid's header border, because
copying from an empty grid makes Fakturama throw. Two Debtors saved with an empty or mangled Company before this
was understood were corrected once each by a guarded one-off script.

## Fourth run: from the image alone (PO000010 / INV000009)

`tools/run_order_first.py --image examples/OrderImageProvided.png --provider ollama --model qwen3-vl:4b-instruct
--compare examples/order.provided.expected.json`. Fakturama was not open: the runner started it, maximised the
window and brought it to the front before every step. The local vision model read the clearer rendering of the
supplied order (615x847 px) in about ten seconds; the totals reconciled (570.00 / 108.30 / 678.30), the runner
printed the differences from the reviewed JSON (optional fields such as the customer ID, e-mail, phone and the
recipient names, and the unit in upper case) and the model's own notes, and waited for confirmation. Northstar
Office GmbH and both products already existed, so the selector checks selected them; the line values were
set and verified, the Order was saved once and confirmed in Data > Documents, the Invoice was created from the
follow-up area and verified (Order Date, both addresses, items, totals), paid and saved once, and the final
Documents check showed the Invoice paid and the Order open.

## Third run: new VAT and new product on an existing customer (PO000009 / INV000008)

`tools/run_order_first.py --input examples/order.reduced-vat.json --no-ocr-check --yes`
(Harbor Books GmbH, one line of `BKS-NOTE-07` at 7% VAT). The VAT 7% record was missing, so `vat-create`
created it (Name and Description `VAT 7%`, value 0.07, code S, saved once; the database log shows
`FKT_VAT ... 'VAT 7%' ... 0.07 ... 'S'` and `vat-check` then reports it). The run then created the Product, set
Qty. 3, verified the line, saved Order PO000009, created and verified the Invoice (copied Order Date, both
addresses, the item row and totals), applied payment, saved INV000008 once, and the final Data > Documents check
showed the Invoice paid and the Order open.

Problems found on the way, all fixed: opening the New TAX Rate editor moves keyboard focus (a real click on the
field is needed before typing); the Value field commits only when focus leaves it; and the empty-grid
detector (Select All/Copy on an empty grid makes Fakturama throw) first missed rows drawn white on a selection
colour, then counted faint grid lines as rows, so it now looks for a filled header bar and strong contrast only.

## First run: the supplied order on an empty database (PO000001 / INV000001)

The first run was operator-assisted for the selector searches, the Debtor
open/Save, the VAT check and the item-grid cells; everything else ran through
the guarded commands (`order-prepare`, `product-prepare`/`product-save`,
`order-finalize --save`, `order-followup-invoice`, `invoice-pay --save`). The
persisted records are in the table above.

## Issues found and handled

1. **Fakturama 2.2.0 save crash.** The first Order Save failed with
   `Failed to persist contents of part ... DocumentEditor.doSave` (NPE). From
   the Fakturama source, `doSave` runs the change-binding of the *selected*
   address tab, which exists only for the tab selected when the Debtor was set.
   Saving with the **Delivery address** tab selected crashes; nothing was
   persisted (confirmed in the DB log). With the **Invoice address** tab
   selected the Save succeeded. `order-finalize --save` now refuses to save
   while the billing address is not the visible tab.
2. **Contact confirmation popup** during Order Save (Fakturama shows the
   Debtor details). The automation now waits up to 90 s for the operator to
   answer it instead of reporting an unconfirmed save after 12 s.
3. **Already-saved protection.** Two later runs correctly refused a second
   Save (`order_already_saved_do_not_save_again`).
4. **Multi-line address values** were initially rejected by the strict value
   sanitiser; CR/LF/TAB are now allowed only for the finalize/invoice reads.
5. **Paid-date label** is `at` in the English UI; bound after one diagnostic
   capture. A safety rule prevents re-clicking `paid` if its fields are
   visible but the toggle reads clear.
6. **Product selector auto-add.** Fakturama closes the product dialog and adds
   the line itself when the search leaves exactly one product; the selector
   check detects that instead of looking for a result grid.
7. **Copy on an empty grid.** Select All/Copy on an empty result grid makes
   Fakturama throw `NullPointerException: copiedCells is null`. Emptiness is
   now established from the grid's pixels, and Copy is never sent to an empty grid.
8. **Modal dialogs and COMError.** UIA raises COMError when a modal closes
   inside Invoke, and for a filter bar rebuilt while typing; both are handled
   (accepted only once the dialog is really gone, state read before typing).
9. **Role popups need a real click.** The address-role popup opens on a
   click, not on UIA Invoke. Window discovery uses Win32 `EnumWindows`
   instead of a UIA desktop walk, which can hang on another application.

## Re-running

`tools/run_order_first.py --input <json>` walks the full Order-first flow in
brief order. `--resume-number`, `--resume-debtor` and `--order-saved` continue
a run that stopped, without repeating a Save. The only operator prompts left are
the VAT check and the confirmations before creating a Debtor or Product.
