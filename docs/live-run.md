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
