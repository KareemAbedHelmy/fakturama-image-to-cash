# Windows validation and evidence

## First session

1. Install the official Windows Fakturama distribution in a separate synthetic-data workspace. Record version, app language, Windows version, display scaling, and the configured document currency.
2. Install the Python package with its windows extra. Run the automated tests on the user's actual Python 3.11 installation.
3. Find the Fakturama process ID under Task Manager > Details. Use `windows --pid` to identify its actual UI window handle. If a Java subprocess owns the window, use that PID rather than assuming the launcher owns it.
4. Open a New Order manually. Run `inspect --pid ... --window-handle ... --out private/order-uia.json`. Inspect accessible names, automation IDs, control types, parent scopes, and supported patterns. Do not invent IDs from the screenshots in the brief.
5. Capture read-only trees for each selector and editor. Modal dialogs may have their own top-level handle; list process windows again while they are open. Keep the original Order tab open.
6. Use the supplied OrderImage.png and the reviewed test transcription in `examples/order.provided.expected.json`. Compare OCR/vision output field-by-field to the image. Source customer ID is reference data: preserve Fakturama's automatically proposed new Debtor ID. Preserve the distinct delivery recipient rather than copying the Debtor company onto the warehouse address.

## Adapter implementation priorities

Implement `DesktopPort` in vertical slices, with every returned value derived from observed UI state. First existing Debtor/existing Products; then missing Debtor/payment; then missing VAT/Product; then multi-line and separate-address branches. Each action uses fresh scoped discovery, a bounded wait, and readback. Stable empty selector results require an observed completed search; virtualized tables require full result enumeration. No direct database shortcut, invented control ID, global Invoice action, or blind save retry.

For saved records, Documents rows may not expose all detail fields. Read the relevant row and reopen the saved editor as needed to construct an observed Snapshot. Do not fill observed fields by copying expected source values. Verify order/invoice linkage from the saved application, not merely because a follow-up button was clicked.

## Acceptance runs

| Scenario | Expected outcome |
| --- | --- |
| All masters already exist | No new Debtor/Product/VAT/payment; one Order and linked Invoice |
| All missing | Create only necessary masters; same open Order; one save each |
| Existing and missing across multiple lines | Source order preserved; no repeated master creation |
| Repeated SKU with different transaction prices/discounts | Separate lines; one unchanged Product master |
| Duplicate exact Debtor/Product/VAT/payment rows | Review stop before selecting/creating conflict |
| Existing Debtor address differs from source | Review stop; don't overwrite master silently |
| Billing differs from delivery | Separate verified address roles; both copied to Invoice |
| PAID / UNPAID | Paid date/full total only for PAID; no invented unpaid payment |
| Unsupported currency/status/charges | Explicit review; no mutation before validation |
| Resize, moved window, display scaling change | Same semantic controls found or clear review stop |
| Search loading, modal interruption, missing save row | Wait/review; no guess or extra save |
| Failure immediately after Order/Invoice save | Persisted record may exist; journal blocks replay |
| App rounding differs from initial model | Stop on mismatch; characterize policy before continuing |
| Repeat same image/reference | No duplicate Order or Invoice |

## Evidence to record

Use synthetic/authorized data. Show input and reviewed values, the open Order number, address/product selector lookup, any creation branch, completed lines/totals, saved Order row, follow-up Invoice action, copied data and relationship, payment settings, saved Invoice row, and still-open Order row. Prefer a short narrated recording; annotate real screenshots with stage, observed document number, and verification. Actual evidence must come from Fakturama. Simulator output and the synthetic source image do not satisfy this deliverable.

Do not publish images, journals, or UI trees containing personal information or credentials. Keep the input filename/data out of the public repo unless specifically authorized.
