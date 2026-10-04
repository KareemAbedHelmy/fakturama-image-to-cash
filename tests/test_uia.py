import unittest
from types import SimpleNamespace

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.uia import Locator, invoke_once, set_value_verified, unique_control, wait_stable


class Control:
    def __init__(self, name, auto_id="", visible=True, enabled=True):
        self.element_info = SimpleNamespace(name=name, automation_id=auto_id)
        self.visible, self.enabled = visible, enabled
        self.value = ""
        self.invocations = 0
        self.container = None

    def is_visible(self): return self.visible
    def is_enabled(self): return self.enabled
    def set_edit_text(self, value): self.value = value
    def get_value(self): return self.value
    def invoke(self): self.invocations += 1
    def parent(self): return self.container


class Scope:
    def __init__(self, controls, enabled=True):
        self.controls = controls
        self.enabled = enabled
        for control in controls:
            control.container = self
    def descendants(self, **kwargs): return self.controls
    def is_enabled(self): return self.enabled


class UIATests(unittest.TestCase):
    def test_enabled_child_of_disabled_toolbar_not_invoked(self):
        control = Control("Invoice")
        scope = Scope([control])
        toolbar = Control("Toolbar", enabled=False)
        toolbar.container = scope
        control.container = toolbar
        with self.assertRaisesRegex(ReviewRequired, "control_missing_or_ambiguous"):
            invoke_once(scope, Locator("Button", name="Invoice"))
        self.assertEqual(control.invocations, 0)

    def test_detached_control_not_invoked(self):
        control = Control("Save")
        scope = Scope([control])
        control.container = None
        with self.assertRaisesRegex(ReviewRequired, "control_missing_or_ambiguous"):
            invoke_once(scope, Locator("Button", name="Save"))
        self.assertEqual(control.invocations, 0)

    def test_unreadable_ancestor_blocks_without_invocation(self):
        class Unreadable(Control):
            def parent(self): raise RuntimeError("unavailable")
        control = Unreadable("Save")
        with self.assertRaisesRegex(ReviewRequired, "control_ancestry_unavailable"):
            invoke_once(Scope([control]), Locator("Button", name="Save"))
        self.assertEqual(control.invocations, 0)

    def test_exact_identity_required(self):
        with self.assertRaises(ValueError):
            Locator("Button")
        scope = Scope([Control("Save"), Control("Save copy")])
        self.assertEqual(unique_control(scope, Locator("Button", name="Save")).element_info.name, "Save")

    def test_ambiguous_control_not_invoked(self):
        controls = [Control("Save"), Control("Save")]
        with self.assertRaisesRegex(ReviewRequired, "control_missing_or_ambiguous"):
            invoke_once(Scope(controls), Locator("Button", name="Save"))
        self.assertEqual(sum(c.invocations for c in controls), 0)

    def test_visible_enabled_filter(self):
        control = Control("Save", auto_id="good")
        scope = Scope([Control("Save", visible=False), Control("Save", enabled=False), control])
        self.assertIs(unique_control(scope, Locator("Button", name="Save")), control)

    def test_literal_source_value_not_hotkeys(self):
        control = Control("Reference", auto_id="reference")
        text = "%{F4} ^a $(shell command)"
        set_value_verified(Scope([control]), Locator("Edit", automation_id="reference"), text)
        self.assertEqual(control.value, text)

    def test_readback_mismatch_blocks(self):
        class WrongValue(Control):
            def get_value(self): return "wrong"
        with self.assertRaisesRegex(ReviewRequired, "uia_value_readback_mismatch"):
            set_value_verified(Scope([WrongValue("Ref")]), Locator("Edit", name="Ref"), "correct")

    def test_uncertain_invoke_not_retried(self):
        class Uncertain(Control):
            def invoke(self):
                self.invocations += 1
                raise RuntimeError("callback failed after action")
        control = Uncertain("Save")
        with self.assertRaisesRegex(ReviewRequired, "uia_invoke_uncertain"):
            invoke_once(Scope([control]), Locator("Button", name="Save"))
        self.assertEqual(control.invocations, 1)

    def test_stable_wait_requires_readiness(self):
        sequence = iter([(False, ()), (False, ()), (True, ("row",)), (True, ("row",)), (True, ("row",))])
        result = wait_stable(lambda: next(sequence), lambda s: s[0], timeout=1, interval=.001)
        self.assertEqual(result, (True, ("row",)))

    def test_stable_empty_but_loading_times_out(self):
        with self.assertRaisesRegex(ReviewRequired, "observation_timeout"):
            wait_stable(lambda: (False, ()), lambda s: s[0], timeout=.01, interval=.001)
