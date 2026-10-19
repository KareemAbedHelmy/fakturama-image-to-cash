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
from fakturama_automation import launcher  # noqa: E402
from fakturama_automation.input_io import read_order  # noqa: E402
from fakturama_automation.uia import discover_fakturama_windows  # noqa: E402

DELAY = 2


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
        self.yes = False
        self.hint = ''  # how to resume if the current stage stops

    def operator(self, title, *lines):
        print(f"\n=== OPERATOR: {title}")
        for line in lines:
            print("   -", line)
        input("   Press Enter when done (Ctrl+C to stop)... ")

    def ask(self, question, *, creation=False):
        """y/n prompt. --yes answers only the safe 'create the missing record' confirmations."""
        if creation and self.yes:
            print(f"   {question} -> yes (--yes)")
            return True
        while True:
            answer = input(f"   {question} [y/n]: ").strip().casefold()
            if answer in {"y", "n"}:
                return answer == "y"

    def auto(self, title, command, *args, optional=False):
        """Run one guarded live command; return its report (exit on review).

        optional=True is for read-only checks: a failure is reported and the run continues."""
        self.step += 1
        print(f"\n=== AUTO {self.step}: {title}")
        pid, handle = window()
        launcher.bring_to_front(int(handle))
        print(f"   Fakturama brought to the front; starting in {DELAY}s. Leave the mouse and keyboard alone.", flush=True)
        time.sleep(DELAY)
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
                     "Inspect Fakturama; never re-run a Save blindly."
                     + (chr(10) * 2 + f"How to resume: {self.hint}" if self.hint else ""))
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


def extract(run, image, compare, provider="claude", model=None):
    target = run.out / "extracted.json"
    if provider == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first (or use --input with a reviewed JSON).")
    if provider == "ollama" and not model:
        sys.exit("--provider ollama requires --model with an installed local vision model.")
    print(f"=== AUTO: extract {image} with {provider} vision (a local model can take several minutes)", flush=True)
    command = ["extract", "--provider", provider, "--image", image, "--out", str(target)]
    if model:
        command.extend(["--model", model])
    if cli(*command) != 0:
        sys.exit("Extraction needs review; nothing was sent to Fakturama.")
    data = json.loads((ROOT / target).read_text(encoding="utf-8"))
    issues = data.get("review_issues") or []
    confirmed = run.out / "confirmed.json"
    confirmed.write_text(json.dumps({**data, "review_issues": []}, indent=2, ensure_ascii=False), encoding="utf-8")
    if cli("validate", str(confirmed)) != 0:
        sys.exit("Extracted totals do not reconcile; review the image. Nothing was sent to Fakturama.")
    print(json.dumps({**data, "review_issues": []}, indent=2, ensure_ascii=False))
    if compare:
        expected = json.loads((ROOT / compare).read_text(encoding="utf-8"))
        diffs = [k for k in expected if k != "review_issues" and expected[k] != data.get(k)]
        print(f"   Differences vs {compare}: {diffs or 'none'}")
    if issues:
        print("   The model flagged these for your attention:")
        for issue in issues:
            print("    -", issue)
    verify_out = run.out / "ocr-verify-extraction.json"
    cli("ocr-verify", "--image", image, "--input", str(confirmed), "--out", str(verify_out))
    if (ROOT / verify_out).is_file():
        report = json.loads((ROOT / verify_out).read_text(encoding="utf-8"))
        flagged = report["critical_mismatches"] + report["soft_mismatches"]
        if flagged:
            print("   OCR (an independent reader) disagrees with the extraction on:", ", ".join(flagged))
            print("   Check those fields against the image before answering.")
        else:
            print("   OCR cross-check agrees with the extraction on the fields it can read.")
    else:
        print("   OCR cross-check could not run; check every field against the image yourself.")
    if not run.ask("Extraction checked against the image and correct?"):
        sys.exit(f"Correct {confirmed} by hand and re-run with --input {confirmed}.")
    target = confirmed
    return str(target)


