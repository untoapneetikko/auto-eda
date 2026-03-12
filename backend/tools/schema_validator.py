import sys
import json
import jsonschema


def validate(data_path: str, schema_path: str) -> bool:
    with open(data_path) as f:
        data = json.load(f)
    with open(schema_path) as f:
        schema = json.load(f)
    try:
        jsonschema.validate(data, schema)
        print(f"✅ VALID: {data_path}")
        return True
    except jsonschema.ValidationError as e:
        print(f"❌ INVALID: {e.message}")
        print(f"   Path: {' -> '.join(str(p) for p in e.absolute_path)}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python schema_validator.py <data.json> <schema.json>", file=sys.stderr)
        sys.exit(1)
    ok = validate(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
