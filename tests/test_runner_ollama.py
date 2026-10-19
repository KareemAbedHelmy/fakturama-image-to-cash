import importlib.util
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_order_first", ROOT / "tools" / "run_order_first.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerOllamaTests(unittest.TestCase):
    def test_local_provider_does_not_require_anthropic_key(self):
        with tempfile.TemporaryDirectory() as folder:
            run = mock.Mock(out=Path(folder))
            run.ask.return_value = True
            source = json.loads((ROOT / "examples" / "order.provided.expected.json").read_text())

            def fake_cli(*args):
                if args[0] == "extract":
                    Path(args[args.index("--out") + 1]).write_text(json.dumps(source))
                return 0

            with (mock.patch.object(runner, "cli", side_effect=fake_cli) as cli,
                  mock.patch.dict(runner.os.environ, {"ANTHROPIC_API_KEY": ""}),
                  contextlib.redirect_stdout(io.StringIO())):
                result = runner.extract(run, "example.png", None, "ollama", "qwen3-vl:4b-instruct")
            self.assertEqual(result, str(Path(folder) / "confirmed.json"))
            self.assertEqual(cli.call_args_list[0].args[:5],
                             ("extract", "--provider", "ollama", "--image", "example.png"))
            self.assertIn("qwen3-vl:4b-instruct", cli.call_args_list[0].args)

    def test_local_provider_requires_explicit_model(self):
        with tempfile.TemporaryDirectory() as folder:
            run = mock.Mock(out=Path(folder))
            with self.assertRaises(SystemExit), mock.patch.object(runner, "cli") as cli:
                runner.extract(run, "example.png", None, "ollama")
            cli.assert_not_called()


if __name__ == "__main__":
    unittest.main()
