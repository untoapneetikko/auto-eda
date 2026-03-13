"""
Basic KiCad file validator — checks that files are parseable and well-formed.
Supports .kicad_sym, .kicad_mod, .kicad_sch, .kicad_pcb
"""
import sys
import os


def validate_sexpr(path: str) -> bool:
    """Validate KiCad S-expression file has balanced parentheses."""
    with open(path) as f:
        content = f.read()

    depth = 0
    for ch in content:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                print(f"❌ INVALID: {path} — unmatched closing parenthesis")
                return False

    if depth != 0:
        print(f"❌ INVALID: {path} — {depth} unclosed parentheses")
        return False

    print(f"✅ VALID: {path}")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python kicad_validator.py <file>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    ext = os.path.splitext(path)[1]

    if ext in (".kicad_sym", ".kicad_mod", ".kicad_sch", ".kicad_pcb"):
        ok = validate_sexpr(path)
    else:
        print(f"⚠ Unknown extension: {ext}, skipping validation")
        ok = True

    sys.exit(0 if ok else 1)
