# Requirement coverage

"Live" means exercised on real Fakturama 2.2.0 with read-back verification
(see [live-run.md](live-run.md)). "Tested" means automated tests against
recorded UIA fixtures or doubles only.

| Brief step | Status |
| --- | --- |
| 1.1–1.2 Extract image data | Run live from the image with a local vision model (Ollama `qwen3-vl:4b-instruct`): schema, decimal reconciliation and a human confirmation before use; OCR cross-checks a reviewed JSON; Claude-vision `extract` implemented but not run |
| 1.3 Open New Order | Automated (`order-open`), live |
| 1.4–1.7 No. kept, Date, Cust.Ref., Net, With VAT | Automated, live (`order-prepare`) |
| 2.1–2.3 Debtor selector existence check | Exact-field matching on copied rows, one exact row selected with OK, none -> creation branch, several -> review; live |
| 2.5–2.9 New Debtor fields, roles, alias, Net, zero discount | `debtor-open-editor` + `debtor-complete-prepare` (Main, roles, delivery address, Miscellaneous); run live |
| 2.10 Payment method and terms-of-payment branch | `payment-term-check` (Data > terms of payment, exact name and zero defaults) and `payment-term-create` (one Save); Credit Card and SEPA Direct Debit created and confirmed live, then selected in the new Debtor's Payment dropdown |
| 2.11–2.13 Save Debtor, reselect from Order | `debtor-save` (one Save), `order-return`, then the selector check re-selects it; run live (PO000005) |
| 3.2–3.3 Product selector existence check | Exact SKU; Fakturama auto-adds a unique match and closes the dialog, which is detected; none -> creation branch; live |
| 3.4–3.6 VAT check/creation | `vat-check` (exact name and value from Data > VATs, then the existing row's editor is opened to require code S (Standard rate); run live on VAT 19% and 7%) and `vat-create` (one Save); VAT 7% was created and confirmed live; the runner then created the Product that uses it |
| 3.7–3.11 Product creation, gross price without line discount, Save once | Automated end-to-end, live (`product-open-editor`, `product-prepare`, `product-save`) |
| 3.12–3.17 Select Product, line Qty./U.Price/VAT/Discount/Price | Products are selected through the selector; `order-line-prepare` applies the extracted values and verifies the committed cells from two grid copies; run live |
| 4.1–4.4 Verify addresses and totals, order discount, zero/free shipping, Save once | Automated, live (`order-discount`, `order-finalize --save`); an order-level discount of 10% ran live, a nonzero shipping charge stops for review; both address pages are checked on postal fields; the delivery recipient heading differs in Fakturama and is reported separately |
| 4.5 Documents row | `documents-verify` checks the saved Order row (open state, date, reference, total); run live |
| 4.6–4.7 Invoice from follow-up area | Automated, live (`order-followup-invoice`) |
| 5.1 Proposed Invoice No./dates kept, copied values checked | `invoice-verify`: Order Date, both addresses, every item row and totals; run live |
| 5.2–5.3 Payment method, paid, payment date, Value | `invoice-pay`: inherited or exact method, `paid`, date and Value; run live for PAID and for UNPAID (`paid` left clear, no date or Value) |
| 5.4, 5.6 Save Invoice once and re-read | Automated, live (`invoice-pay --save`) |
| 5.5 Documents rows (Invoice paid, Order still open) | `documents-verify` after the Invoice Save; run live (paid Invoice `COMMAND_CHECKED`, unpaid `COMMAND_ERROR`) |
| 5.7 No Delivery/Correction/Dunning | No such action exists in the code |
| No fixed coordinates | UIA labels/structure and fresh OCR locate current controls; no stored screen coordinates |

Unknown hidden tests cannot be guaranteed. The suite covers the explicit brief
and representative failure scenarios; live acceptance on a fresh database for
every branch has not been completed (see "If I had three more hours" in the
[README](../README.md#if-i-had-three-more-hours)).
