import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fakturama_automation.finance import product_gross, validate_order  # noqa: E402
from fakturama_automation.input_io import read_order  # noqa: E402
from fakturama_automation.uia import discover_fakturama_windows  # noqa: E402

DELAY = 5


def window():
    rows = [w for w in discover_fakturama_windows() if (w["name"] or "").startswith("Fakturama - ")]
    if len(rows) != 1:
        sys.exit(f"Expected exactly one Fakturama main window, found {len(rows)}")
    return str(rows[0]["pid"]), str(rows[0]["handle"])


def cli(*args):
    return subprocess.run([sys.executable, "-m", "fakturama_automation.cli", *args], cwd=ROOT).returncode


class Run:
    def __init__(self):
        self.out = Path("private") / f"run-{datetime.now():%Y%m%d-%H%M%S}"
        (ROOT / self.out).mkdir(parents=True)
        self.step = 0
        self.input = None

    def operator(self, title, *lines):
        print(f"\n=== OPERATOR: {title}")
        for line in lines:
            print("   -", line)
        input("   Press Enter when done (Ctrl+C to stop)... ")

    def ask(self, question):
        while True:
            answer = input(f"   {question} [y/n]: ").strip().casefold()
            if answer in {"y", "n"}:
                return answer == "y"

    def auto(self, title, command, *args, optional=False):
        """Run one guarded live command; return its report (exit on review).

        optional=True is for read-only checks: a failure is reported and the run continues."""
        self.step += 1
        print(f"\n=== AUTO {self.step}: {title}")
        print(f"   Keep Fakturama in the foreground; starting in {DELAY}s...", flush=True)
        time.sleep(DELAY)
        pid, handle = window()
        out = self.out / f"{self.step:02d}-{command}.json"
        code = cli(command, "--input", self.input, "--reviewed-input", *args,
                   "--pid", pid, "--window-handle", handle, "--out", str(out))
        report = json.loads((ROOT / out).read_text(encoding="utf-8")) if (ROOT / out).exists() else {}
        if code != 0 and optional:
            print(f"   WARNING: '{title}' could not be completed ({report.get('code', code)}). "
                  "Read-only check skipped; verify it by hand in Fakturama.")
            return report
        if code != 0:
            sys.exit(f"\nStopped at '{title}' ({report.get('code', code)}). Report: {out}. "
                     "Inspect Fakturama; never re-run a Save blindly.")
        print(f"   -> {report.get('status')}")
        return report

    def capture(self, label):
        """Best-effort UIA evidence bundle; never blocks or stops the run."""
        pid, _ = window()
        try:
            subprocess.run([sys.executable, "-m", "fakturama_automation.cli", "capture", "--label", label,
                            "--pid", pid, "--out-dir", str(self.out / "ui")], cwd=ROOT, timeout=90)
        except subprocess.TimeoutExpired:
            print(f"   (evidence capture '{label}' timed out and was skipped)")


def extract(run, image, compare):
    target = run.out / "extracted.json"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first (or use --input with a reviewed JSON).")
    print(f"=== AUTO: extract {image} with Claude vision")
    if cli("extract", "--image", image, "--out", str(target)) != 0:
        sys.exit("Extraction needs review; nothing was sent to Fakturama.")
    if cli("validate", str(target)) != 0:
        sys.exit("Extracted totals do not reconcile; review the image. Nothing was sent to Fakturama.")
    data = json.loads((ROOT / target).read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, ensure_ascii=False))
    if compare:
        expected = json.loads((ROOT / compare).read_text(encoding="utf-8"))
        diffs = [k for k in expected if expected[k] != data.get(k)]
        print(f"   Differences vs {compare}: {diffs or 'none'}")
    if data.get("review_issues"):
        print("   Extractor review issues:", data["review_issues"])
    if not run.ask("Extraction checked against the image and correct?"):
        sys.exit(f"Correct {target} by hand and re-run with --input {target}.")
    return str(target)


