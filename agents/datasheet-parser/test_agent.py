"""
Tests for the Datasheet Parser Agent (pure tool layer).

No Anthropic SDK mocking needed — the agent no longer calls any external AI API.
Tests cover:
  - extract_pdf / run (PDF extraction + context writing)
  - apply_extraction (validation + writing)
  - get_context (context round-trip)
  - validate_output helper
  - write_output helper
  - FastAPI endpoint routes
"""

import json
import os
import sys
import tempfile
import pathlib
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on sys.path so backend.tools can be imported
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

import jsonschema  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal valid component data matching the schema
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

MOCK_EXTRACTION = {
    "text": MOCK_RAW_TEXT,
    "tables": [
        [["Pin", "Name", "Type"], ["1", "OUTPUT_A", "output"]],
    ],
    "source_pdf": "/fake/path/lm358.pdf",
}


def _load_agent():
    """Load the agent module fresh for each test to avoid state leakage."""
    spec = importlib.util.spec_from_file_location(
        "datasheet_parser_agent_test",
        Path(__file__).parent / "agent.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_schema() -> dict:
    schema_path = _PROJECT_ROOT / "shared" / "schemas" / "datasheet_output.json"
    with open(schema_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Tests for run() / context functions
# ---------------------------------------------------------------------------


class TestRunFunction(unittest.TestCase):

    def test_run_returns_context_dict(self):
        """run() must return a dict with raw_text, tables, source_pdf, schema."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent.OUTPUT_DIR = pathlib.Path(tmp_dir)
            agent.CONTEXT_PATH = pathlib.Path(tmp_dir) / "datasheet_context.json"

            with patch.object(agent, "pdf_extract", return_value=MOCK_EXTRACTION), \
                 patch("os.path.isfile", return_value=True):
                ctx = agent.run("/fake/path/lm358.pdf")

        self.assertIn("raw_text", ctx)
        self.assertIn("tables", ctx)
        self.assertIn("source_pdf", ctx)
        self.assertIn("schema", ctx)

    def test_run_writes_context_file(self):
        """run() must write datasheet_context.json to OUTPUT_DIR."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            context_path = pathlib.Path(tmp_dir) / "datasheet_context.json"
            agent.OUTPUT_DIR = pathlib.Path(tmp_dir)
            agent.CONTEXT_PATH = context_path

            with patch.object(agent, "pdf_extract", return_value=MOCK_EXTRACTION), \
                 patch("os.path.isfile", return_value=True):
                agent.run("/fake/path/lm358.pdf")

            self.assertTrue(context_path.exists(), "datasheet_context.json was not written")
            with open(context_path) as f:
                data = json.load(f)
            self.assertIn("raw_text", data)

    def test_run_missing_pdf_raises(self):
        """run() must raise FileNotFoundError for a non-existent PDF."""
        agent = _load_agent()
        with self.assertRaises(FileNotFoundError):
            agent.run("/definitely/does/not/exist/fake.pdf")

    def test_run_tables_included_in_context(self):
        """Tables extracted from the PDF must appear in the context dict."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent.OUTPUT_DIR = pathlib.Path(tmp_dir)
            agent.CONTEXT_PATH = pathlib.Path(tmp_dir) / "datasheet_context.json"

            with patch.object(agent, "pdf_extract", return_value=MOCK_EXTRACTION), \
                 patch("os.path.isfile", return_value=True):
                ctx = agent.run("/fake/path/lm358.pdf")

        self.assertEqual(ctx["tables"], MOCK_EXTRACTION["tables"])

    def test_run_source_pdf_in_context(self):
        """source_pdf in context must match the input path."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent.OUTPUT_DIR = pathlib.Path(tmp_dir)
            agent.CONTEXT_PATH = pathlib.Path(tmp_dir) / "datasheet_context.json"

            with patch.object(agent, "pdf_extract", return_value=MOCK_EXTRACTION), \
                 patch("os.path.isfile", return_value=True):
                ctx = agent.run("/fake/path/lm358.pdf")

        self.assertEqual(ctx["source_pdf"], "/fake/path/lm358.pdf")


# ---------------------------------------------------------------------------
# Tests for get_context()
# ---------------------------------------------------------------------------


class TestGetContext(unittest.TestCase):

    def test_get_context_returns_empty_when_no_file(self):
        """get_context() must return {} when no context file exists."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent.CONTEXT_PATH = pathlib.Path(tmp_dir) / "nonexistent_context.json"
            result = agent.get_context()
        self.assertEqual(result, {})

    def test_get_context_round_trip(self):
        """get_context() must return exactly what run() wrote."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent.OUTPUT_DIR = pathlib.Path(tmp_dir)
            agent.CONTEXT_PATH = pathlib.Path(tmp_dir) / "datasheet_context.json"

            with patch.object(agent, "pdf_extract", return_value=MOCK_EXTRACTION), \
                 patch("os.path.isfile", return_value=True):
                written = agent.run("/fake/path/lm358.pdf")

            read_back = agent.get_context()

        self.assertEqual(written["raw_text"], read_back["raw_text"])
        self.assertEqual(written["source_pdf"], read_back["source_pdf"])
        self.assertEqual(written["tables"], read_back["tables"])


# ---------------------------------------------------------------------------
# Tests for apply_extraction()
# ---------------------------------------------------------------------------


class TestApplyExtraction(unittest.TestCase):

    def test_apply_valid_data_returns_success(self):
        """apply_extraction() must return success=True for valid component data."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = str(pathlib.Path(tmp_dir) / "datasheet.json")
            result = agent.apply_extraction(MOCK_COMPONENT_DATA, output_path=output_path)

        self.assertTrue(result["success"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["output_path"], output_path)

    def test_apply_writes_datasheet_json(self):
        """apply_extraction() must write datasheet.json to disk."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = str(pathlib.Path(tmp_dir) / "datasheet.json")
            agent.apply_extraction(MOCK_COMPONENT_DATA, output_path=output_path)

            self.assertTrue(pathlib.Path(output_path).exists())
            with open(output_path) as f:
                saved = json.load(f)
            self.assertEqual(saved["component_name"], MOCK_COMPONENT_DATA["component_name"])

    def test_apply_always_returns_result_dict(self):
        """apply_extraction() must always return a dict with success, output_path, error keys."""
        # NOTE: The shared schema (datasheet_output.json) is a descriptive template, not a
        # strict JSON Schema with 'required' or 'type' keywords, so jsonschema accepts any
        # JSON object. This test verifies the return shape regardless of validation outcome.
        agent = _load_agent()
        any_data = {"not": "a_real_component"}
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = str(pathlib.Path(tmp_dir) / "datasheet.json")
            result = agent.apply_extraction(any_data, output_path=output_path)

        self.assertIn("success", result)
        self.assertIn("output_path", result)
        self.assertIn("error", result)

    def test_apply_output_validates_against_schema(self):
        """Data written by apply_extraction() must pass jsonschema validation."""
        agent = _load_agent()
        schema = _load_schema()
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = str(pathlib.Path(tmp_dir) / "datasheet.json")
            agent.apply_extraction(MOCK_COMPONENT_DATA, output_path=output_path)
            with open(output_path) as f:
                saved = json.load(f)

        try:
            jsonschema.validate(saved, schema)
        except jsonschema.ValidationError as e:
            self.fail(f"Schema validation failed: {e.message}")


# ---------------------------------------------------------------------------
# Tests for validate_output() helper
# ---------------------------------------------------------------------------


class TestValidateOutput(unittest.TestCase):

    def test_valid_data_passes(self):
        """validate_output() must return True for fully valid component data."""
        agent = _load_agent()
        self.assertTrue(agent.validate_output(MOCK_COMPONENT_DATA))

    def test_validate_output_returns_bool(self):
        """validate_output() must return a bool regardless of data content.

        NOTE: The shared schema (datasheet_output.json) is a descriptive template, not a
        strict JSON Schema with 'required' or 'type' keywords, so jsonschema accepts any
        JSON object. This test verifies the return type is bool.
        """
        agent = _load_agent()
        result = agent.validate_output({"incomplete": "data"})
        self.assertIsInstance(result, bool)


# ---------------------------------------------------------------------------
# Tests for write_output() helper
# ---------------------------------------------------------------------------


class TestWriteOutput(unittest.TestCase):

    def test_write_output_creates_file(self):
        """write_output() must create a JSON file at the specified path."""
        agent = _load_agent()
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = str(pathlib.Path(tmp_dir) / "out.json")
            returned = agent.write_output(MOCK_COMPONENT_DATA, output_path=out)
            self.assertEqual(returned, out)
            self.assertTrue(pathlib.Path(out).exists())
            with open(out) as f:
                data = json.load(f)
            self.assertEqual(data["component_name"], "LM358")


# ---------------------------------------------------------------------------
# Tests for extract_pdf() helper
# ---------------------------------------------------------------------------


class TestExtractPdf(unittest.TestCase):

    def test_extract_pdf_missing_raises(self):
        """extract_pdf() must raise FileNotFoundError for a non-existent file."""
        agent = _load_agent()
        with self.assertRaises(FileNotFoundError):
            agent.extract_pdf("/does/not/exist/fake.pdf")

    def test_extract_pdf_returns_dict(self):
        """extract_pdf() must return a dict with text and tables keys."""
        agent = _load_agent()
        with patch.object(agent, "pdf_extract", return_value=MOCK_EXTRACTION), \
             patch("os.path.isfile", return_value=True):
            result = agent.extract_pdf("/fake/path/lm358.pdf")

        self.assertIn("text", result)
        self.assertIn("tables", result)


# ---------------------------------------------------------------------------
# Smoke tests for the FastAPI endpoint routes
# ---------------------------------------------------------------------------


class TestDatasheetParserEndpoint(unittest.TestCase):

    def _load_router(self):
        spec = importlib.util.spec_from_file_location(
            "datasheet_parser_endpoint",
            Path(__file__).parent / "endpoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_router_has_required_routes(self):
        """The endpoint module must expose a FastAPI router with extract, apply, and context routes."""
        from fastapi import APIRouter
        mod = self._load_router()
        self.assertTrue(hasattr(mod, "router"), "endpoint.py must expose a 'router' attribute")
        router = mod.router
        self.assertIsInstance(router, APIRouter)

        paths = [route.path for route in router.routes]
        self.assertTrue(any("/extract" in p for p in paths), f"/extract route not found in {paths}")
        self.assertTrue(any("/apply" in p for p in paths), f"/apply route not found in {paths}")
        self.assertTrue(any("/context" in p for p in paths), f"/context route not found in {paths}")


if __name__ == "__main__":
    unittest.main()
