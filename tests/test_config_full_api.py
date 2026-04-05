"""Regression tests for /api/config/full write responses."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from argus.web import server


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class ConfigWriteApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_keys_are_returned_in_skipped(self) -> None:
        updates = {"dashboard": {"port": "8080", "unknown_key": "value"}}

        with (
            patch.object(server, "_HAS_CONFIG_API", True),
            patch.object(
                server,
                "write_config",
                return_value={
                    "restart_required": [],
                    "skipped": ["dashboard.unknown_key (unknown field)"],
                },
            ),
            patch.object(server.events, "log"),
            patch("argus.config_schema.validate") as validate_mock,
        ):
            validate_mock.return_value.errors = []
            validate_mock.return_value.warnings = []

            result = await server.config_write(_FakeRequest(updates))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["restart_required"], [])
        self.assertIn("dashboard.unknown_key (unknown field)", result["skipped"])


if __name__ == "__main__":
    unittest.main()
