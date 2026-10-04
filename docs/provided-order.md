# Supplied order image - reviewed expected values

Source: OrderImage.png supplied by the user, labelled synthetic test data. Dimensions: 385x529 pixels. This document and `examples/order.provided.expected.json` are a visually reviewed test oracle. Production extraction must read the input image; it must not return these constants or select a fixture by filename/hash.

| Field | Expected source value |
| --- | --- |
| External reference | WEB-2026-0714-A17 |
| Order date | 2026-07-14 |
| Source customer ID | CUST-1007 |
| Currency | EUR |
| Company | Northstar Office GmbH |
| Contact | Marta Klein |
| Alias | NORTHSTAR-BERLIN |
| Email | marta.klein@example.test |
| Telephone | +49 30 5550 1420 |
| Billing recipient | Northstar Office GmbH |
| Billing address | Friedrichstrasse 88, 10117 Berlin, Germany |
| Delivery recipient | Northstar Office Warehouse |
| Delivery address | Beusselstrasse 44, 10553 Berlin, Germany |
| Payment method/status | Bank Transfer / PAID |
| Payment date | 2026-07-18 |

| SKU | Description | Qty./unit | Unit net | Discount | VAT | Line net | Product master gross |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CHR-ERG-01 | Ergonomic Desk Chair | 2 pcs | 250.00 | 10% | 19% | 450.00 | 297.50 |
| MAT-DESK-02 | Anti-Fatigue Desk Mat | 3 pcs | 40.00 | 0% | 19% | 120.00 | 47.60 |

Total net 570.00; VAT 108.30; gross 678.30. Product master gross prices exclude transaction-line discounts. The PAID Invoice payment value must equal 678.30.

## Interpretation decisions

- Retain CUST-1007 as source reference data. When creating a Debtor, leave the app's proposed Customer ID unchanged as the assignment explicitly requires. Do not substitute CUST-1007.
- Contact is split into First Name Marta and Last Name Klein. No salutation is supplied; the new Debtor must use ---.
- Email/telephone appear in the customer/contact block. Assign them to the main billing contact; do not invent delivery contact details.
- Billing and delivery differ in recipient, street, and ZIP. Keep separate address roles, including the explicit warehouse recipient. The Debtor's Main address recipient is checked; for the delivery address Fakturama shows the contact's company in the heading, so only its street, ZIP, city and country are verified.
- The image supplies no order-level discount or shipping charge, and the printed totals reconcile exactly from the item lines. Use overall discount 0 and free shipping 0.00; do not introduce a charge.
- Both items use pcs. Retain this source unit; an application-specific unit mapping must be observed rather than guessed.
- Invoice number, Invoice Date, and Service date are application-proposed values, not fields to copy from the image. Preserve them. The copied Order Date remains 2026-07-14.

Local Tesseract OCR reads this small image's header band, customer block, payment row and printed totals (used as a cross-check by `ocr-verify`), but not the item table or street lines; those need a vision extraction or a human-reviewed transcription. Numerical agreement alone cannot detect a misspelled name, wrong SKU or wrong address.
