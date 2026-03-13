"""
net_namer.py — Analyzes component pin functions and suggests descriptive net names.

Rules (from agents/schematic/CLAUDE.md):
  - Power nets:     VCC_3V3, VCC_5V, VCC_12V  (include voltage)
  - Interface nets: I2C_SDA, SPI_MOSI, UART_TX
  - Control nets:   EN_POWER, nRESET  (active-low prefixed with n)
  - Analog nets:    ADC_IN, DAC_OUT, VREF
  - Never use NET001-style names
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Keyword → canonical net name mapping
# Checked against normalised pin names/functions (upper-case, underscores).
# Order matters: more-specific patterns first.
# ---------------------------------------------------------------------------

_POWER_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bVCC\b"),                   "VCC"),
    (re.compile(r"\bVDD\b"),                   "VDD"),
    (re.compile(r"\b3[._]?3\s*V\b", re.I),    "VCC_3V3"),
    (re.compile(r"\b5\s*V\b", re.I),           "VCC_5V"),
    (re.compile(r"\b12\s*V\b", re.I),          "VCC_12V"),
    (re.compile(r"\b1[._]?8\s*V\b", re.I),    "VCC_1V8"),
    (re.compile(r"\bAVCC\b"),                  "AVCC"),
    (re.compile(r"\bAVDD\b"),                  "AVDD"),
    (re.compile(r"\bVREF\b"),                  "VREF"),
    (re.compile(r"\bVIN\b"),                   "VIN"),
    (re.compile(r"\bVOUT\b"),                  "VOUT"),
    (re.compile(r"\bGND\b"),                   "GND"),
    (re.compile(r"\bAGND\b"),                  "AGND"),
    (re.compile(r"\bPGND\b"),                  "PGND"),
    (re.compile(r"\bDGND\b"),                  "GND"),
]

_INTERFACE_KEYWORDS: list[tuple[re.Pattern, str]] = [
    # I2C
    (re.compile(r"\bSDA\b"),                   "I2C_SDA"),
    (re.compile(r"\bSCL\b"),                   "I2C_SCL"),
    (re.compile(r"\bI2C_SDA\b"),               "I2C_SDA"),
    (re.compile(r"\bI2C_SCL\b"),               "I2C_SCL"),
    # SPI
    (re.compile(r"\bMOSI\b"),                  "SPI_MOSI"),
    (re.compile(r"\bMISO\b"),                  "SPI_MISO"),
    (re.compile(r"\bSCK\b"),                   "SPI_SCK"),
    (re.compile(r"\bSS\b|\bCSN?\b|\bNCS\b"),   "SPI_CS"),
    # UART
    (re.compile(r"\bTX\b|\bTXD\b"),            "UART_TX"),
    (re.compile(r"\bRX\b|\bRXD\b"),            "UART_RX"),
    (re.compile(r"\bRTS\b"),                   "UART_RTS"),
    (re.compile(r"\bCTS\b"),                   "UART_CTS"),
    # USB
    (re.compile(r"\bUSB_D\+\b|\bDP\b"),        "USB_DP"),
    (re.compile(r"\bUSB_D\-\b|\bDN\b"),        "USB_DM"),
    # CAN
    (re.compile(r"\bCANH\b"),                  "CAN_H"),
    (re.compile(r"\bCANL\b"),                  "CAN_L"),
    # PWM / GPIO
    (re.compile(r"\bPWM\b"),                   "PWM_OUT"),
    (re.compile(r"\bINT\b|\bIRQ\b"),           "nINT"),
    (re.compile(r"\bDRDY\b|\bDATA_READY\b"),   "nDRDY"),
]

_CONTROL_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bRESET\b|\bRSTN?\b"),        "nRESET"),
    (re.compile(r"\bEN\b|\bENA\b|\bENABLE\b"),  "EN"),
    (re.compile(r"\bPDN\b|\bPOWER_?DOWN\b"),    "nPDN"),
    (re.compile(r"\bSTANDBY\b|\bSTBY\b"),       "nSTBY"),
    (re.compile(r"\bOE\b|\bOUT_?EN\b"),         "nOE"),
    (re.compile(r"\bWP\b|\bWRITE_?PROT\b"),     "nWP"),
    (re.compile(r"\bFAULT\b|\bFLT\b"),         "nFAULT"),
    (re.compile(r"\bALERT\b"),                  "nALERT"),
    (re.compile(r"\bBOOT\b"),                   "BOOT"),
    (re.compile(r"\bSEL\b|\bADDR\b"),          "ADDR_SEL"),
]

_ANALOG_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bADC\b|\bAIN\b|\bANALOG_?IN\b"),   "ADC_IN"),
    (re.compile(r"\bDAC\b|\bAOUT\b|\bANALOG_?OUT\b"), "DAC_OUT"),
    (re.compile(r"\bFB\b|\bFEEDBACK\b"),               "FB"),
    (re.compile(r"\bSENSE\b|\bISENSE\b"),              "I_SENSE"),
    (re.compile(r"\bVMON\b|\bV_MON\b"),                "V_MON"),
    (re.compile(r"\bCOMP\b|\bCOMPARAT\b"),             "COMP_OUT"),
]


def _normalise(text: str) -> str:
    """Upper-case, replace common separators with underscore for matching."""
    return re.sub(r"[\s\-/\\]+", "_", text.upper().strip())


def _match_all_tables(text: str) -> str | None:
    """Return the first matching canonical net name from all tables, or None."""
    for table in (_POWER_KEYWORDS, _INTERFACE_KEYWORDS, _CONTROL_KEYWORDS, _ANALOG_KEYWORDS):
        for pattern, name in table:
            if pattern.search(text):
                return name
    return None


def _deduplicate(names: dict[str, str]) -> dict[str, str]:
    """
    If two different pin_functions map to the same net name, append _1, _2
    to keep uniqueness (except for GND/VCC which are intentionally shared).
    """
    shared_ok = {"GND", "AGND", "PGND", "VCC", "VDD", "AVCC", "AVDD"}
    seen: dict[str, int] = {}
    result: dict[str, str] = {}
    for pin_fn, net in names.items():
        if net in shared_ok:
            result[pin_fn] = net
            continue
        if net not in seen:
            seen[net] = 0
            result[pin_fn] = net
        else:
            seen[net] += 1
            result[pin_fn] = f"{net}_{seen[net]}"
    return result


def suggest_net_names(datasheet: dict[str, Any]) -> dict[str, str]:
    """
    Analyse datasheet.json (matches shared/schemas/datasheet_output.json) and
    return a mapping of {pin_function_string: suggested_net_name}.

    The caller (agent.py) passes this dict to the Anthropic prompt so Claude
    can use the pre-computed names rather than inventing NET001-style ones.
    """
    pins: list[dict[str, Any]] = datasheet.get("pins", [])
    suggestions: dict[str, str] = {}

    for pin in pins:
        pin_type: str = pin.get("type", "")
        pin_name: str = pin.get("name", "")
        pin_fn: str = pin.get("function", "")

        # NC pins → always "NC"
        if pin_type == "nc":
            suggestions[pin_fn or pin_name] = "NC"
            continue

        # Try matching combined text: name + function
        combined = _normalise(f"{pin_name} {pin_fn}")
        matched = _match_all_tables(combined)
        if matched:
            suggestions[pin_fn or pin_name] = matched
            continue

        # Try name alone
        matched = _match_all_tables(_normalise(pin_name))
        if matched:
            suggestions[pin_fn or pin_name] = matched
            continue

        # Try function alone
        if pin_fn:
            matched = _match_all_tables(_normalise(pin_fn))
            if matched:
                suggestions[pin_fn] = matched
                continue

        # Fallback: derive a readable name from the pin name
        if pin_name:
            safe = re.sub(r"[^A-Z0-9_]", "_", _normalise(pin_name)).strip("_")
            if safe:
                suggestions[pin_fn or pin_name] = safe

    return _deduplicate(suggestions)
