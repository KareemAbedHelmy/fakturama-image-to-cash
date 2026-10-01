# Requirement coverage and acceptance plan

Status describes observed work. “Core tested” means automated tests against a simulator or pure functions. It does not mean a Windows UI control has been exercised. Only the PDF brief was supplied; the order image is still needed.

| Assignment rule | Implementation/test | Live acceptance still needed |
| --- | --- | --- |
| Single image input; extract every source field | Image sanitation, local OCR, schema-constrained local vision client; response tests mocked | Employer image, model accuracy, verified end-to-end image entrypoint |
| New Order first; retain proposed number | Workflow sequence and generated handle tested | Toolbar grounding, number readback |
| Date, Cust.Ref., Net, With VAT | Snapshot comparison and simulator configuration | Native control identities and value/mode readback |
| Retain same Order while creating masters | Original handle checked before each action; tab-loss test | Tab selection under nested editors/modal dialogs |
| Upper existing-contact selector, not green + | Separate selector and creation APIs; branch tests | Inspect icon identities and parent scopes |
| Exact Company/First Name/Name/ZIP/City | Deterministic matching; duplicate/conflict tests | Full stable, scrollable selector readout |
| Address comparison after selecting | Complete billing/delivery comparison tested | UI address blocks, formatting, country locale |
| New Debtor fields and automatic customer ID | Source contract and simulator master copy | Real editor fields, preserving ID; --- salutation when absent |
| Address roles, alias, Net, zero discount | Separate addresses preserved in core | Role toggles and additional delivery address UI |
| Debtor payment dropdown, then terms branch | Available/reuse/create/conflict cases tested | Dropdown refresh and terms list definition inspection |
| Exact payment mappings and zero defaults | Mapping and definition conflict tests | Description/account/texts/default flag and one toolbar save |
| Reselect saved Debtor from Order | Missing-save/selector tests | Persisted selector row and populated addresses |
| Exact SKU for every line, preserve row order | Existing/missing/mixed/repeated SKU tests | Scroll/search behavior, complete table readout |
| VAT before missing Product; exact name/value/S | Creation order and conflicting/duplicate VAT tests | E-invoice code, description, untouched Standard VAT |
| Product gross excludes transaction discount | Decimal calculation and master-price tests | All zero/blank fields and gross-price save readback |
| Reselect saved Product | Missing-product test | Product search after save |
| Qty., unit net, VAT, discount, line net | Snapshot comparison, multi-line and rate tests | Grid editing patterns and cell readback |
| Overall discount/shipping | Nonzero values explicitly stop for review | Implement provided nonzero values after matching app semantics |
| Net/VAT/gross totals match image and UI | Decimal validation, mismatch and rounding tests | Actual Fakturama rounding policy; default shipping label |
| Save Order once and verify Documents row | Single-save and missing-row tests | Number/date/reference/open state/total row lookup |
| Invoice from saved Order's follow-up area | Follow-up-only port method and linked snapshot tests | Reopen saved Order, scoped follow-up action |
| Preserve Invoice number/date/service date | Before/after/saved checks | Proposed UI dates and true persisted relationship |
| Exact Invoice payment method and paid fields | Paid/unpaid/missing-method/corrupted-value tests | Date/value controls, locale, complete saved payment readback |
| Verify saved Invoice and still-open source Order | Both persisted rows and saved Invoice checks | Actual paid/unpaid state labels and source Order remains open |
| No Delivery/Correction/Dunning | No such actions in workflow or port | Evidence recording of final stopping point |
| No fixed coordinates/layout assumptions | Exact scoped UIA primitives tested; no coordinate implementation | Actual resize/DPI/layout and inaccessible-control fallback |
| Git repo, README, setup | Local Git repository and instructions | Fresh Windows installation and hosted repo submission |
| Annotated screenshots or recording | Evidence checklist only | Genuine live Fakturama evidence, not simulator output |

Unknown hidden tests cannot be guaranteed. The contract covers the explicit brief and representative failure scenarios; live acceptance and further adversarial testing remain necessary.