def finish_invoice(run, number):
    """After the Order is saved: Documents check, linked Invoice, payment, final checks."""
    run.capture("order-saved")
    run.auto("Verify saved open Order in Data > Documents (4.5)",
             "documents-verify", "--expected-number", number, optional=True)
    run.auto("Linked Invoice via follow-up 'Invoice' (4.6-4.7)", "order-followup-invoice",
             "--expected-number", number)

    # 5. Payment status, Save once, re-read.
    run.auto("Verify the Invoice copied addresses, Order Date, items and totals (5.1)", "invoice-verify")
    run.auto("Apply extracted payment status (5.1-5.3)", "invoice-pay")
    saved = run.auto("Re-verify and Save the Invoice once (5.4, 5.6)", "invoice-pay", "--save")
    run.capture("invoice-saved")
    run.auto("Verify saved Invoice and still-open Order in Data > Documents (5.5)",
             "documents-verify", "--expected-number", number,
             "--invoice-number", saved['values']['number'],
             "--invoice-date", saved['values']['date_text'], optional=True)
    print(f"\nDone: Order {number} saved, Invoice {saved.get('editor_name')} saved "
          f"({'paid' if saved.get('paid') else 'unpaid'}). Evidence in {run.out}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="Order image (PNG/JPEG); required unless --no-ocr-check")
    parser.add_argument("--input", help="Reviewed extraction JSON; OCR cross-checks it against the image. "
                        "Without it, Claude vision extracts (needs ANTHROPIC_API_KEY).")
    parser.add_argument("--compare", help="Optional reviewed JSON to diff a Claude extraction against")
    parser.add_argument("--no-ocr-check", action="store_true",
                        help="Variant test order with no matching image: skip the OCR cross-check (JSON is used as given)")
    parser.add_argument("--resume-debtor", help="Customer ID of an already open, unsaved New Debtor (needs --resume-number)")
    parser.add_argument("--order-saved", action="store_true",
                        help="The Order (--resume-number) is already saved; continue with the Documents check and the Invoice")
    parser.add_argument("--resume-number", help="Skip opening/header: the New Order with this No. is already open and prepared")
    args = parser.parse_args()
    run = Run()
    if args.input and args.no_ocr_check:
        print("=== OCR cross-check skipped (--no-ocr-check): the JSON has no matching image")
        run.input = args.input
    elif args.input:
        if not args.image:
            sys.exit("--image is required for the OCR cross-check")
        verify_out = run.out / "ocr-verify.json"
        print("=== AUTO: OCR cross-check of the JSON against the image")
        if cli("ocr-verify", "--image", args.image, "--input", args.input, "--out", str(verify_out)) != 0:
            sys.exit("OCR disagrees with the JSON on a critical field; see " + str(verify_out))
        print("   (item lines are not OCR-readable at this resolution; they are reconciled via the printed totals)")
        run.input = args.input
    else:
        if not args.image:
            sys.exit("--image is required for extraction")
        run.input = extract(run, args.image, args.compare)
    s = read_order(ROOT / run.input)
    validate_order(s)
    print(f"\nSource: {s.external_reference}, {len(s.items)} line(s), total {s.totals.gross} {s.currency}, "
          f"{s.payment.method} {s.payment.status}. Reports: {run.out}")
    if not args.resume_number:
        run.operator("Prepare", "Fakturama open with no New Order/New product editors open; close finished editors.")

    # 1. Open the Order first and fill its header.
    if args.resume_number:
        number = args.resume_number
        print(f"   Resuming with the already open, header-prepared Order {number}.")
    else:
        opened = run.auto("Open New Order (1.3)", "order-open")
        number = opened["proposed_number"]
        run.auto(f"Header of {number}: Date, Cust.Ref., Net, With VAT (1.4-1.7)", "order-prepare",
                 "--expected-number", number)

    if args.order_saved:
        if not args.resume_number:
            sys.exit("--order-saved needs --resume-number")
        finish_invoice(run, number)
        return

    # 2. Debtor: the Order's address selector is the existence check.
    if args.resume_debtor:
        found = {"status": "NOT_FOUND"}
        created = {"generated_id": args.resume_debtor}
        print(f"   Resuming with the already open New Debtor {args.resume_debtor}.")
    else:
        found = run.auto("Debtor selector existence check (2.1-2.3)", "selector-check", "--kind", "debtor")
    if found["status"] == "NOT_FOUND":
        if not args.resume_debtor:
            print(f"   No exact Debtor row for '{s.debtor.company}'. Rows seen: {found.get('rows')}")
            if not run.ask("Confirm the Debtor really does not exist and should be created?"):
                sys.exit("Stopped before creating a Debtor.")
            created = run.auto("Open New Debtor while retaining the Order (2.5)",
                               "debtor-open-editor", "--expected-number", number)
        run.auto("Prepare New Debtor fields, roles and payment (2.6-2.10)",
                 "debtor-complete-prepare", "--expected-id", created['generated_id'])
        run.auto("Save the prepared Debtor once (2.11)", "debtor-save", "--expected-id", created['generated_id'])
        run.auto("Return to the retained Order (2.12)", "order-return", "--expected-number", number)
        again = run.auto("Re-select the new Debtor from the Order (2.12-2.13)", "selector-check", "--kind", "debtor")
        if again["status"] != "SELECTED_EXISTING":
            sys.exit("New Debtor not selectable from the Order; review.")

    # 3. Every Product line, in source order.
    for index, item in enumerate(s.items, 1):
        found = run.auto(f"Product {index}/{len(s.items)} selector check: {item.sku} (3.2-3.3)",
                         "selector-check", "--kind", "product", "--sku", item.sku)
        if found["status"] == "NOT_FOUND":
            print(f"   No exact row for SKU {item.sku}.")
            if not run.ask(f"Confirm {item.sku} does not exist and should be created?"):
                sys.exit("Stopped before creating a Product.")
            run.operator(f"VAT check (3.4-3.6)", f"Data > VATs: exactly one 'VAT {item.vat_percent.normalize()}%', "
                         f"value {item.vat_percent}%, code S. Create it (Save once) only if missing. "
                         "Return to the Order tab.")
            run.auto(f"Open New product for {item.sku} (3.7)", "product-open-editor", "--sku", item.sku)
            run.auto(f"Fill {item.sku}, gross {product_gross(item)} (3.8-3.10)", "product-prepare",
                     "--reviewed-vat", "--sku", item.sku)
            run.auto(f"Save {item.sku} once (3.11)", "product-save", "--sku", item.sku)
            run.auto("Return to the retained Order", "order-return", "--expected-number", number)
            again = run.auto(f"Re-select {item.sku} from the Order (3.12)", "selector-check",
                             "--kind", "product", "--sku", item.sku)
            if again["status"] != "SELECTED_EXISTING":
                sys.exit(f"{item.sku} not selectable from the Order; review.")
        run.auto(f"Apply extracted quantity/discount and verify line {index}: {item.sku} (3.13-3.16)",
                 "order-line-prepare", "--expected-number", number, "--line-index", str(index))

    # 4. Verify, Save once, linked Invoice from the follow-up area.
    run.auto("Verify Order vs source and Save once (4.1-4.4)", "order-finalize", "--expected-number", number, "--save")
    finish_invoice(run, number)


if __name__ == "__main__":
    main()
