"""
Trace Width Calculator — determines PCB trace width based on current draw and net type.

Formula: width_mm = current_amps * 0.4  (IPC-2221 simplified)
Power/ground nets always get power classification.
High-speed nets get controlled impedance sizing.
"""

from __future__ import annotations

# Keywords that classify a net as power
POWER_KEYWORDS = {"VCC", "VDD", "GND", "PWR", "V3V3", "V5V", "V12V", "VBAT", "VSYS", "VIN", "VOUT", "AVCC", "AVDD", "AGND", "PGND", "DVDD"}

# Keywords that indicate high-speed signals
HIGHSPEED_KEYWORDS = {"CLK", "CLOCK", "SCK", "USB", "DP", "DM", "DIFF", "LVDS", "SERDES", "ETH", "MDIO", "RMII"}

# Keywords that indicate analog signals
ANALOG_KEYWORDS = {"AIN", "AOUT", "ADC", "DAC", "REF", "VREF", "SENSE", "AGND", "AVCC", "AVDD"}

# Minimum trace widths (mm)
MIN_SIGNAL_WIDTH_MM = 0.15
PREFERRED_SIGNAL_WIDTH_MM = 0.2
HIGHSPEED_50OHM_WIDTH_MM = 0.3   # 50Ω on standard FR4 2-layer
MIN_POWER_WIDTH_MM = 0.4         # 1A minimum for power traces


def classify_net(net_name: str) -> str:
    """
    Classify a net as power, signal, highspeed, or analog based on its name.

    Args:
        net_name: The net name string (e.g. "VCC", "CLK", "/data_out")

    Returns:
        One of "power", "highspeed", "analog", "signal"
    """
    upper = net_name.upper().lstrip("/\\")

    # Check power keywords
    for kw in POWER_KEYWORDS:
        if kw in upper:
            return "power"

    # Check high-speed keywords
    for kw in HIGHSPEED_KEYWORDS:
        if kw in upper:
            return "highspeed"

    # Check analog keywords
    for kw in ANALOG_KEYWORDS:
        if kw in upper:
            return "analog"

    return "signal"


def calculate_trace_width(net_name: str, current_amps: float = 0.1) -> dict:
    """
    Calculate the recommended trace width for a net.

    Args:
        net_name: Net name string (used for classification)
        current_amps: Expected current through the trace in amps (default 0.1A for signal)

    Returns:
        Dict with keys:
            net       — the net name
            type      — "power" | "signal" | "highspeed" | "analog"
            width_mm  — recommended trace width in mm
            rationale — human-readable explanation
    """
    net_type = classify_net(net_name)

    if net_type == "power":
        # IPC-2221 simplified: width = current * 0.4 mm/A
        calculated = current_amps * 0.4
        width_mm = max(calculated, MIN_POWER_WIDTH_MM)
        rationale = (
            f"Power trace: {current_amps}A × 0.4 mm/A = {calculated:.3f}mm, "
            f"enforcing minimum {MIN_POWER_WIDTH_MM}mm → {width_mm:.3f}mm"
        )

    elif net_type == "highspeed":
        # Controlled impedance: 50Ω on standard 2-layer FR4 ≈ 0.3mm
        width_mm = HIGHSPEED_50OHM_WIDTH_MM
        rationale = (
            f"High-speed signal: controlled 50Ω impedance on 2-layer FR4 → {width_mm}mm"
        )

    elif net_type == "analog":
        # Analog traces: slightly wider than digital signal, keep away from noise
        width_mm = PREFERRED_SIGNAL_WIDTH_MM
        rationale = (
            f"Analog signal: preferred width {width_mm}mm; "
            "route away from switching noise, add guard traces if sensitive"
        )

    else:
        # Generic signal trace
        width_mm = PREFERRED_SIGNAL_WIDTH_MM
        rationale = (
            f"Signal trace: preferred width {width_mm}mm "
            f"(minimum {MIN_SIGNAL_WIDTH_MM}mm)"
        )

    # Hard floor — production minimum
    width_mm = max(width_mm, MIN_SIGNAL_WIDTH_MM)

    return {
        "net": net_name,
        "type": net_type,
        "width_mm": round(width_mm, 3),
        "rationale": rationale,
    }


if __name__ == "__main__":
    # Quick self-test
    examples = [
        ("VCC", 1.0),
        ("GND", 2.0),
        ("VBAT", 0.5),
        ("CLK", 0.02),
        ("USB_DP", 0.05),
        ("AIN0", 0.01),
        ("/data_out", 0.01),
        ("SDA", 0.05),
    ]
    print(f"{'Net':<15} {'Type':<12} {'Width (mm)'}")
    print("-" * 40)
    for net, amps in examples:
        result = calculate_trace_width(net, amps)
        print(f"{result['net']:<15} {result['type']:<12} {result['width_mm']}")
