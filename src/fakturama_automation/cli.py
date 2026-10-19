"""Command line: extraction and validation, the simulator demo and every guarded live Fakturama step."""

import argparse
import io
import json
import re
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path

from .domain import OrderInput, fingerprint
from .diagnostics import capture_bundle
from .debtor_ui import inspect_debtor_payment_options
from .debtor_live_ui import prepare_debtor_page
from .debtor_address_ui import prepare_billing_role, prepare_delivery
from .product_live_ui import prepare_product
from .order_items_ui import prepare_order_line
from .order_finalize_ui import apply_order_discount, create_followup_invoice, finalize_order
from .product_create_ui import open_new_order, open_new_product, save_product
from .master_navigation_ui import open_new_debtor, return_to_order
from .invoice_verify_ui import verify_invoice_deep
from .vat_ui import check_vat, create_vat
from .payment_term_ui import check_payment, create_payment
from .debtor_flow_ui import prepare_new_debtor, save_new_debtor
from .documents_ui import verify_documents
from .selector_live_ui import check_selector
from .invoice_ui import pay_invoice
from .errors import ReviewRequired
from .ocr_verify import verify_against_image
from .extraction import CLAUDE_DEFAULT_MODEL, extract_claude_vision, extract_local_vision, local_ocr, sanitize_image
from .finance import validate_order
from .input_io import read_order, write_private
from .journal import Journal
from .matching import PAYMENT_CODES
from .order_ui import inspect_order_header
from .order_live_ui import prepare_order_header
from .payment_ui import inspect_payment_editor, prepare_payment_editor
from .payment_list_ui import capture_payment_list
from .payment_copy_ui import capture_payment_headers, copy_payment_table, payment_header_bundle
from .payment_search_ui import search_payment_list
from .payment_link_ui import link_debtor_payment
from .combo_ui import ComboReadRequired
from .native_ui import SELECTOR_TITLES, inspect_native_selector
from .selector_vision import capture_selector_grid, inspect_selector_image, ocr_words
from .simulator import Simulator, seed_simulator
from .demo import run_reviewer_demo
from .uia import discover_fakturama_windows, inspect_window, list_process_windows, window_inventory
from .workflow import Workflow


