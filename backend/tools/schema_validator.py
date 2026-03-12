import sys
import json
import argparse
import jsonschema


def validate(data_path: str, schema_path: str, strict: bool = False) -> bool:
    with open(data_path) as f:
        data = json.load(f)
    with open(schema_path) as f:
        schema = json.load(f)

    if strict and "$schema" not in schema:
        print(
            f"❌ STRICT MODE: schema file '{schema_path}' is missing the "
            f'"$schema" declaration. Add "$schema": "http://json-schema.org/draft-07/schema#" '
            f"to the top of the schema file.",
            file=sys.stderr,
        )
        return False

    try:
        jsonschema.validate(data, schema)
        print(f"✅ VALID: {data_path}")
        return True
    except jsonschema.ValidationError as e:
        print(f"❌ INVALID: {e.message}")
        print(f"   Path: {' -> '.join(str(p) for p in e.absolute_path)}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a JSON data file against a JSON Schema file."
    )
    parser.add_argument("data", help="Path to the JSON data file to validate")
    parser.add_argument("schema", help="Path to the JSON Schema file")
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help=(
            'Fail if the schema file does not declare "$schema". '
            "Use this to prevent regressions where schemas lack a draft declaration."
        ),
    )
    args = parser.parse_args()
    ok = validate(args.data, args.schema, strict=args.strict)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
