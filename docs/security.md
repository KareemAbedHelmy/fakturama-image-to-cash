# Security notes

## Threat model

Untrusted inputs: the order image, OCR and model output, rows copied from
Fakturama's selectors, and any source text that resembles instructions or
hotkeys. The trusted boundary is the operator's own Windows machine, the
installed Fakturama executable and, for the optional extractor, the Anthropic
API. An attacker who already controls the desktop, the application or the
journal is outside this model.

## Safeguards

| Risk | Safeguard | Remaining limitation |
| --- | --- | --- |
| Prompt injection asks for application actions | The extractor has no tools: it returns JSON that is parsed against a strict schema; source text never becomes keystrokes | A schema-valid answer can still contain a wrong name or SKU; the guided runner shows the extraction for confirmation |
| Hotkey or command injection | Tesseract runs with an argv list and `shell=False`; fields are written as literal values; only fixed shortcuts (Ctrl+A/Ctrl+C, date navigation keys) are ever sent | UIA pattern support was verified only on Fakturama 2.2.0 |
| Malformed or oversized images | Format, dimension, frame and byte checks; a fresh RGB PNG is built, which drops metadata | Pillow's decoders remain an attack surface |
| Invalid money values | Floats, exponents and negatives are rejected; decimal arithmetic; totals must reconcile before anything is typed | A reconciled total does not prove a correct name, SKU or address |
| Ambiguous search results | Exact, unique matching on copied selector rows; several matches or a conflict stop the run | Absence is inferred from an empty result grid, so the runner asks you to confirm before creating a record |
| Duplicate documents | Each live command checks the editor state first and refuses to Save an already saved Order or Invoice; the simulator workflow also keeps a hashed-key journal | One workspace per database; no exactly-once guarantee |
| A Save happens but its response fails | An intent file is written before every live action; each Save or Invoke runs once and is never retried | Recovery from an uncertain result is manual |
| Wrong window | The tool binds to the Fakturama process, requires it in the foreground and re-checks identity before each action; no stored coordinates | A user clicking elsewhere mid-step can still cause a stop |
| Text leaking into logs | Errors are codes; reports contain only the order's synthetic data | Reports under `private/` can hold personal data |
| Output overwrite | Intent and report files are created exclusively | The parent directory must be trusted |
| Network egress | Only the opt-in `extract --provider claude` call leaves the machine, to a fixed Anthropic host (the key comes from the environment); `--provider ollama` talks to a fixed loopback address | The image is sent to the API when the Claude provider is used |
| Excess privilege | No admin rights; no direct writes to Fakturama's database; Fakturama's own logs are read-only | Accessibility may need matching privilege levels |

## Known limits

- This is not a security-certified or production system. Its safeguards reduce
  avoidable risks; they do not remove every vulnerability.
- Dependencies are pinned but not locked transitively or audited.
- Copying from the grid overwrites the clipboard. The tool reads only a
  clipboard freshly owned by Fakturama and never restores earlier content.
- Evidence and reports can contain accounting data. The examples use synthetic
  data only; keep real data out of the repository (`private/` is ignored).
