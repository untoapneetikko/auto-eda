"""
Tests for the Datasheet Parser Agent.

Approach: mock pdf_extractor and anthropic.Anthropic so the agent can be tested
without a real PDF file or live API key.
"""

import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

# Ensure project root is on sys.path so backend.tools can be imported
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

import jsonschema  # noqa: E402 — validate schema in tests


# ---------------------------------------------------------------------------
# Minimal valid component data that matches the schema
# ---------------------------------------------------------------------------

MOCK_RAW_TEXT = (
    "LM358 Dual Operational Amplifier\n"
    "Texas Instruments\n"
    "Package: DIP-8\n"
    "Pin 1: OUTPUT A\n"
    "Pin 2: IN- A\n"
    "Pin 3: IN+ A\n"
    "Pin 4: GND\n"
    "Pin 5: IN+ B\n"
    "Pin 6: IN- B\n"
    "Pin 7: OUTPUT B\n"
    "Pin 8: VCC\n"
    "Supply voltage: 3V to 32V\n"
    "Max output current: 40mA\n"
    "Typical application: voltage follower, comparator\n"
)

MOCK_COMPONENT_DATA = {
    "component_name": "LM358",
    "manufacturer": "Texas Instruments",
    "package": "DIP-8",
    "pins": [
        {"number": 1, "name": "OUTPUT_A", "type": "output", "function": "Op-amp A output"},
        {"number": 2, "name": "IN_NEG_A", "type": "input", "function": "Op-amp A inverting input"},
        {"number": 3, "name": "IN_POS_A", "type": "input", "function": "Op-amp A non-inverting input"},
        {"number": 4, "name": "GND", "type": "power", "function": "Ground"},
        {"number": 5, "name": "IN_POS_B", "type": "input", "function": "Op-amp B non-inverting input"},
        {"number": 6, "name": "IN_NEG_B", "type": "input", "function": "Op-amp B inverting input"},
        {"number": 7, "name": "OUTPUT_B", "type": "output", "function": "Op-amp B output"},
        {"number": 8, "name": "VCC", "type": "power", "function": "Positive supply voltage"},
    ],
    "footprint": {
        "standard": "DIP-8",
        "pad_count": 8,
        "pitch_mm": 2.54,
        "courtyard_mm": {"x": 10.16, "y": 7.62},
    },
    "electrical": {
        "vcc_min": 3.0,
        "vcc_max": 32.0,
        "i_max_ma": 40.0,
    },
    "example_application": {
        "description": "Voltage follower configuration",
        "required_passives": ["10k resistor", "100nF decoupling capacitor"],
        "typical_schematic_notes": "Connect IN+ to signal, IN- to OUTPUT for unity gain buffer",
    },
    "raw_text": MOCK_RAW_TEXT[:500],
    "source_pdf": "/fake/path/lm358.pdf",
}


def _load_schema() -> dict:
    schema_path = _PROJECT_ROOT / "shared" / "schemas" / "datasheet_output.json"
    with open(schema_path) as f:
        return json.load(f)


def _build_mock_anthropic_client(response_json: dict):
    """Build a mock anthropic.Anthropic client that returns response_json as text."""
    mock_content = MagicMock()
    mock_content.text = json.dumps(response_json)

    mock_message = MagicMock()
    mock_message.content = [mock_content]

    mock_messages = MagicMock()
    mock_messages.create.return_value = mock_message

    mock_client = MagicMock()
    mock_client.messages = mock_messages

    return mock_client


