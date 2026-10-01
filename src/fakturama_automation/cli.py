"""Small CLI; live run explicitly unavailable until the Windows adapter is verified."""

import argparse
import json
import re
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from .domain import OrderInput
from .diagnostics import capture_bundle
from .errors import ReviewRequired
from .extraction import extract_local_vision, local_ocr, sanitize_image
from .finance import product_gross, validate_order
from .input_io import read_order, write_private
from .journal import Journal
from .matching import PAYMENT_CODES, PaymentRow, VatRow, vat_name
from .simulator import Simulator
from .uia import discover_fakturama_windows, inspect_window, list_process_windows
from .workflow import Workflow


def seed_simulator(sim: Simulator, source: OrderInput, mode: str):
    if mode in {"existing", "mixed"}:
        sim.seed_debtor(source.debtor)
        sim.payments.append(PaymentRow("1", source.payment.method, PAYMENT_CODES[source.payment.method], Decimal("0"), 0, 0))
        selected_items = source.items if mode == "existing" else source.items[:1]
        for item in selected_items:
            if not any(p.row.sku == item.sku for p in sim.products):
                sim.seed_product(item, product_gross(item))
            if not any(v.name == vat_name(item.vat_percent) for v in sim.vats):
                sim.vats.append(VatRow(str(len(sim.vats)+1), vat_name(item.vat_percent), item.vat_percent, "S"))


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
    journal = commands.add_parser("journal", help="Read journal state; no reset or retry")
    journal.add_argument("path", type=Path)
    extract = commands.add_parser("extract", help="Opt-in local Ollama vision extraction to untrusted candidate JSON")
    extract.add_argument("--image", type=Path, required=True)
    extract.add_argument("--model", required=True, help="Already-installed LOCAL vision model; disable server cloud features")
    extract.add_argument("--out", type=Path, required=True)
    ocr = commands.add_parser("ocr", help="Produce local OCR text, without inferring structured order data")
    ocr.add_argument("--image", type=Path, required=True)
    ocr.add_argument("--tesseract", type=Path, required=True)
    ocr.add_argument("--language", default="eng")
    ocr.add_argument("--out", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="Read-only Windows UIA tree dump; may contain private data")
    inspect.add_argument("--pid", type=int, required=True)
    inspect.add_argument("--window-handle", type=lambda v: int(v, 0), required=True)
    inspect.add_argument("--out", type=Path, required=True)
    windows = commands.add_parser("windows", help="List windows in an explicit Windows process (read-only)")
    windows.add_argument("--pid", type=int, required=True)
    commands.add_parser("discover", help="List visible Fakturama windows read-only; no PID needed")
    capture = commands.add_parser("capture", help="Create a read-only Fakturama UIA diagnostic ZIP; no clicks or saves")
    capture.add_argument("--label", required=True)
    capture.add_argument("--out-dir", type=Path, default=Path("private/ui"))
    capture.add_argument("--pid", type=int)
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
    elif args.command == "journal":
        if not args.path.is_file():
            raise ReviewRequired("journal_not_found")
        with Journal(args.path) as journal:
            print(json.dumps(journal.summary(), indent=2))
    elif args.command == "extract":
        clean, image_hash = sanitize_image(args.image)
        candidate = extract_local_vision(clean, args.model)
        write_private(args.out, candidate.model_dump_json(indent=2))
        print(json.dumps({"status": "CANDIDATE_WRITTEN", "image_sha256": image_hash,
                          "review_issue_count": len(candidate.review_issues),
                          "note": "Review every field against the source image before desktop use."}))
    elif args.command == "ocr":
        clean, _ = sanitize_image(args.image)
        write_private(args.out, local_ocr(clean, args.tesseract, args.language))
        print(json.dumps({"status": "OCR_TEXT_WRITTEN"}))
    elif args.command == "inspect":
        if args.pid <= 0 or args.window_handle <= 0:
            raise ReviewRequired("invalid_window_identity")
        tree = inspect_window(args.pid, args.window_handle)
        write_private(args.out, json.dumps(tree, ensure_ascii=False, indent=2))
        print(json.dumps({"status": "UIA_DUMP_WRITTEN", "note": "Inspect and redact private data before sharing."}))
    elif args.command == "run":
        raise ReviewRequired("live_adapter_not_implemented")
    elif args.command == "windows":
        if args.pid <= 0:
            raise ReviewRequired("invalid_process_id")
        print(json.dumps(list_process_windows(args.pid), ensure_ascii=False, indent=2))
    elif args.command == "discover":
        print(json.dumps(discover_fakturama_windows(), ensure_ascii=False, indent=2))
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
