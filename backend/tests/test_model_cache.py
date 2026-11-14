"""Tests for the shared ONNX model cache & execution-provider helpers."""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from services import model_cache


class _FakeOrt:
    def __init__(self, providers: list[str]) -> None:
        self._providers = providers

    def get_available_providers(self) -> list[str]:
        return self._providers


class _FakeSession:
    def __init__(self, providers: list[str] | None = None) -> None:
        self._providers = providers

    def get_providers(self) -> list[str]:
        if self._providers is None:
            raise AssertionError("session was never initialised")
        return self._providers


class UrlFilenameTests(unittest.TestCase):
    def test_ultralytics_weights_keep_their_own_name(self) -> None:
        self.assertEqual(
            model_cache.url_filename(
                "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11m.onnx"
            ),
            "yolo11m.onnx",
        )

    def test_huggingface_weights_are_prefixed_with_the_repository(self) -> None:
        self.assertEqual(
            model_cache.url_filename(
                "https://huggingface.co/onnx-community/depth-anything-v2-base/resolve/main/onnx/model.onnx"
            ),
            "depth-anything-v2-base-model.onnx",
        )
        self.assertEqual(
            model_cache.url_filename(
                "https://huggingface.co/onnx-community/depth-anything-v2-large/resolve/main/onnx/model.onnx"
            ),
            "depth-anything-v2-large-model.onnx",
        )

    def test_non_model_urls_are_rejected(self) -> None:
        self.assertEqual(model_cache.url_filename(""), "")
        self.assertEqual(model_cache.url_filename("https://example.com/readme.txt"), "")


class ProviderSelectionTests(unittest.TestCase):
    def test_cuda_is_requested_first_when_available(self) -> None:
        ort = _FakeOrt(["CPUExecutionProvider", "CUDAExecutionProvider"])
        self.assertEqual(
            model_cache.ort_providers(ort),
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
        )

    def test_missing_cuda_provider_is_not_requested(self) -> None:
        self.assertEqual(
            model_cache.ort_providers(_FakeOrt(["CPUExecutionProvider"])),
            ["CPUExecutionProvider"],
        )

    def test_cpu_remains_the_last_resort(self) -> None:
        self.assertEqual(
            model_cache.ort_providers(_FakeOrt(["AzureExecutionProvider"])),
            ["CPUExecutionProvider"],
        )

    def test_bound_provider_is_the_first_session_provider(self) -> None:
        session = _FakeSession(["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.assertEqual(model_cache.session_provider(session), "CUDAExecutionProvider")
        self.assertEqual(model_cache.session_provider(_FakeSession()), "CPUExecutionProvider")
        self.assertEqual(model_cache.session_provider(object()), "CPUExecutionProvider")


class SessionLoggingTests(unittest.TestCase):
    def test_log_reports_device_provider_and_weight_tier(self) -> None:
        logger = logging.getLogger("fastframe.test.model-cache")
        with mock.patch.dict("os.environ", {"APP_EDITION": "pro"}):
            with self.assertLogs(logger, level="INFO") as captured:
                model_cache.log_session_ready(
                    logger,
                    label="YOLO tracking",
                    model_path="/models/yolo11m.onnx",
                    session=_FakeSession(["CUDAExecutionProvider"]),
                )
        message = captured.output[0]
        self.assertIn("weights=Pro", message)
        self.assertIn("device=CUDA", message)
        self.assertIn("yolo11m.onnx", message)

    def test_pro_cpu_fallback_is_warned_about(self) -> None:
        logger = logging.getLogger("fastframe.test.model-cache")
        with mock.patch.dict("os.environ", {"APP_EDITION": "pro"}):
            with self.assertLogs(logger, level="WARNING") as captured:
                model_cache.log_session_ready(
                    logger,
                    label="monocular depth",
                    model_path="/models/midas-v21-small.onnx",
                    session=_FakeSession(["CPUExecutionProvider"]),
                )
        self.assertIn("fell back to the CPU provider", captured.output[0])

    def test_standard_edition_does_not_warn_on_cpu(self) -> None:
        logger = logging.getLogger("fastframe.test.model-cache")
        with mock.patch.dict("os.environ", {"APP_EDITION": "standard"}):
            with self.assertNoLogs(logger, level="WARNING"):
                model_cache.log_session_ready(
                    logger,
                    label="YOLO tracking",
                    model_path="/models/yolo11s.onnx",
                    session=_FakeSession(["CPUExecutionProvider"]),
                )

    def test_weight_tier_tracks_the_edition(self) -> None:
        cases = {"lite": "Lite", "standard": "Standard", "pro": "Pro", "community": "Standard"}
        for edition, label in cases.items():
            with self.subTest(edition=edition):
                with mock.patch.dict("os.environ", {"APP_EDITION": edition}):
                    self.assertEqual(model_cache.weights_label(), label)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
