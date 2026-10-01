# Security review

## Threat model

Untrusted inputs: images, OCR/model output, desktop selector rows, copied document fields, loading dialogs, and source text resembling instructions or hotkeys. Trusted boundary: operator-controlled local machine, installed executable, configured local model server, and application. An attacker already controlling the desktop, model server, journal, or application is outside this initial threat model.

## Implemented safeguards

| Risk | Safeguard | Remaining limitation |
| --- | --- | --- |
| Prompt injection requests application actions | Extractor has no tools; typed schema; workflow accepts allowlisted data | Valid schema can still contain hallucinated facts; image review required |
| Command/hotkey injection | OCR uses argv with shell=False; UIA ValuePattern-style edit; no type_keys on source | Native pattern support is unverified on Fakturama |
| Malformed/oversized images | Decode format/dimension/frame/byte checks; fresh RGB PNG; remove metadata | Pillow decoder remains a dependency attack surface |
| Invalid financial values | Reject numeric floats/booleans/exponents/nonfinite/negative; Decimal arithmetic; reconcile totals | Rounding parity with Fakturama needs live validation |
| Ambiguous search results | Exact unique matching; conflict stops; no first-row selection | Adapter must prove results complete and search settled |
| Duplicate accounting documents | Hashed business key + source fingerprint; journal lease; adapter preflight | One stable journal/workspace per database; no cross-machine/exactly-once guarantee |
| Save happened but response failed | Commit INTENT before side effect; preserve REVIEW lease; no retry | Recovery is manual; records can remain saved/incomplete |
| Text leaks in errors | Error codes and opaque journal keys; no raw validation exception | Explicit extraction outputs/UI dumps can contain private data |
| Output overwrite/symlink | Exclusive create for candidate files; reject journal symlink | Trusted parent directory required; journal check has no hostile-directory race guarantee |
| Network egress | Client has fixed loopback Ollama address, no proxy/URL option, bounded responses | Local model server can forward requests; operator must disable cloud |
| Excess privileges | No admin requirement, no direct Fakturama DB writes, no automatic installers | Windows app accessibility permissions may require matching privilege levels |
| Credential leakage | No API keys needed; Git ignores .env/private/artifacts/journals | Git ignore is not a DLP control; inspect staged files before submission |

## Open risks before submission

- Implement and inspect the real desktop adapter; a mistaken scope can edit the wrong record even with safe helpers.
- Test model extraction with the actual input; no benchmark or factual-accuracy claim is supported yet.
- Verify numerical locale, zero/reduced VAT behavior, duplicate search results, and saved UI state labels.
- Add read-only reconciliation tooling for interrupted runs. Do not implement deletion/automatic rollback of accounting records.
- Perform a fresh dependency vulnerability scan and lock the Windows dependency tree. No current advisory database was available to this runtime; installed version pins are not an audit result.
- Use Windows user-private folders/ACLs for image, OCR, candidate, journal, and UI evidence. chmod alone does not establish Windows access restrictions.
- Decide retention and redact evidence before sending it to reviewers. Do not use real pharmacy/patient/accounting data.

The project is not a security-certified or production-ready system. Its safeguards reduce several avoidable risks; they cannot eliminate all vulnerabilities.
