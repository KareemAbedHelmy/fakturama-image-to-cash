import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fakturama_automation.domain import OrderInput
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.journal import Journal
from test_domain_finance import fixture, source


class JournalTests(unittest.TestCase):
    def test_duplicate_and_changed_input_block(self):
        with TemporaryDirectory() as folder, Journal(Path(folder)/"j.sqlite") as journal:
            key = journal.begin("workspace", source())
            journal.finish("workspace", key)
            with self.assertRaisesRegex(ReviewRequired, "duplicate_job"):
                journal.begin("workspace", source())
            data = fixture()
            data["order_date"] = "2026-09-29"
            with self.assertRaisesRegex(ReviewRequired, "changed_input_same_reference"):
                journal.begin("workspace", OrderInput.model_validate(data))

    def test_interrupted_run_lease_survives_connection_close(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"j.sqlite"
            with Journal(path) as journal:
                journal.begin("workspace", source())
            data = fixture()
            data["external_reference"] = "ANOTHER-ORDER"
            with Journal(path) as reopened:
                with self.assertRaisesRegex(ReviewRequired, "workspace_has_unresolved_run"):
                    reopened.begin("workspace", OrderInput.model_validate(data))

    def test_two_connections_cannot_own_same_workspace(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"j.sqlite"
            with Journal(path) as first, Journal(path) as second:
                first.begin("workspace", source())
                data = fixture()
                data["external_reference"] = "SECOND"
                with self.assertRaisesRegex(ReviewRequired, "workspace_has_unresolved_run"):
                    second.begin("workspace", OrderInput.model_validate(data))
                self.assertEqual(len(second.summary()), 1)

    def test_private_source_text_not_in_journal(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"j.sqlite"
            with Journal(path) as journal:
                key = journal.begin("workspace", source())
                journal.event("workspace", key, "save_order", "INTENT")
            data = path.read_bytes()
            self.assertNotIn(source().debtor.company.encode(), data)
            self.assertNotIn(source().external_reference.encode(), data)

    def test_symlink_journal_rejected(self):
        with TemporaryDirectory() as folder:
            target = Path(folder)/"target"
            target.write_text("anything")
            link = Path(folder)/"link"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ReviewRequired, "journal_symlink_rejected"):
                Journal(link)
