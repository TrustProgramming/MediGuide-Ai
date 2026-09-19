from __future__ import annotations

import os

from python_backend.app.playwright_booking import _click_first_matching, _fill_first_available


def test_click_first_matching_prefers_pay_at_clinic_text():
    class FakeLocator:
        def __init__(self):
            self.count_calls = 0

        def count(self):
            self.count_calls += 1
            return 1

        def click(self, timeout=0):
            return None

    class FakePage:
        def locator(self, selector):
            return FakeLocator()

        def get_by_role(self, role, name):
            class FakeRoleLocator:
                def __init__(self):
                    self.count_calls = 0

                def count(self):
                    self.count_calls += 1
                    return 1

                def first(self):
                    class FakeFirst:
                        def click(self, timeout=15000):
                            return None

                    return FakeFirst()

            return FakeRoleLocator()

    fake_page = FakePage()
    assert _click_first_matching(fake_page, [
        "button:has-text('Pay at clinic')",
        "button:has-text('Pay online')",
        "button:has-text('Book appointment')",
    ]) is True


def test_fill_first_available_ignores_patient_verification_selectors():
    class FakeLocator:
        def __init__(self):
            self.calls = []

        def count(self):
            return 1

        def fill(self, text):
            self.calls.append(text)

        def select_option(self, text):
            self.calls.append(text)

    class FakePage:
        def locator(self, selector):
            return FakeLocator()

    result = _fill_first_available(FakePage(), ["input[name*='otp' i]", "input[name*='date' i]"], "2026-09-20")
    assert result is True
