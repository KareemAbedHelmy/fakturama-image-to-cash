"""Field-by-field accuracy of an image extraction against a reviewed JSON.

  .\.venv\Scripts\python.exe tools\extraction_report.py --image examples/order.synthetic.png ^
      --expected examples/order.synthetic.json --provider ollama --model qwen3-vl:4b-instruct

Runs `extract` (or reads --candidate, an existing extraction) and prints every field that differs
plus a summary line. Nothing is sent to Fakturama.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def flatten(value, prefix=""):
    if isinstance(value, dict):
        return {k: v for key, item in value.items() for k, v in flatten(item, f"{prefix}{key}.").items()}
    if isinstance(value, list):
        return {k: v for i, item in enumerate(value) for k, v in flatten(item, f"{prefix}{i}.").items()}
    return {prefix[:-1]: value}


DETAIL = ("source_customer_id", "salutation", "recipient_name", "additional_name", "specification",
          "district", "email", "telephone", "unit")


def is_detail(field):
    """Optional descriptive fields; the order, items, payment, totals and addresses are essential."""
    return field.rsplit(".", 1)[-1] in DETAIL


def compare(expected, extracted):
    want = {k: v for k, v in flatten(expected).items() if not k.startswith("review_issues")}
    got = flatten(extracted)
    wrong = {k: (want[k], got.get(k)) for k in want if str(got.get(k)) != str(want[k])}
    return want, wrong


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True, type=Path, help="Reviewed JSON for the image")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--candidate", type=Path, help="Use an existing extraction instead of running one")
    parser.add_argument("--provider", choices=["claude", "ollama"], default="ollama")
    parser.add_argument("--model")
    args = parser.parse_args()
    started = time.monotonic()
    candidate = args.candidate
    if candidate is None:
        if not args.image:
            sys.exit("--image or --candidate is required")
        candidate = ROOT / "private" / f"extraction-{args.provider}-{int(started)}.json"
        command = [sys.executable, "-m", "fakturama_automation.cli", "extract", "--provider", args.provider,
                   "--image", str(args.image), "--out", str(candidate)]
        if args.model:
            command += ["--model", args.model]
        if subprocess.run(command, cwd=ROOT).returncode != 0:
            sys.exit("Extraction failed; see the message above.")
    want, wrong = compare(json.loads(args.expected.read_text(encoding="utf-8")),
                          json.loads(Path(candidate).read_text(encoding="utf-8")))
    for field, (expected, got) in wrong.items():
        print(f"  DIFF {field}{' (detail)' if is_detail(field) else ''}: expected {expected!r}, got {got!r}")
    essential = [f for f in want if not is_detail(f)]
    missed = [f for f in wrong if not is_detail(f)]
    print(f"essential fields: {len(essential) - len(missed)}/{len(essential)} match; "
          f"all fields: {len(want) - len(wrong)}/{len(want)} ({100 * (len(want) - len(wrong)) / len(want):.1f}%); "
          f"{time.monotonic() - started:.0f}s. Extraction: {candidate}")


if __name__ == "__main__":
    main()
