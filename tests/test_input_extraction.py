import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.extraction import extract_local_vision, sanitize_image
from fakturama_automation.input_io import write_private
from test_domain_finance import fixture


class FakeResponse:
    status = 200

    def __init__(self, content):
        self.content = content

    def read(self, limit):
        return self.content[:limit]


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class InputExtractionTests(unittest.TestCase):
    def test_image_metadata_stripped(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"image.jpg"
            im = Image.new("RGB", (100, 100), "white")
            exif = Image.Exif()
            exif[270] = "Sensitive source metadata"
            im.save(path, exif=exif)
            clean, digest = sanitize_image(path)
            with Image.open(io.BytesIO(clean)) as result:
                self.assertFalse(result.getexif())
                self.assertEqual(result.format, "PNG")
            self.assertEqual(len(digest), 64)

    def test_non_image_rejected(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"bad.png"
            path.write_text("not an image")
            with self.assertRaisesRegex(ReviewRequired, "invalid_image"):
                sanitize_image(path)

    def test_non_supported_format(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"gif.png"
            Image.new("RGB", (100, 100)).save(path, format="GIF")
            with self.assertRaisesRegex(ReviewRequired, "unsupported_image_format"):
                sanitize_image(path)

    def test_image_dimensions_and_bytes_bounded(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"small.png"
            Image.new("RGB", (20, 20)).save(path)
            with self.assertRaisesRegex(ReviewRequired, "image_dimensions_out_of_range"):
                sanitize_image(path)
            path.write_bytes(b"x"*(10*1024*1024+1))
            with self.assertRaisesRegex(ReviewRequired, "image_too_large"):
                sanitize_image(path)

    def test_output_does_not_overwrite_or_follow_symlink(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"output"
            write_private(path, "first")
            with self.assertRaises(FileExistsError):
                write_private(path, "second")
            self.assertEqual(path.read_text(), "first")
            link = Path(folder)/"link"
            try:
                link.symlink_to(path)
            except OSError:
                return
            with self.assertRaises(FileExistsError):
                write_private(link, "third")
            self.assertEqual(path.read_text(), "first")

    def test_local_vision_schema_request_and_response(self):
        envelope = json.dumps({"done": True, "message": {"content": json.dumps(fixture())}}).encode()
        conn = FakeConnection(FakeResponse(envelope))
        with patch("fakturama_automation.extraction.http.client.HTTPConnection", return_value=conn) as factory:
            result = extract_local_vision(b"clean png", "local-vision:latest")
            factory.assert_called_once_with("127.0.0.1", 11434, timeout=120)
        self.assertEqual(result.external_reference, fixture()["external_reference"])
        self.assertTrue(conn.closed)
        payload = json.loads(conn.requests[0][1]["body"])
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["format"]["additionalProperties"])
        self.assertEqual(payload["format"]["$defs"]["Item"]["properties"]["unit_net"]["type"], "string")
        self.assertNotIn("tools", payload)
        self.assertIn("images", payload["messages"][1])

    def test_vision_incomplete_and_tool_call_rejected(self):
        for envelope, code in [({"done": False}, "vision_response_incomplete"),
                               ({"done": True, "message": {"content": "{}", "tool_calls": ["anything"]}}, "vision_unexpected_tool_request")]:
            conn = FakeConnection(FakeResponse(json.dumps(envelope).encode()))
            with patch("fakturama_automation.extraction.http.client.HTTPConnection", return_value=conn):
                with self.assertRaisesRegex(ReviewRequired, code):
                    extract_local_vision(b"png", "local-vision")

    def test_vision_oversized_response_rejected(self):
        conn = FakeConnection(FakeResponse(b" "*(1024*1024+1)))
        with patch("fakturama_automation.extraction.http.client.HTTPConnection", return_value=conn):
            with self.assertRaisesRegex(ReviewRequired, "json_too_large"):
                extract_local_vision(b"png", "local-vision")

    def test_cloud_or_invalid_model_rejected_without_network(self):
        for name in ["", "model-cloud", "vision;cmd", "vision model"]:
            with self.subTest(name=name), patch("fakturama_automation.extraction.http.client.HTTPConnection") as factory:
                with self.assertRaises(ReviewRequired):
                    extract_local_vision(b"png", name)
                factory.assert_not_called()
