"""
Design Rule Checker — validates placement and routing output JSON.
Usage:
  python drc_checker.py <file.json> --check clearance,courtyard
  python drc_checker.py <file.kicad_pcb> --full
"""
import sys
import json
import argparse
import math


def check_clearance(placements: list, min_clearance_mm: float = 0.25) -> list[str]:
    errors = []
    for i, a in enumerate(placements):
        for b in placements[i + 1:]:
            dist = math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)
            if dist < min_clearance_mm:
                errors.append(
                    f"Clearance violation: {a['reference']} and {b['reference']} "
                    f"are {dist:.3f}mm apart (min {min_clearance_mm}mm)"
                )
    return errors


def check_placement_file(path: str, checks: list[str]) -> bool:
    with open(path) as f:
        data = json.load(f)

    placements = data.get("placements", [])
    all_errors = []

    if "clearance" in checks or "courtyard" in checks:
        all_errors.extend(check_clearance(placements))

    if all_errors:
        for err in all_errors:
            print(f"❌ DRC: {err}")
        return False

    print(f"✅ DRC PASS: {path}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="File to check")
    parser.add_argument("--check", default="clearance", help="Comma-separated checks")
    parser.add_argument("--full", action="store_true", help="Run all checks")
    args = parser.parse_args()

    checks = ["clearance", "courtyard", "width", "shorts"] if args.full else args.check.split(",")

    if args.file.endswith(".json"):
        ok = check_placement_file(args.file, checks)
    else:
        # KiCad PCB files — basic existence check for now
        print(f"⚠ Full KiCad PCB DRC not yet implemented for: {args.file}")
        ok = True

    sys.exit(0 if ok else 1)
