"""OCR of screenshots of the selector grids: header normalisation and word grouping. OCR words are
candidates, never verified rows.
"""

import io
import math
import platform
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

from .errors import ReviewRequired
from .extraction import MAX_IMAGE_BYTES, MAX_PIXELS
from .input_io import MAX_JSON_BYTES
from .selector_ui import AddressSelectorSession, ProductSelectorSession, exactly_one

HEADERS = {
    "Select a product": ("Item No.", "Name", "Description", "Stock", "Price", "VAT"),
    "Select the address": ("No.", "First Name", "Name", "Company", "ZIP", "City"),
}
TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


@dataclass(frozen=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float


def parse_tsv(data: bytes, size: tuple[int, int]) -> list[OcrWord]:
    """TSV is tab-separated text, not CSV: quote characters are literal data."""
    if len(data) > MAX_JSON_BYTES:
        raise ReviewRequired("ocr_output_too_large")
    try:
        lines = data.decode("utf-8").splitlines()
        if not lines or lines[0] != TSV_HEADER or len(lines) > 20_000:
            raise ValueError()
        words = []
        page_seen = False
        for line in lines[1:]:
            fields = line.split("\t", 11)
            if len(fields) != 12:
                raise ValueError()
            level, page, block, par, row, word, left, top, width, height = map(int, fields[:10])
            conf = float(fields[10])
            if not math.isfinite(conf) or not -1 <= conf <= 100:
                raise ValueError()
            if not 1 <= level <= 5 or page != 1 or min(block, par, row, word, left, top, width, height) < 0:
                raise ValueError()
            if left + width > size[0] or top + height > size[1]:
                raise ValueError()
            if level == 1:
                if page_seen or (left, top, width, height) != (0, 0, *size):
                    raise ValueError()
                page_seen = True
            text = fields[11]
            if level != 5 or not text.strip():
                continue
            if conf < 0 or min(width, height) == 0 or len(text) > 1024 or any(ord(c) < 32 for c in text):
                raise ValueError()
            words.append(OcrWord(text, left, top, width, height, conf))
            if len(words) > 5000:
                raise ValueError()
        if not page_seen:
            raise ValueError()
        return words
    except (UnicodeError, ValueError, OverflowError):
        raise ReviewRequired("invalid_ocr_tsv") from None


def ocr_words(clean_png: bytes, executable: Path) -> tuple[list[OcrWord], tuple[int, int]]:
    if not executable.is_absolute() or not executable.is_file():
        raise ReviewRequired("ocr_executable_requires_absolute_existing_path")
    if len(clean_png) > MAX_IMAGE_BYTES:
        raise ReviewRequired("image_too_large")
    with Image.open(io.BytesIO(clean_png)) as image:
        size = image.size
    if min(size) < 32 or size[0] * size[1] > MAX_PIXELS:
        raise ReviewRequired("image_dimensions_out_of_range")
    with tempfile.TemporaryDirectory(prefix="fakturama-selector-ocr-") as folder:
        source, target = Path(folder) / "input.png", Path(folder) / "words"
        source.write_bytes(clean_png)
        source.chmod(0o600)
        try:
            result = subprocess.run([str(executable), str(source), str(target), "-l", "eng",
                                     "--psm", "11", "tsv"], shell=False, timeout=45,
                                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode != 0:
                raise ReviewRequired("ocr_process_failed")
            with target.with_suffix(".tsv").open("rb") as output:
                data = output.read(MAX_JSON_BYTES + 1)
        except (OSError, subprocess.TimeoutExpired):
            raise ReviewRequired("ocr_process_failed") from None
    return parse_tsv(data, size), size


def _header_key(text):
    # Only UI header typography is normalized; identifier data is preserved.
    return re.sub(r"[ .]", "", text).casefold()


def observe_headers(words: list[OcrWord], title: str) -> dict:
    if title not in HEADERS:
        raise ReviewRequired("invalid_selector_title")
    expected = HEADERS[title]
    matches = []
    # Group by shared vertical overlap, independent of OCR block/line numbering.
    for anchor in words:
        if _header_key(anchor.text) not in {_header_key(expected[0].split()[0]), _header_key(expected[0])}:
            continue
        band = sorted([w for w in words if max(w.top, anchor.top) <
                       min(w.top + w.height, anchor.top + anchor.height)], key=lambda w: w.left)
        start = band.index(anchor)
        headers, index = [], start
        for label in expected:
            combined, parts = "", []
            while index < len(band) and len(combined) < len(_header_key(label)):
                parts.append(band[index])
                combined += _header_key(band[index].text)
                index += 1
            if combined != _header_key(label) or not parts or min(w.confidence for w in parts) < 70:
                break
            if any(a.left + a.width > b.left for a, b in zip(parts, parts[1:])):
                break
            headers.append({"label": label, "words": [asdict(w) for w in parts]})
        if len(headers) == len(expected):
            matches.append(headers)
    if len(matches) != 1:
        raise ReviewRequired("selector_headers_missing_low_confidence_or_ambiguous")
    return {"status": "UNVERIFIED_VISUAL_EVIDENCE", "title": title,
            "headers": matches[0], "words": [asdict(w) for w in words],
            "search_complete": False, "all_rows_enumerated": False,
            "absence_verified": False,
            "note": "OCR candidates only; no row selection or master creation is authorized. Boxes are image-local."}


def inspect_selector_image(clean_png: bytes, title: str, executable: Path) -> dict:
    words, size = ocr_words(clean_png, executable)
    report = observe_headers(words, title)
    report["image_size"] = list(size)
    return report


def _rect(control):
    r = control.rectangle()
    result = (r.left, r.top, r.right, r.bottom)
    if min(result[2] - result[0], result[3] - result[1]) < 32:
        raise ReviewRequired("selector_capture_invalid_rectangle")
    return result


def _grid(session, dialog):
    bars = [c for c in dialog.descendants(control_type="ScrollBar")
            if c.element_info.name == "Horizontal" and c.is_visible()]
    bar = exactly_one(bars, "horizontal_scrollbar", session.prefix)
    pane = bar.parent()
    if pane.element_info.control_type != "Pane" or pane.element_info.class_name != "SWT_Window0":
        raise ReviewRequired("selector_grid_structure_changed")
    if pane.element_info.process_id != session.root.element_info.process_id or not pane.is_visible():
        raise ReviewRequired("selector_grid_scope_mismatch")
    return pane


def capture_grid(session, foreground_handle) -> tuple[bytes, dict]:
    """One read-only frame with state checks; stable state does not imply search completion.

    Does not focus/raise any window. Caller must keep the selector in front.
    """
    def state():
        dialog = session.dialog()
        pane = _grid(session, dialog)
        dialog_rect, grid_rect = _rect(dialog), _rect(pane)
        if not (dialog_rect[0] <= grid_rect[0] < grid_rect[2] <= dialog_rect[2]
                and dialog_rect[1] <= grid_rect[1] < grid_rect[3] <= dialog_rect[3]):
            raise ReviewRequired("selector_grid_outside_dialog")
        if not dialog.handle or not pane.handle or foreground_handle() != dialog.handle:
            raise ReviewRequired("selector_must_remain_in_foreground")
        query = session.search_control(dialog).get_value()
        if not isinstance(query, str) or len(query) > 1024 or any(ord(c) < 32 for c in query):
            raise ReviewRequired("selector_capture_invalid_query")
        return pane, (dialog.handle, pane.handle, dialog_rect, grid_rect, query)

    pane, before = state()
    r = before[3]
    if (r[2] - r[0]) * (r[3] - r[1]) > MAX_PIXELS:
        raise ReviewRequired("image_dimensions_out_of_range")
    image = pane.capture_as_image()
    _, after = state()
    if before != after:
        raise ReviewRequired("selector_changed_during_capture")
    if image is None or image.size != (r[2] - r[0], r[3] - r[1]):
        raise ReviewRequired("selector_capture_scale_mismatch")
    if image.width * image.height > MAX_PIXELS:
        raise ReviewRequired("image_dimensions_out_of_range")
    clean = Image.frombytes("RGB", image.size, image.convert("RGB").tobytes())
    output = io.BytesIO()
    clean.save(output, format="PNG")
    if output.tell() > MAX_IMAGE_BYTES:
        raise ReviewRequired("image_too_large")
    return output.getvalue(), {"order_number": session.number, "query": before[4],
                              "title": session.dialog_title, "result_state": "UNVERIFIED"}


def capture_selector_grid(pid: int, handle: int, title: str) -> tuple[bytes, dict]:
    if type(pid) is not int or type(handle) is not int or min(pid, handle) <= 0 or title not in HEADERS:
        raise ReviewRequired("invalid_selector_capture_scope")
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_selector_capture")
    try:
        from pywinauto import Desktop
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    root = Desktop(backend="uia").window(handle=handle).wrapper_object()
    if (root.handle != handle or root.element_info.process_id != pid or not root.is_visible()
            or root.element_info.class_name != "SWT_Window0"
            or not root.element_info.name.startswith("Fakturama - ")):
        raise ReviewRequired("selector_main_window_identity_mismatch")
    session_type = ProductSelectorSession if title == "Select a product" else AddressSelectorSession
    return capture_grid(session_type(root), user32.GetForegroundWindow)
