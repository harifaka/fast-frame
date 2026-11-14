"""Tests for the three delivery editions (lite / standard / pro)."""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException

import config
from routers import ai


class EditionNormalisationTests(unittest.TestCase):
    def test_known_editions_are_returned_verbatim(self) -> None:
        for edition in ("lite", "standard", "pro"):
            with self.subTest(edition=edition):
                with mock.patch.dict("os.environ", {"APP_EDITION": edition.upper()}):
                    self.assertEqual(config.app_edition(), edition)

    def test_missing_edition_defaults_to_standard(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(config.app_edition(), "standard")

    def test_legacy_and_unknown_values_fall_back_to_standard(self) -> None:
        for raw in ("community", "cpu", "gpu", "something-else", ""):
            with self.subTest(raw=raw):
                with mock.patch.dict("os.environ", {"APP_EDITION": raw}):
                    self.assertEqual(config.app_edition(), "standard")


class AiFeatureTests(unittest.TestCase):
    def test_lite_disables_the_ai_runtime(self) -> None:
        with mock.patch.dict("os.environ", {"APP_EDITION": "lite"}):
            self.assertTrue(config.is_lite_edition())
            self.assertFalse(config.ai_features_enabled())

    def test_standard_and_pro_keep_ai_enabled(self) -> None:
        for edition in ("standard", "pro"):
            with self.subTest(edition=edition):
                with mock.patch.dict("os.environ", {"APP_EDITION": edition}):
                    self.assertFalse(config.is_lite_edition())
                    self.assertTrue(config.ai_features_enabled())


class AiRouteGuardTests(unittest.TestCase):
    def test_lite_rejects_ai_only_routes(self) -> None:
        with mock.patch.dict("os.environ", {"APP_EDITION": "lite"}):
            with self.assertRaises(HTTPException) as caught:
                ai.require_ai_runtime()
        self.assertEqual(caught.exception.status_code, 503)

    def test_standard_allows_ai_routes(self) -> None:
        with mock.patch.dict("os.environ", {"APP_EDITION": "standard"}):
            self.assertIsNone(ai.require_ai_runtime())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