def parser():
    p = argparse.ArgumentParser(description="Fakturama guarded core (live desktop integration pending)")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="Print the extraction JSON schema")
    validate = commands.add_parser("validate", help="Validate candidate JSON and financial totals")
    validate.add_argument("input", type=Path)
    simulate = commands.add_parser("simulate", help="Run only the in-memory simulator; never writes Fakturama")
    simulate.add_argument("input", type=Path)
    simulate.add_argument("--seed", choices=["missing", "existing", "mixed"], default="missing")
    simulate.add_argument("--journal", type=Path, default=Path("runs/simulator.sqlite"))
    simulate.add_argument("--workspace", default="simulator-only")
    demo = commands.add_parser("demo", help="Run reviewer scenarios and produce reports; SIMULATOR ONLY")
    demo.add_argument("input", type=Path, help="Reviewed candidate JSON; never automatic image extraction")
    demo.add_argument("--reviewed-input", action="store_true", required=True,
                      help="Acknowledge that the input was checked against its source")
    demo.add_argument("--out-dir", type=Path, required=True, help="New private output directory; no overwrite")
    journal = commands.add_parser("journal", help="Read journal state; no reset or retry")
    journal.add_argument("path", type=Path)
    extract = commands.add_parser("extract", help="Vision extraction (Claude API or local Ollama) to untrusted candidate JSON")
    extract.add_argument("--image", type=Path, required=True)
    extract.add_argument("--provider", choices=["claude", "ollama"], default="claude",
                         help="claude: Anthropic API (ANTHROPIC_API_KEY, image leaves the machine); ollama: local server")
    extract.add_argument("--model", help=f"claude default {CLAUDE_DEFAULT_MODEL}; ollama: an installed LOCAL vision model")
    extract.add_argument("--out", type=Path, required=True)
    ocr = commands.add_parser("ocr", help="Produce local OCR text, without inferring structured order data")
    ocr.add_argument("--image", type=Path, required=True)
    ocr.add_argument("--tesseract", type=Path, required=True)
    ocr.add_argument("--language", default="eng")
    ocr.add_argument("--out", type=Path, required=True)
    ocrv = commands.add_parser("ocr-verify", help="OCR cross-check of reviewed/extracted JSON against the order image")
    ocrv.add_argument("--image", type=Path, required=True)
    ocrv.add_argument("--input", type=Path, required=True)
    ocrv.add_argument("--tesseract", type=Path, default=Path("C:/Program Files/Tesseract-OCR/tesseract.exe"))
    ocrv.add_argument("--out", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="Read-only Windows UIA tree dump; may contain private data")
    inspect.add_argument("--pid", type=int, required=True)
    inspect.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    inspect.add_argument("--out", type=Path, required=True)
    header = commands.add_parser("header", help="Read-only observed New Order header; not a live workflow")
    header.add_argument("--pid", type=int, required=True)
    header.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    header.add_argument("--currency", required=True, help="Expected ISO currency from the reviewed source")
    order_prepare = commands.add_parser("order-prepare", help="Prepare retained live Order header from reviewed JSON; NEVER saves")
    order_prepare.add_argument("--input", type=Path, required=True)
    order_prepare.add_argument("--reviewed-input", action="store_true")
    order_prepare.add_argument("--pid", type=int, required=True)
    order_prepare.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    order_prepare.add_argument("--expected-number", required=True)
    order_prepare.add_argument("--out", type=Path, required=True)
    native = commands.add_parser("native-selector", help="Read-only Win32 control tree of an observed selector")
    native.add_argument("--pid", type=int, required=True)
    native.add_argument("--title", choices=sorted(SELECTOR_TITLES), required=True)
    native.add_argument("--out", type=Path, required=True)
    debtor = commands.add_parser("debtor-options", help="Read-only observed native New Debtor Payment dropdown entries")
    debtor.add_argument("--pid", type=int, required=True)
    debtor.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    debtor.add_argument("--out", type=Path, required=True)
    debtor_prepare = commands.add_parser("debtor-prepare", help="Prepare selected live Debtor Main/Misc page from reviewed JSON; NEVER saves")
    debtor_prepare.add_argument("--input", type=Path, required=True)
    debtor_prepare.add_argument("--reviewed-input", action="store_true")
    debtor_prepare.add_argument("--pid", type=int, required=True)
    debtor_prepare.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    debtor_prepare.add_argument("--expected-id", required=True)
    debtor_prepare.add_argument("--page", choices=["main", "misc"], required=True)
    debtor_prepare.add_argument("--out", type=Path, required=True)
    billing_role = commands.add_parser("debtor-billing-role", help="Set Main address roles in the operator-opened popup; NEVER saves")
    billing_role.add_argument("--input", type=Path, required=True)
    billing_role.add_argument("--reviewed-input", action="store_true")
    billing_role.add_argument("--pid", type=int, required=True)
    billing_role.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    billing_role.add_argument("--expected-id", required=True)
    billing_role.add_argument("--out", type=Path, required=True)
    delivery = commands.add_parser("debtor-delivery-prepare", help="Fill the selected additional address and set Delivery role; NEVER saves")
    delivery.add_argument("--input", type=Path, required=True)
    delivery.add_argument("--reviewed-input", action="store_true")
    delivery.add_argument("--pid", type=int, required=True)
    delivery.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    delivery.add_argument("--expected-id", required=True)
    delivery.add_argument("--manual-role-popup", action="store_true",
                          help="Verify already-prepared fields and select role in an operator-opened popup; no field writes")
    delivery.add_argument("--out", type=Path, required=True)
    product = commands.add_parser("product-prepare", help="Fill selected New product from reviewed source SKU; NEVER saves")
    product.add_argument("--input", type=Path, required=True)
    product.add_argument("--reviewed-input", action="store_true")
    product.add_argument("--reviewed-vat", action="store_true", help="Confirm exact VAT name, percentage and S code were checked in VATs")
    product.add_argument("--pid", type=int, required=True)
    product.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    product.add_argument("--sku", required=True)
    product.add_argument("--out", type=Path, required=True)
    finalize = commands.add_parser("order-finalize", help="Part 4: verify the completed live Order against reviewed JSON; --save invokes Save ONCE")
    discount = commands.add_parser("order-discount", help="Set the Order's overall Discount from the reviewed JSON after Debtor and lines; never saves")
    followup = commands.add_parser("order-followup-invoice", help="Part 4.6: invoke the saved Order's follow-up Invoice ONCE; Invoice is not saved")
    line = commands.add_parser("order-line-prepare", help="Fill and verify extracted Qty./U.Price/Discount on the appended Order line; never saves")
    line.add_argument("--line-index", type=int, required=True, help="1-based source row, including repeated SKUs")
    line.add_argument("--tesseract", type=Path, default=Path("C:/Program Files/Tesseract-OCR/tesseract.exe"))
    for live_doc in (finalize, followup, line, discount):
        live_doc.add_argument("--input", type=Path, required=True)
        live_doc.add_argument("--reviewed-input", action="store_true")
        live_doc.add_argument("--pid", type=int, required=True)
        live_doc.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
        live_doc.add_argument("--expected-number", required=True)
        live_doc.add_argument("--out", type=Path, required=True)
    product_open = commands.add_parser("product-open-editor", help="Step 3.7: invoke toolbar 'Create a new product' ONCE")
    product_save = commands.add_parser("product-save", help="Step 3.11: verify New product fields against source SKU, Save ONCE")
    vat_check = commands.add_parser("vat-check", help="3.4: read Data > VATs; report whether the VAT of --sku's rate exists (read-only)")
    vat_create = commands.add_parser("vat-create", help="3.5: create the missing VAT of --sku's rate and Save ONCE")
    pay_check = commands.add_parser("payment-term-check", help="2.10: read Data > terms of payment; report whether the source payment method exists (read-only)")
    pay_create = commands.add_parser("payment-term-create", help="2.10: create the missing term of payment (needs the Order and New Debtor open) and Save ONCE")
    debtor_open = commands.add_parser("debtor-open-editor", help="Open New Debtor while retaining the source Order")
    debtor_complete = commands.add_parser("debtor-complete-prepare", help="Fill New Debtor main/delivery/misc fields and roles; never Save")
    debtor_save = commands.add_parser("debtor-save", help="Save the prepared New Debtor ONCE; never retried")
    invoice_verify = commands.add_parser("invoice-verify", help="5.1: read-only check of the linked Invoice's copied addresses, Order Date, items and totals")
    invoice_verify.add_argument("--tesseract", type=Path, default=Path("C:/Program Files/Tesseract-OCR/tesseract.exe"))
    launch = commands.add_parser("launch", help="Start Fakturama if needed, size its window and bring it to the front")
    launch.add_argument("--window", default="maximize", help="maximize (default) or WIDTHxHEIGHT, e.g. 1920x1080")
    order_return = commands.add_parser("order-return", help="Select and verify the retained unsaved Order")
    documents = commands.add_parser("documents-verify", help="Verify saved Order and optional Invoice in Data > Documents")
    for navigation in (debtor_open, order_return):
        navigation.add_argument("--input", type=Path, required=True)
        navigation.add_argument("--reviewed-input", action="store_true")
        navigation.add_argument("--pid", type=int, required=True)
        navigation.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
        navigation.add_argument("--expected-number", required=True)
        navigation.add_argument("--out", type=Path, required=True)
    for debtor_cmd in (debtor_complete, debtor_save):
        debtor_cmd.add_argument("--input", type=Path, required=True)
        debtor_cmd.add_argument("--reviewed-input", action="store_true")
        debtor_cmd.add_argument("--pid", type=int, required=True)
        debtor_cmd.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
        debtor_cmd.add_argument("--expected-id", required=True)
        debtor_cmd.add_argument("--out", type=Path, required=True)
    documents.add_argument("--input", type=Path, required=True)
    documents.add_argument("--reviewed-input", action="store_true")
    documents.add_argument("--pid", type=int, required=True)
    documents.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    documents.add_argument("--expected-number", required=True)
    documents.add_argument("--invoice-number")
    documents.add_argument("--invoice-date", help="Saved Invoice Date as observed in its editor")
    documents.add_argument("--out", type=Path, required=True)
    for live_product in (product_open, product_save, vat_check, vat_create):
        live_product.add_argument("--input", type=Path, required=True)
        live_product.add_argument("--reviewed-input", action="store_true")
        live_product.add_argument("--pid", type=int, required=True)
        live_product.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
        live_product.add_argument("--sku", required=True)
        live_product.add_argument("--out", type=Path, required=True)
    invoice_pay = commands.add_parser("invoice-pay", help="Part 5: apply extracted payment status on the linked Invoice; --save saves ONCE")
    invoice_pay.add_argument("--input", type=Path, required=True)
    invoice_pay.add_argument("--reviewed-input", action="store_true")
    invoice_pay.add_argument("--pid", type=int, required=True)
    invoice_pay.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    invoice_pay.add_argument("--out", type=Path, required=True)
    invoice_pay.add_argument("--save", action="store_true")
    selector = commands.add_parser("selector-check", help="2.1-2.3 / 3.2-3.3: Order selector as existence check; OK on one exact row, else Cancel")
    selector.add_argument("--kind", choices=["debtor", "product"], required=True)
    selector.add_argument("--sku", help="Required for --kind product")
    order_open = commands.add_parser("order-open", help="1.3: toolbar 'Create: New Order' once; reports the proposed No.")
    for live_cmd in (selector, order_open, invoice_verify, pay_check, pay_create):
        live_cmd.add_argument("--input", type=Path, required=True)
        live_cmd.add_argument("--reviewed-input", action="store_true")
        live_cmd.add_argument("--pid", type=int, required=True)
        live_cmd.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
        live_cmd.add_argument("--out", type=Path, required=True)
    finalize.add_argument("--save", action="store_true", help="After all checks pass, invoke toolbar Save exactly once")
    payment = commands.add_parser("payment-editor", help="Read-only new payment editor fields and native code options")
    payment.add_argument("--pid", type=int, required=True)
    payment.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    payment.add_argument("--out", type=Path, required=True)
    prepare = commands.add_parser("payment-prepare", help="Fill an explicitly scoped blank payment form; NEVER saves")
    prepare.add_argument("--pid", type=int, required=True)
    prepare.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    prepare.add_argument("--method", choices=sorted(PAYMENT_CODES), required=True)
    prepare.add_argument("--out", type=Path, required=True)
    payment_list = commands.add_parser("payment-list", help="Read-only expanded payment list screenshot, OCR and UIA/Win32 evidence ZIP")
    payment_list.add_argument("--pid", type=int, required=True)
    payment_list.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    payment_list.add_argument("--tesseract", type=Path, required=True)
    payment_list.add_argument("--out", type=Path, required=True)
    payment_copy = commands.add_parser("payment-copy", help="Select All/Copy twice from the focused payment table; never saves")
    payment_copy.add_argument("--pid", type=int, required=True)
    payment_copy.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    payment_copy.add_argument("--method", choices=sorted(PAYMENT_CODES), required=True)
    payment_copy.add_argument("--tesseract", type=Path, required=True)
    payment_copy.add_argument("--out", type=Path, required=True)
    payment_headers = commands.add_parser("payment-headers", help="Read-only exact payment table/panel image and header OCR diagnostic ZIP")
    payment_headers.add_argument("--pid", type=int, required=True)
    payment_headers.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    payment_headers.add_argument("--tesseract", type=Path, required=True)
    payment_headers.add_argument("--out", type=Path, required=True)
    payment_search = commands.add_parser("payment-search", help="Set literal payment list Search and capture twice; NEVER saves")
    payment_search.add_argument("--pid", type=int, required=True)
    payment_search.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    payment_search.add_argument("--method", choices=sorted(PAYMENT_CODES), required=True)
    payment_search.add_argument("--tesseract", type=Path, required=True)
    payment_search.add_argument("--out", type=Path, required=True)
    link = commands.add_parser("payment-link-debtor", help="Verify reopened payment and select it in retained Debtor; NEVER saves")
    link.add_argument("--pid", type=int, required=True)
    link.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    link.add_argument("--method", choices=sorted(PAYMENT_CODES), required=True)
    link.add_argument("--expected-standard", required=True)
    link.add_argument("--expected-id", required=True)
    link.add_argument("--manual-tabs", action="store_true", help="Wait for operator tab clicks; never calls UIA Select")
    link.add_argument("--out", type=Path, required=True)
    visual = commands.add_parser("selector-image", help="Read-only local OCR evidence from a selector screenshot")
    visual.add_argument("--image", type=Path, required=True)
    visual.add_argument("--title", choices=sorted(SELECTOR_TITLES), required=True)
    visual.add_argument("--tesseract", type=Path, required=True)
    visual.add_argument("--out", type=Path, required=True)
    screen = commands.add_parser("selector-screen", help="Read-only foreground selector table screenshot and OCR evidence ZIP")
    screen.add_argument("--pid", type=int, required=True)
    screen.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    screen.add_argument("--title", choices=sorted(SELECTOR_TITLES), required=True)
    screen.add_argument("--tesseract", type=Path, required=True)
    screen.add_argument("--out", type=Path, required=True)
    windows = commands.add_parser("windows", help="List windows in an explicit Windows process (read-only)")
    windows.add_argument("--pid", type=int, required=True)
    discover = commands.add_parser("discover", help="List visible Fakturama windows read-only; no PID needed")
    discover.add_argument("--all", action="store_true", help="Diagnostic: include other visible window titles and rejection reasons")
    capture = commands.add_parser("capture", help="Create a read-only Fakturama UIA diagnostic ZIP; no clicks or saves")
    capture.add_argument("--label", required=True)
    capture.add_argument("--out-dir", type=Path, default=Path("private/ui"))
    capture.add_argument("--pid", type=int, help="Explicit operator-selected process for read-only capture; bypass auto-discovery")
    live = commands.add_parser("run", help="Reserved live command; blocked until the desktop adapter is implemented")
    live.add_argument("--image", type=Path, required=True)
    return p