class TestDatasheetParserAgent(unittest.TestCase):

    def setUp(self):
        # Ensure ANTHROPIC_API_KEY is set for the agent module
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-mock-key"

    def _run_agent_with_mocks(self, component_data: dict, pdf_path: str = "/fake/path/lm358.pdf"):
        """Helper: patch pdf_extractor, anthropic, os.path.isfile, and OUTPUT_DIR, then call agent.run."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "datasheet_parser_agent_under_test",
            Path(__file__).parent / "agent.py",
        )
        agent_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_mod)

        mock_extraction = {
            "text": MOCK_RAW_TEXT,
            "tables": [],
            "source_pdf": pdf_path,
        }

        mock_client = _build_mock_anthropic_client(component_data)

        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Point OUTPUT_DIR and OUTPUT_PATH at temp dir
            agent_mod.OUTPUT_DIR = pathlib.Path(tmp_dir)
            agent_mod.OUTPUT_PATH = pathlib.Path(tmp_dir) / "datasheet.json"

            with patch.object(agent_mod, "pdf_extract", return_value=mock_extraction), \
                 patch("anthropic.Anthropic", return_value=mock_client), \
                 patch.object(agent_mod.anthropic, "Anthropic", return_value=mock_client), \
                 patch("os.path.isfile", return_value=True):
                result = agent_mod.run(pdf_path)

        return result

    def test_run_returns_dict(self):
        """agent.run() should return a Python dict."""
        result = self._run_agent_with_mocks(MOCK_COMPONENT_DATA)
        self.assertIsInstance(result, dict)

    def test_output_has_required_fields(self):
        """Output must contain all top-level schema fields."""
        result = self._run_agent_with_mocks(MOCK_COMPONENT_DATA)
        for field in ("component_name", "manufacturer", "package", "pins",
                      "footprint", "electrical", "example_application",
                      "raw_text", "source_pdf"):
            self.assertIn(field, result, f"Missing required field: {field}")

    def test_pins_is_list(self):
        """pins must be a non-empty list."""
        result = self._run_agent_with_mocks(MOCK_COMPONENT_DATA)
        self.assertIsInstance(result["pins"], list)
        self.assertGreater(len(result["pins"]), 0)

    def test_pin_types_are_valid(self):
        """All pin types must be one of the allowed values."""
        valid_types = {"power", "input", "output", "bidirectional", "passive", "nc"}
        result = self._run_agent_with_mocks(MOCK_COMPONENT_DATA)
        for pin in result["pins"]:
            self.assertIn(pin["type"], valid_types, f"Invalid pin type '{pin['type']}' for pin {pin['number']}")

    def test_output_validates_against_schema(self):
        """Output must pass jsonschema validation against datasheet_output.json."""
        result = self._run_agent_with_mocks(MOCK_COMPONENT_DATA)
        schema = _load_schema()
        # Should not raise
        try:
            jsonschema.validate(result, schema)
        except jsonschema.ValidationError as e:
            self.fail(f"Schema validation failed: {e.message} at {list(e.absolute_path)}")

    def test_source_pdf_preserved(self):
        """source_pdf in output must match the input pdf_path."""
        pdf_path = "/fake/path/lm358.pdf"
        result = self._run_agent_with_mocks(MOCK_COMPONENT_DATA, pdf_path=pdf_path)
        self.assertEqual(result["source_pdf"], pdf_path)

    def test_missing_api_key_raises(self):
        """agent.run() must raise RuntimeError when ANTHROPIC_API_KEY is absent."""
        import importlib.util, pathlib, tempfile
        spec = importlib.util.spec_from_file_location(
            "datasheet_parser_agent_no_key",
            Path(__file__).parent / "agent.py",
        )
        agent_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_mod)

        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with patch("os.path.isfile", return_value=True):
                with self.assertRaises(RuntimeError):
                    agent_mod.run("/fake/path/lm358.pdf")
        finally:
            if saved:
                os.environ["ANTHROPIC_API_KEY"] = saved

    def test_missing_pdf_raises(self):
        """agent.run() must raise FileNotFoundError for a non-existent PDF."""
        import importlib.util, pathlib, tempfile
        spec = importlib.util.spec_from_file_location(
            "datasheet_parser_agent_no_pdf",
            Path(__file__).parent / "agent.py",
        )
        agent_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_mod)

        with self.assertRaises(FileNotFoundError):
            agent_mod.run("/definitely/does/not/exist/fake.pdf")

    def test_invalid_json_response_raises(self):
        """agent.run() must raise ValueError when the model returns non-JSON text."""
        import importlib.util, pathlib, tempfile
        spec = importlib.util.spec_from_file_location(
            "datasheet_parser_agent_bad_json",
            Path(__file__).parent / "agent.py",
        )
        agent_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_mod)

        mock_extraction = {"text": MOCK_RAW_TEXT, "tables": [], "source_pdf": "/fake/path/lm358.pdf"}

        mock_content = MagicMock()
        mock_content.text = "This is not JSON at all."
        mock_message = MagicMock()
        mock_message.content = [mock_content]
        mock_messages = MagicMock()
        mock_messages.create.return_value = mock_message
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with tempfile.TemporaryDirectory() as tmp_dir:
            agent_mod.OUTPUT_DIR = pathlib.Path(tmp_dir)
            agent_mod.OUTPUT_PATH = pathlib.Path(tmp_dir) / "datasheet.json"

            with patch.object(agent_mod, "pdf_extract", return_value=mock_extraction), \
                 patch.object(agent_mod.anthropic, "Anthropic", return_value=mock_client), \
                 patch("os.path.isfile", return_value=True):
                with self.assertRaises(ValueError):
                    agent_mod.run("/fake/path/lm358.pdf")

    def test_markdown_fence_stripped(self):
        """agent.run() must handle model responses wrapped in markdown code fences."""
        import importlib.util, pathlib, tempfile
        spec = importlib.util.spec_from_file_location(
            "datasheet_parser_agent_fence",
            Path(__file__).parent / "agent.py",
        )
        agent_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_mod)

        mock_extraction = {"text": MOCK_RAW_TEXT, "tables": [], "source_pdf": "/fake/path/lm358.pdf"}
        fenced_response = "```json\n" + json.dumps(MOCK_COMPONENT_DATA) + "\n```"

        mock_content = MagicMock()
        mock_content.text = fenced_response
        mock_message = MagicMock()
        mock_message.content = [mock_content]
        mock_messages = MagicMock()
        mock_messages.create.return_value = mock_message
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with tempfile.TemporaryDirectory() as tmp_dir:
            agent_mod.OUTPUT_DIR = pathlib.Path(tmp_dir)
            agent_mod.OUTPUT_PATH = pathlib.Path(tmp_dir) / "datasheet.json"

            with patch.object(agent_mod, "pdf_extract", return_value=mock_extraction), \
                 patch.object(agent_mod.anthropic, "Anthropic", return_value=mock_client), \
                 patch("os.path.isfile", return_value=True):
                result = agent_mod.run("/fake/path/lm358.pdf")

        self.assertEqual(result["component_name"], MOCK_COMPONENT_DATA["component_name"])


class TestDatasheetParserEndpoint(unittest.TestCase):
    """Basic smoke tests for the FastAPI endpoint."""

    def _load_router(self):
        """Load the endpoint module and return the FastAPI router."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "datasheet_parser_endpoint",
            Path(__file__).parent / "endpoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_router_has_run_and_status_routes(self):
        """The endpoint module must expose a FastAPI router with /run and /status routes."""
        from fastapi import APIRouter
        mod = self._load_router()
        self.assertTrue(hasattr(mod, "router"), "endpoint.py must expose a 'router' attribute")
        router = mod.router
        self.assertIsInstance(router, APIRouter)

        paths = [route.path for route in router.routes]
        self.assertTrue(
            any("/run" in p for p in paths),
            f"/run route not found in {paths}",
        )
        self.assertTrue(
            any("/status" in p for p in paths),
            f"/status route not found in {paths}",
        )


if __name__ == "__main__":
    unittest.main()
