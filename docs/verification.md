# Verification record - 1 October 2026

Environment: Linux, Python 3.12.14, Pydantic 2.13.5, Pillow 12.3.0, Tesseract 5.3.4. The target Windows/Python 3.11 environment has not yet been exercised.

| Check | Result | What it establishes |
| --- | --- | --- |
| `python -m unittest discover -s tests -q` | 82 tests passed | Pure rules, simulator orchestration, journal behavior, mocked extraction, UIA helpers, provided-order fixture, and diagnostics |
| `python -m compileall -q src tests tools` | Passed | Python syntax compilation on this runtime |
| Editable installation without dependency download | Passed | Package metadata and entrypoint import with already-installed core dependencies |
| Synthetic JSON validation | Passed; net 240.00, VAT 38.40, gross 278.40 | Deterministic two-line/two-rate Decimal reconciliation |
| Missing, existing, and mixed simulator CLI runs | All VERIFIED | Core branch sequencing against the simulator |
| Reserved live command | REVIEW_REQUIRED: live_adapter_not_implemented | No misleading live success or simulator fallback |
| UIA inspection on Linux | REVIEW_REQUIRED: windows_required_for_uia | Platform guard; no desktop mutations |
| Tesseract OCR on synthetic image | Text produced successfully | Actual local OCR subprocess and image sanitation exercised |
| Design PDF | Four A4 pages, all rendered and visually inspected | Part 1 format and layout |
| Supplied-image reviewed fixture | Net 570.00, VAT 108.30, gross 678.30 reconciled | Visually transcribed source values and business-rule arithmetic |
| Supplied-image fixture in simulator | VERIFIED; Product gross 297.50 and 47.60; distinct warehouse recipient preserved | Expected-source orchestration, not automatic image extraction or live Fakturama |
| Supplied-image local OCR (4x upscale for analysis) | Significant OCR errors | Actual 385x529 image is too unreliable for this plain OCR pass |

The local OCR output included a spurious underscore after the second SKU and a space in the email address. This is a concrete limitation: even readable synthetic inputs can contain plausible extraction errors. No assertion of exact OCR accuracy is made. Do not strip identifier characters heuristically or accept a candidate only because totals reconcile. Verify extracted facts against the supplied image.

Not tested: an installed live vision model/server, actual Fakturama controls, application-specific selector loading/readback, displayed state labels, real saved relationship/payment persistence, display scaling/resize, fresh Windows installation, or current vulnerability advisories. The actual image has been visually reviewed and OCR-tried, but no automated extractor has demonstrated exact source accuracy. The real desktop adapter and screenshots/recording remain unimplemented deliverables. No real Order or Invoice has been created.

The mocked vision test confirms fixed loopback routing, schema request, bounded response parsing, and rejecting unexpected tool calls. It does not evaluate a model's image understanding. The UIA tests use control doubles and do not establish accessibility support in Fakturama.
