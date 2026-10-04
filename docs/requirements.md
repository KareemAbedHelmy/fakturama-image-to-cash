# Requirement coverage

"Live" means exercised on real Fakturama 2.2.0 with read-back verification
(see [live-run.md](live-run.md)). "Tested" means automated tests against
recorded UIA fixtures or doubles only.

| Brief step | Status |
| --- | --- |
| 1.1–1.2 Extract image data | OCR cross-check live; Claude-vision `extract` implemented (needs API key); the live run used a reviewed transcription |
| 1.3 Open New Order | Automated (`order-open`), live |
| 1.4–1.7 No. kept, Date, Cust.Ref., Net, With VAT | Automated, live (`order-prepare`) |
| 2.1–2.3 Debtor selector existence check | Exact-field matching on copied rows, one exact row selected with OK, none -> creation branch, several -> review; live |
| 2.5–2.9 New Debtor fields, roles, alias, Net, zero discount | `debtor-open-editor` + `debtor-complete-prepare` (Main, roles, delivery address, Miscellaneous); run live |
| 2.1–2.3 Debtor selector existence check | Exact-field matching on copied rows, one exact row selected with OK, none -> creation branch, several -> review; live |
| 2.1–2.3 Debtor selector existence check | Exact-field matching on copied rows, one exact row selected with OK, none -> creation branch, several -> review; live |
| 3.2–3.3 Product selector existence check | Exact SKU; Fakturama auto-adds a unique match and closes the dialog, which is detected; none -> creation branch; live |
| 3.4–3.6 VAT check/creation | Operator (runner prompt); VAT 19% already existed in both runs |
| 3.7–3.11 Product creation, gross price without line discount, Save once | Automated end-to-end, live (`product-open-editor`, `product-prepare`, `product-save`) |
| 3.12–3.17 Select Product, line Qty./U.Price/VAT/Discount/Price | Runner selects Products, applies extracted transaction values via `order-line-prepare`, and verifies committed cells; both sample lines checked live, unsaved |
| 4.1–4.4 Verify addresses and totals, zero discount/shipping, Save once | Automated, live (`order-finalize --save`); both address pages are checked on postal fields; the delivery recipient heading differs in Fakturama and is reported separately |
| 4.5 Documents row | `documents-verify` checks the saved Order row (open state, date, reference, total); run live |
| 4.6–4.7 Invoice from follow-up area | Automated, live (`order-followup-invoice`) |
| 5.1 Proposed Invoice No./dates kept, copied values checked | `invoice-verify`: Order Date, both addresses, every item row and totals; run live |
| 5.2–5.3 Payment method, paid, payment date, Value | `invoice-pay`: inherited or exact method, `paid`, date and Value; run live |
| 5.4, 5.6 Save Invoice once and re-read | Automated, live (`invoice-pay --save`) |
| 5.5 Documents rows (Invoice paid, Order still open) | `documents-verify` after the Invoice Save; run live |
| 5.7 No Delivery/Correction/Dunning | No such action exists in the code |
| No fixed coordinates | UIA labels/structure and fresh OCR locate current controls; no stored screen coordinates |

Unknown hidden tests cannot be guaranteed. The suite covers the explicit brief
and representative failure scenarios; live acceptance on a fresh database for
every branch has not been completed (see "If I had three more hours" in the
[README](../README.md#if-i-had-three-more-hours)).