def finish_invoice(run, number):
    """After the Order is saved: Documents check, linked Invoice, payment, final checks."""
    run.hint = (f"The Order {number} is saved. Open Data > Documents first. If an Invoice for this Order already exists, "
                "finish or fix that Invoice by hand: --order-saved would invoke the follow-up again and create a second "
                f"Invoice. If there is no Invoice yet, re-run the same command and add: --resume-number {number} --order-saved")
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
                        "Without it, the selected vision provider extracts the image; Claude needs ANTHROPIC_API_KEY.")
    parser.add_argument("--compare", help="Optional reviewed JSON to diff the vision extraction against")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="claude",
                        help="Image extraction provider when --input is omitted (default: claude)")
    parser.add_argument("--model", help="Vision model for --provider; required for local Ollama")
    parser.add_argument("--no-ocr-check", action="store_true",
                        help="Variant test order with no matching image: skip the OCR cross-check (JSON is used as given)")
    parser.add_argument("--resume-debtor", help="Customer ID of an already open, unsaved New Debtor (needs --resume-number)")
    parser.add_argument("--order-saved", action="store_true",
                        help="The Order (--resume-number) is already saved; continue with the Documents check and the Invoice")
    parser.add_argument("--debtor-done", action="store_true",
                        help="The Debtor is already selected in the open Order; continue with the products (needs --resume-number)")
    parser.add_argument("--resume-line", type=int,
                        help="Debtor and products up to this 1-based line are already in the open Order; continue with that line's Qty./Discount (needs --resume-number)")
    parser.add_argument("--yes", action="store_true",
                        help="Create a missing Debtor/Product without asking (the selector check already proved it absent). "
                             "The extraction confirmation is never skipped")
    parser.add_argument("--window", default="maximize",
                        help="Fakturama window size: maximize (default) or WIDTHxHEIGHT, e.g. 1920x1080")
    parser.add_argument("--no-launch", action="store_true",
                        help="Do not start or resize Fakturama; use the window that is already open")
    parser.add_argument("--resume-number", help="Skip opening/header: the New Order with this No. is already open and prepared")
    args = parser.parse_args()
    run = Run()
    run.yes = args.yes
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
        run.input = extract(run, args.image, args.compare, args.provider, args.model)
    s = read_order(ROOT / run.input)
    validate_order(s)
    print(f"\nSource: {s.external_reference}, {len(s.items)} line(s), total {s.totals.gross} {s.currency}, "
          f"{s.payment.method} {s.payment.status}. Reports: {run.out}")
    launched = False
    if not args.no_launch:
        info = launcher.prepare(args.window)
        launched = info["launched"]
        print(f"   Fakturama {'started' if launched else 'found'} (pid {info['pid']}); window: {args.window}")
    if not args.resume_number and not launched:
        run.operator("Prepare", "Close any open New Order/New Debtor/New product editors and finished documents.")

    # 1. Open the Order first and fill its header.
    run.hint = ("the Order header is not finished. Close the New Order tab without saving and start again "
                "(no resume flag is safe at this point).")
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
    run.hint = (f"re-run the same command and add: --resume-number {number}  (the Debtor is checked again and is "
                "selected if it already exists; if a New Debtor editor is still open and unsaved, close it first, "
                "or add --resume-debtor CUSTNNNNNN with its customer ID). If the report of the Debtor Save says "
                "save_invoked: true, the Debtor exists: close its tab and use the plain --resume-number form.")
    if args.debtor_done:
        found = {"status": "SELECTED_EXISTING"}
        print("   Resuming after the Debtor: it is already selected in the Order.")
    elif args.resume_line:
        found = {"status": "SELECTED_EXISTING"}
        print(f"   Resuming at line {args.resume_line}: the Debtor and earlier products are already in the Order.")
    elif args.resume_debtor:
        found = {"status": "NOT_FOUND"}
        created = {"generated_id": args.resume_debtor}
        print(f"   Resuming with the already open New Debtor {args.resume_debtor}.")
    else:
        found = run.auto("Debtor selector existence check (2.1-2.3)", "selector-check", "--kind", "debtor")
    if found["status"] == "NOT_FOUND":
        if not args.resume_debtor:
            print(f"   No exact Debtor row for '{s.debtor.company}'. Rows seen: {found.get('rows')}")
            if not run.ask("Confirm the Debtor really does not exist and should be created?", creation=True):
                sys.exit("Stopped before creating a Debtor.")
            # The New Debtor editor reads its payment list once, when it opens, so a missing term is created first.
            pay = run.auto(f"Payment term '{s.payment.method}' check in Data > terms of payment (2.10)",
                           "payment-term-check")
            if pay["status"] == "PAYMENT_MISSING":
                run.auto(f"Create the '{s.payment.method}' term of payment and Save once (2.10)", "payment-term-create")
                again_pay = run.auto("Confirm the term in Data > terms of payment", "payment-term-check")
                if again_pay["status"] != "PAYMENT_EXISTS":
                    sys.exit(f"'{s.payment.method}' not found after creation; review.")
                run.auto("Return to the retained Order after the term (2.10.6)", "order-return",
                         "--expected-number", number)
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
        if args.resume_line and index < args.resume_line:
            continue
        before = "--debtor-done" if index == 1 else f"--resume-line {index - 1}"
        run.hint = (f"count the lines in the Order's item list first. Re-run the same command and add "
                    f"--resume-number {number} plus: --resume-line {index} if line {index} ({item.sku}) is already "
                    f"in the Order (continue with its Qty./Discount); {before} if it is not there yet "
                    "(selecting a product adds a line every time, so using the wrong flag adds a duplicate line). "
                    "If a New product or VAT editor is open and unsaved, close it first.")
        if args.resume_line == index:
            found = {"status": "SELECTED_EXISTING"}
        else:
            found = run.auto(f"Product {index}/{len(s.items)} selector check: {item.sku} (3.2-3.3)",
                             "selector-check", "--kind", "product", "--sku", item.sku)
        if found["status"] == "NOT_FOUND":
            print(f"   No exact row for SKU {item.sku}.")
            if not run.ask(f"Confirm {item.sku} does not exist and should be created?", creation=True):
                sys.exit("Stopped before creating a Product.")
            vat = run.auto(f"VAT {item.vat_percent.normalize()}% check in Data > VATs (3.4)", "vat-check", "--sku", item.sku)
            if vat["status"] == "VAT_MISSING":
                run.auto(f"Create {vat['name']} and Save once (3.5-3.6)", "vat-create", "--sku", item.sku)
                again_vat = run.auto(f"Confirm {vat['name']} in Data > VATs", "vat-check", "--sku", item.sku)
                if again_vat["status"] != "VAT_EXISTS":
                    sys.exit(f"{vat['name']} not found after creation; review.")
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

    # 4. Verify, Save once, linked Invoice from the follow-up area. Selecting the Debtor resets the Order's
    # overall Discount to the contact's, so a source discount is set here, after the Debtor and the lines.
    run.hint = (f"is the Order saved (its tab shows {number} without a * and Data > Documents lists it)? If yes, check "
                f"Documents for an Invoice first, then add --resume-number {number} --order-saved (only if there is "
                f"no Invoice yet). If not saved, re-run the same command and add --resume-number {number} "
                f"--resume-line {len(s.items)}.")
    if s.order_discount_percent != 0:
        run.auto(f"Set the Order discount to {s.order_discount_percent}% (4.2)", "order-discount",
                 "--expected-number", number)
    run.auto("Verify Order vs source and Save once (4.1-4.4)", "order-finalize", "--expected-number", number, "--save")
    finish_invoice(run, number)


if __name__ == "__main__":
    main()