def execute(args):
    if args.command == "schema":
        print(json.dumps(OrderInput.model_json_schema(), indent=2))
    elif args.command == "validate":
        expected = validate_order(read_order(args.input))
        print(json.dumps({"status": "VALIDATED_CANDIDATE", "totals": expected.model_dump(mode="json"),
                          "note": "Check the candidate against the source image; validation is not extraction accuracy."}))
    elif args.command == "simulate":
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", args.workspace):
            raise ReviewRequired("invalid_workspace_id")
        source = read_order(args.input)
        validate_order(source)
        sim = Simulator()
        seed_simulator(sim, source, args.seed)
        with Journal(args.journal) as journal:
            result = Workflow(sim, journal, args.workspace).run(source)
        print(json.dumps({"backend": "SIMULATOR_ONLY", "result": asdict(result),
                          "calls": sim.calls, "note": "No Fakturama records were created."}, indent=2))
    elif args.command == "demo":
        report = run_reviewer_demo(args.input, args.out_dir, reviewed_input=args.reviewed_input)
        print(json.dumps({"status": report["status"], "backend": report["backend"],
                          "scenario_count": len(report["scenarios"]),
                          "report": str(args.out_dir / "report.html"),
                          "note": "Reviewed JSON input. Simulator results only; no Fakturama records created."}))
    elif args.command == "journal":
        if not args.path.is_file():
            raise ReviewRequired("journal_not_found")
        with Journal(args.path) as journal:
            print(json.dumps(journal.summary(), indent=2))
    elif args.command == "extract":
        clean, image_hash = sanitize_image(args.image)
        if args.provider == "claude":
            import os
            candidate = extract_claude_vision(clean, args.model or CLAUDE_DEFAULT_MODEL,
                                              api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        else:
            if not args.model:
                raise ReviewRequired("ollama_model_required")
            candidate = extract_local_vision(clean, args.model)
        write_private(args.out, candidate.model_dump_json(indent=2))
        print(json.dumps({"status": "CANDIDATE_WRITTEN", "image_sha256": image_hash,
                          "review_issue_count": len(candidate.review_issues),
                          "note": "Review every field against the source image before desktop use."}))
    elif args.command == "ocr":
        clean, _ = sanitize_image(args.image)
        write_private(args.out, local_ocr(clean, args.tesseract, args.language))
        print(json.dumps({"status": "OCR_TEXT_WRITTEN"}))
    elif args.command == "ocr-verify":
        clean, image_hash = sanitize_image(args.image)
        report = verify_against_image(clean, read_order(args.input), args.tesseract)
        report["image_sha256"] = image_hash
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report["status"], "critical_mismatches": report["critical_mismatches"],
                          "soft_mismatches": report["soft_mismatches"], "report": str(args.out)}))
        if report["critical_mismatches"]:
            raise ReviewRequired("ocr_critical_mismatch")
    elif args.command == "inspect":
        if args.pid <= 0 or args.window_handle <= 0:
            raise ReviewRequired("invalid_window_identity")
        tree = inspect_window(args.pid, args.window_handle)
        write_private(args.out, json.dumps(tree, ensure_ascii=False, indent=2))
        print(json.dumps({"status": "UIA_DUMP_WRITTEN", "note": "Inspect and redact private data before sharing."}))
    elif args.command == "header":
        if args.pid <= 0 or args.window_handle <= 0:
            raise ReviewRequired("invalid_window_identity")
        print(json.dumps(inspect_order_header(args.pid, args.window_handle, args.currency), indent=2))
    elif args.command == "launch":
        from .launcher import prepare
        print(json.dumps(prepare(args.window), indent=2))
    elif args.command == "native-selector":
        report = inspect_native_selector(args.pid, args.title)
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": "READ_ONLY_NATIVE_DUMP_WRITTEN", "truncated": report["truncated"]}))
    elif args.command == "selector-image":
        clean, image_hash = sanitize_image(args.image)
        report = inspect_selector_image(clean, args.title, args.tesseract)
        report["image_sha256"] = image_hash
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": "UNVERIFIED_VISUAL_EVIDENCE_WRITTEN",
                          "note": "OCR candidates only; no verified master absence or desktop actions."}))
    elif args.command == "debtor-options":
        report = inspect_debtor_payment_options(args.pid, args.window_handle)
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        if report.get("status") == "REVIEW_REQUIRED":
            raise ReviewRequired(report["code"])
        print(json.dumps({"status": "READ_ONLY_DEBTOR_OPTIONS_WRITTEN",
                          "note": "No dropdown opened or selected. Review private content before sharing."}))
    elif args.command == "selector-screen":
        clean, context = capture_selector_grid(args.pid, args.window_handle, args.title)
        report = inspect_selector_image(clean, args.title, args.tesseract)
        report["capture_context"] = context
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("grid.png", clean)
            archive.writestr("evidence.json", json.dumps(report, ensure_ascii=False, indent=2))
        write_private(args.out, bundle.getvalue())
        print(json.dumps({"status": "UNVERIFIED_VISUAL_EVIDENCE_WRITTEN",
                          "note": "Read-only screenshot/OCR ZIP. Review private content before sharing."}))
    elif args.command == "payment-editor":
        report = inspect_payment_editor(args.pid, args.window_handle)
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        if report.get("status") == "REVIEW_REQUIRED":
            raise ReviewRequired(report["code"])
        print(json.dumps({"status": "READ_ONLY_PAYMENT_EDITOR_WRITTEN",
                          "note": "No values changed, code selected or term saved. Review private content before sharing."}))
    elif args.command == "product-prepare":
        if not args.reviewed_input or not args.reviewed_vat:
            raise ReviewRequired("product_prepare_source_and_vat_review_required")
        source = read_order(args.input)
        validate_order(source)
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("product_prepare_output_exists")
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "PRODUCT_PREPARATION_INTENT",
            "pid": args.pid, "window_handle": args.window_handle, "sku": args.sku,
            "input_sha256": fingerprint(source), "vat_definition_operator_reviewed": True,
            "save_enabled": False}, indent=2))
        diagnostics = {}
        try:
            report = prepare_product(args.pid, args.window_handle, source, args.sku, diagnostics=diagnostics)
        except Exception as exc:
            code = exc.code if isinstance(exc, ReviewRequired) else "product_prepare_unexpected_uncertain"
            report = {"status": "REVIEW_REQUIRED", "code": code, **diagnostics,
                "note": "Inspect retained Product before another attempt. No Save invoked; no automatic retry."}
            if isinstance(exc, ComboReadRequired): report['combo_diagnostic'] = exc.diagnostic
            write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
            raise ReviewRequired(code) from None
        report['progress'] = diagnostics['progress']
        report['vat_definition_verification'] = 'OPERATOR_REVIEWED_NOT_AUTOMATICALLY_VERIFIED'
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report['status'], "report": str(args.out),
            "note": "Actual Product fields and VAT label read back. Product remains unsaved; Order selection and line entry are pending."}))
    elif args.command == "debtor-delivery-prepare":
        if not args.reviewed_input:
            raise ReviewRequired("delivery_prepare_reviewed_input_required")
        source = read_order(args.input)
        validate_order(source)
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("delivery_prepare_output_exists")
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "DELIVERY_ADDRESS_PREPARATION_INTENT",
            "pid": args.pid, "window_handle": args.window_handle,
            "expected_id": args.expected_id, "input_sha256": fingerprint(source),
            "manual_role_popup": args.manual_role_popup,
            "save_enabled": False}, indent=2))
        diagnostics = {}
        try:
            report = prepare_delivery(args.pid, args.window_handle, source,
                args.expected_id, diagnostics=diagnostics, manual_role_popup=args.manual_role_popup)
        except Exception as exc:
            code = exc.code if isinstance(exc, ReviewRequired) else "delivery_prepare_unexpected_uncertain"
            report = {"status": "REVIEW_REQUIRED", "code": code, **diagnostics,
                "note": "Additional address fields or checkboxes may have changed. Inspect before another attempt. No Save invoked; no automatic retry."}
            if isinstance(exc, ComboReadRequired):
                report['combo_diagnostic'] = exc.diagnostic
            write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
            raise ReviewRequired(code) from None
        report['progress'] = diagnostics['progress']
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report['status'], "report": str(args.out),
            "note": "Actual additional-address fields and Delivery-only role read back. Debtor remains unsaved; Order linking and document recipient are pending."}))
    elif args.command == "debtor-billing-role":
        if not args.reviewed_input:
            raise ReviewRequired("billing_role_reviewed_input_required")
        source = read_order(args.input)
        validate_order(source)
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("billing_role_output_exists")
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "BILLING_ROLE_SELECTION_INTENT",
            "pid": args.pid, "window_handle": args.window_handle,
            "expected_id": args.expected_id, "input_sha256": fingerprint(source),
            "save_enabled": False}, indent=2))
        diagnostics = {}
        try:
            report = prepare_billing_role(args.pid, args.window_handle, source,
                args.expected_id, diagnostics=diagnostics)
        except Exception as exc:
            code = exc.code if isinstance(exc, ReviewRequired) else "billing_role_unexpected_uncertain"
            write_private(args.out, json.dumps({"status": "REVIEW_REQUIRED", "code": code,
                **diagnostics, "note": "Checkboxes may have changed. Inspect Main address before another attempt. No Save invoked; no automatic retry."},
                ensure_ascii=False, indent=2))
            raise ReviewRequired(code) from None
        report['progress'] = diagnostics['progress']
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report['status'], "report": str(args.out),
            "note": "Actual Main address checkbox states read back. Debtor remains unsaved; delivery address and Order linking are pending."}))
    elif args.command == "debtor-prepare":
        if not args.reviewed_input:
            raise ReviewRequired("debtor_prepare_reviewed_input_required")
        source = read_order(args.input)
        validate_order(source)
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("debtor_prepare_output_exists")
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "DEBTOR_PAGE_PREPARATION_INTENT",
            "pid": args.pid, "window_handle": args.window_handle,
            "expected_id": args.expected_id, "page": args.page,
            "input_sha256": fingerprint(source), "save_enabled": False}, indent=2))
        diagnostics = {}
        try:
            report = prepare_debtor_page(args.pid, args.window_handle, source,
                args.expected_id, args.page, diagnostics=diagnostics)
        except Exception as exc:
            code = exc.code if isinstance(exc, ReviewRequired) else "debtor_prepare_unexpected_uncertain"
            report = {"status": "REVIEW_REQUIRED", "code": code, **diagnostics,
                "note": "Inspect the retained unsaved Debtor before another attempt. No Save invoked; attempted writes/selections are recorded in progress."}
            if isinstance(exc, ComboReadRequired):
                report['combo_diagnostic'] = exc.diagnostic
            write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
            raise ReviewRequired(code) from None
        report['progress'] = diagnostics['progress']
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report['status'], "report": str(args.out),
            "note": "Actual Fakturama page fields prepared/read back. Debtor remains unsaved; lookup, address roles, delivery and Order linking are pending."}))
    elif args.command == "order-prepare":
        if not args.reviewed_input:
            raise ReviewRequired("order_prepare_reviewed_input_required")
        source = read_order(args.input)
        validate_order(source)
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("order_prepare_output_exists")
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "ORDER_HEADER_PREPARATION_INTENT",
            "pid": args.pid, "window_handle": args.window_handle,
            "expected_number": args.expected_number, "input_sha256": fingerprint(source),
            "save_enabled": False}, indent=2))
        diagnostics = {}
        try:
            report = prepare_order_header(args.pid, args.window_handle, source,
                                          args.expected_number, diagnostics=diagnostics)
        except Exception as exc:
            code = exc.code if isinstance(exc, ReviewRequired) else "order_prepare_unexpected_uncertain"
            report = {"status": "REVIEW_REQUIRED", "code": code, **diagnostics,
                "note": "Inspect the retained unsaved Order before another attempt. No Save invoked; any attempted writes are listed in progress."}
            if isinstance(exc, ComboReadRequired):
                report["combo_diagnostic"] = exc.diagnostic
            write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
            raise ReviewRequired(code) from None
        report["progress"] = diagnostics["progress"]
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report["status"], "report": str(args.out),
                          "note": "Actual Fakturama header values changed/read back. Order remains unsaved; customer/items/invoice are pending."}))
    elif args.command == "payment-prepare":
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("payment_preparation_output_exists")
        # Exclusive, fsynced intent BEFORE native writes; final evidence is also
        # exclusive. An interrupted attempt retains its intent marker.
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "PAYMENT_PREPARATION_INTENT",
                                         "pid": args.pid, "window_handle": args.window_handle,
                                         "method": args.method, "save_enabled": False}, indent=2))
        try:
            report = prepare_payment_editor(args.pid, args.window_handle, args.method)
        except ReviewRequired as exc:
            report = exc.report() if isinstance(exc, ComboReadRequired) else {
                "status": "REVIEW_REQUIRED", "code": exc.code}
            report["note"] = "Preparation stopped; inspect the unsaved form before another attempt. No Save or Set as standard action was invoked."
            write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
            raise
        except Exception:
            write_private(args.out, json.dumps({"status": "REVIEW_REQUIRED",
                "code": "payment_preparation_unexpected_uncertain",
                "note": "Inspect the unsaved form before another attempt. No Save or Set as standard action was invoked."}, indent=2))
            raise ReviewRequired("payment_preparation_unexpected_uncertain") from None
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": "PAYMENT_EDITOR_PREPARED_UNSAVED",
                          "note": "Fields prepared and read back; term remains unsaved and Standard was not changed."}))
    elif args.command == "payment-list":
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("payment_list_output_exists")
        clean, report = capture_payment_list(args.pid, args.window_handle)
        try:
            words, size = ocr_words(clean, args.tesseract)
            report["ocr"] = {"status": "UNVERIFIED_OCR_CANDIDATES",
                             "image_size": list(size), "words": [asdict(w) for w in words]}
        except ReviewRequired as exc:
            report["ocr"] = {"status": "REVIEW_REQUIRED", "code": exc.code}
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("payment-list.png", clean)
            archive.writestr("evidence.json", json.dumps(report, ensure_ascii=False, indent=2))
        write_private(args.out, bundle.getvalue())
        print(json.dumps({"status": "READ_ONLY_PAYMENT_LIST_EVIDENCE_WRITTEN",
                          "ocr_status": report["ocr"]["status"],
                          "note": "Screenshot and UIA/Win32 evidence only. No values changed or terms saved. Review private content before sharing."}))
    elif args.command == "payment-link-debtor":
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("payment_link_output_exists")
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "DEBTOR_PAYMENT_LINK_INTENT", "pid": args.pid,
            "window_handle": args.window_handle, "method": args.method,
            "expected_id": args.expected_id, "expected_standard": args.expected_standard,
            "tab_mode": "manual" if args.manual_tabs else "uia", "save_enabled": False}, indent=2))
        diagnostics = {}
        try:
            report = link_debtor_payment(args.pid, args.window_handle, args.method,
                                        args.expected_standard, args.expected_id, diagnostics=diagnostics,
                                        manual_tabs=args.manual_tabs,
                                        notify=lambda event: print(json.dumps(event), flush=True))
        except Exception as exc:
            code = exc.code if isinstance(exc, ReviewRequired) else "payment_link_unexpected_failure"
            report = exc.report() if isinstance(exc, ComboReadRequired) else {"status": "REVIEW_REQUIRED", "code": code}
            report.update(diagnostics)
            report["note"] = "Tabs or Payment may have changed. No Save invoked. Inspect the unsaved Debtor before another attempt; no automatic retry."
            write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
            raise ReviewRequired(code) from None
        report["progress"] = diagnostics["progress"]
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report["status"], "generated_id": report["generated_id"],
                          "note": "Payment selected and read back; Debtor remains unsaved. Generated ID and other Miscellaneous values preserved."}))
    elif args.command == "payment-search":
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("payment_search_output_exists")
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "PAYMENT_SEARCH_INTENT", "pid": args.pid,
            "window_handle": args.window_handle, "method": args.method, "save_enabled": False}, indent=2))
        diagnostics = {}
        try:
            report = search_payment_list(args.pid, args.window_handle, args.method, args.tesseract,
                                         diagnostics=diagnostics)
        except Exception as exc:
            code = exc.code if isinstance(exc, ReviewRequired) else "payment_search_unexpected_failure"
            report = {"status": "REVIEW_REQUIRED", "code": code,
                      "progress": diagnostics.get("progress", {}), "scope_rechecked": False,
                      "master_absence_verified": False, "persistence_verified": False,
                      "note": "Search may have changed; no editor-field write or Save was invoked. No retry or automatic filter restoration."}
            write_private(args.out, payment_header_bundle(diagnostics.get("frames", []), report))
            raise ReviewRequired(code) from None
        report["progress"] = diagnostics["progress"]
        write_private(args.out, payment_header_bundle(diagnostics["frames"], report))
        print(json.dumps({"status": report["status"], "stable_frame_count": report["stable_frame_count"],
                          "note": "Search set and read back; filtered image evidence written. Term remains unsaved; absence is unverified. Review private content before sharing."}))
    elif args.command == "payment-headers":
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("payment_headers_output_exists")
        frames, report = capture_payment_headers(args.pid, args.window_handle, args.tesseract)
        write_private(args.out, payment_header_bundle(frames, report))
        print(json.dumps({"status": report["status"],
                          "grid_status": frames[0]["evidence"]["status"],
                          "grid_code": frames[0]["evidence"].get("code"),
                          "note": "Read-only images/OCR written. No keyboard, clipboard or Save actions. Review private content before sharing."}))
    elif args.command == "payment-copy":
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("payment_copy_output_exists")
        diagnostic_out = args.out.with_name(args.out.stem + "-headers.zip")
        if diagnostic_out.exists() or diagnostic_out.is_symlink():
            raise ReviewRequired("payment_copy_diagnostic_output_exists")
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": "PAYMENT_COPY_INTENT", "pid": args.pid,
                                         "window_handle": args.window_handle, "method": args.method,
                                         "save_enabled": False}, indent=2))
        diagnostics = {}

        def failure_report(code):
            report = {"status": "REVIEW_REQUIRED", "code": code,
                      "progress": diagnostics.get("progress", {}),
                      "note": "Copy stopped; selection or clipboard may have changed. No field write or Save was invoked."}
            if diagnostics.get("frames"):
                diagnostic = {**report, "status": "UNVERIFIED_PAYMENT_HEADER_DIAGNOSTICS",
                              "scope_rechecked": False,
                              "note": "Captured header pixels/OCR for diagnosis only; a failed run does not verify stable scope or complete rows."}
                write_private(diagnostic_out, payment_header_bundle(diagnostics["frames"], diagnostic))
                report["diagnostic_bundle"] = str(diagnostic_out)
            write_private(args.out, json.dumps(report, indent=2))

        try:
            report = copy_payment_table(args.pid, args.window_handle, args.method, args.tesseract,
                                       diagnostics=diagnostics)
        except ReviewRequired as exc:
            failure_report(exc.code)
            raise
        except Exception:
            failure_report("payment_copy_unexpected_failure")
            raise ReviewRequired("payment_copy_unexpected_failure") from None
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": "PAYMENT_TABLE_COPIED_NOT_SAVED", "row_count": len(report["rows"]),
                          "lookup_result": report["lookup_result"],
                          "note": "Two fresh copies read. No fields changed or terms saved. Review private content before sharing."}))
    elif args.command in {"order-finalize", "order-discount", "order-followup-invoice", "product-open-editor", "product-save", "invoice-pay",
                          "selector-check", "order-open", "order-line-prepare", "debtor-open-editor", "order-return",
                          "documents-verify", "debtor-complete-prepare", "debtor-save", "invoice-verify",
                          "vat-check", "vat-create", "payment-term-check", "payment-term-create"}:
        if not args.reviewed_input:
            raise ReviewRequired("live_document_reviewed_input_required")
        source = read_order(args.input)
        validate_order(source)
        if args.out.exists() or args.out.is_symlink():
            raise ReviewRequired("live_document_output_exists")
        mutates = (args.command not in {"order-finalize", "documents-verify", "invoice-verify", "vat-check", "payment-term-check"}
                   or getattr(args, "save", False))
        intent = args.out.with_name(args.out.name + ".intent.json")
        write_private(intent, json.dumps({"status": args.command.upper().replace("-", "_") + "_INTENT",
            "pid": args.pid, "window_handle": args.window_handle,
            "target": (getattr(args, "expected_number", None)
                       or getattr(args, "expected_id", None) or getattr(args, "sku", None)),
            "input_sha256": fingerprint(source), "mutating_action_enabled": mutates}, indent=2))
        diagnostics = {}
        try:
            if args.command == "order-line-prepare":
                report = prepare_order_line(args.pid, args.window_handle, source, args.expected_number,
                                            args.line_index, args.tesseract, diagnostics=diagnostics)
            elif args.command == "order-discount":
                report = apply_order_discount(args.pid, args.window_handle, source, args.expected_number,
                                              diagnostics=diagnostics)
            elif args.command == "order-finalize":
                report = finalize_order(args.pid, args.window_handle, source, args.expected_number,
                                        save=args.save, diagnostics=diagnostics)
            elif args.command == "selector-check":
                if args.kind == "product" and not args.sku:
                    raise ReviewRequired("selector_sku_required")
                query = args.sku if args.kind == "product" else source.debtor.company
                report = check_selector(args.pid, args.window_handle, source, args.kind, query, diagnostics=diagnostics)
            elif args.command == "order-open":
                report = open_new_order(args.pid, args.window_handle, diagnostics=diagnostics)
            elif args.command == "debtor-open-editor":
                report = open_new_debtor(args.pid, args.window_handle, source, args.expected_number,
                                         diagnostics=diagnostics)
            elif args.command == "debtor-complete-prepare":
                report = prepare_new_debtor(args.pid, args.window_handle, source, args.expected_id,
                                            diagnostics=diagnostics)
            elif args.command == "payment-term-check":
                report = check_payment(args.pid, args.window_handle, source, diagnostics=diagnostics)
            elif args.command == "payment-term-create":
                report = create_payment(args.pid, args.window_handle, source, diagnostics=diagnostics)
            elif args.command == "vat-check":
                report = check_vat(args.pid, args.window_handle, source, args.sku, diagnostics=diagnostics)
            elif args.command == "vat-create":
                report = create_vat(args.pid, args.window_handle, source, args.sku, diagnostics=diagnostics)
            elif args.command == "invoice-verify":
                report = verify_invoice_deep(args.pid, args.window_handle, source, args.tesseract,
                                             diagnostics=diagnostics)
            elif args.command == "debtor-save":
                report = save_new_debtor(args.pid, args.window_handle, source, args.expected_id,
                                         diagnostics=diagnostics)
            elif args.command == "order-return":
                report = return_to_order(args.pid, args.window_handle, source, args.expected_number,
                                         diagnostics=diagnostics)
            elif args.command == "documents-verify":
                report = verify_documents(args.pid, args.window_handle, source, args.expected_number,
                                          invoice_number=args.invoice_number, invoice_date=args.invoice_date,
                                          diagnostics=diagnostics)
            elif args.command == "invoice-pay":
                report = pay_invoice(args.pid, args.window_handle, source, save=args.save, diagnostics=diagnostics)
            elif args.command == "product-open-editor":
                report = open_new_product(args.pid, args.window_handle, source, args.sku, diagnostics=diagnostics)
            elif args.command == "product-save":
                report = save_product(args.pid, args.window_handle, source, args.sku, diagnostics=diagnostics)
            else:
                report = create_followup_invoice(args.pid, args.window_handle, source, args.expected_number,
                                                 diagnostics=diagnostics)
        except Exception as exc:
            code = exc.code if isinstance(exc, ReviewRequired) else "live_document_unexpected_uncertain"
            report = {"status": "REVIEW_REQUIRED", "code": code, **diagnostics,
                "note": "Inspect Fakturama before any further step. progress shows whether Save/follow-up was invoked; never retry it blindly."}
            write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
            raise ReviewRequired(code) from None
        report["progress"] = diagnostics.get("progress", {})
        write_private(args.out, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report["status"], "report": str(args.out)}, ensure_ascii=False))
    elif args.command == "run":
        raise ReviewRequired("live_adapter_not_implemented")
    elif args.command == "windows":
        if args.pid <= 0:
            raise ReviewRequired("invalid_process_id")
        print(json.dumps(list_process_windows(args.pid), ensure_ascii=False, indent=2))
    elif args.command == "discover":
        print(json.dumps(window_inventory() if args.all else discover_fakturama_windows(), ensure_ascii=False, indent=2))
    elif args.command == "capture":
        bundle = capture_bundle(args.label, args.out_dir, args.pid)
        print(json.dumps({"status": "READ_ONLY_CAPTURE_WRITTEN", "bundle": str(bundle),
                          "note": "Review the JSON files before sharing. No application data was changed."}))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        execute(args)
        return 0
    except ReviewRequired as exc:
        print(json.dumps({"status": "REVIEW_REQUIRED", "code": exc.code}), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print(json.dumps({"status": "ERROR", "code": "local_io_or_configuration_error"}), file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print(json.dumps({"status": "INTERRUPTED", "note": "Reconcile any started workflow before retrying."}), file=sys.stderr)
        return 130
    except Exception:
        print(json.dumps({"status": "ERROR", "code": "unexpected_failure_review_journal"}), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
