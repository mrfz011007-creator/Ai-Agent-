from __future__ import annotations

from typing import Any, Mapping


class SchemaValidationError(ValueError):
    """Raised when tool arguments do not satisfy the registered parameter schema."""


def validate_tool_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """Validate the bounded JSON-schema subset used by the tool registry."""
    if not isinstance(schema, Mapping):
        raise SchemaValidationError("TOOL_SCHEMA_INVALID")

    if not isinstance(arguments, Mapping):
        raise SchemaValidationError("TOOL_ARGUMENTS_MUST_BE_OBJECT")

    if schema.get("type") not in (None, "object"):
        raise SchemaValidationError("TOOL_SCHEMA_ROOT_MUST_BE_OBJECT")

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise SchemaValidationError("TOOL_SCHEMA_PROPERTIES_INVALID")

    required = schema.get("required", ())
    if not isinstance(required, (list, tuple)):
        raise SchemaValidationError("TOOL_SCHEMA_REQUIRED_INVALID")

    for name in required:
        if name not in arguments:
            raise SchemaValidationError(f"MISSING_REQUIRED_ARGUMENT: {name}")

    additional = schema.get("additionalProperties", True)
    if additional is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise SchemaValidationError(
                "UNKNOWN_ARGUMENTS: " + ", ".join(unknown)
            )

    for name, value in arguments.items():
        spec = properties.get(name)
        if spec is None:
            continue
        _validate_value(name, value, spec)


def _validate_value(name: str, value: Any, spec: Mapping[str, Any]) -> None:
    if not isinstance(spec, Mapping):
        raise SchemaValidationError(f"TOOL_SCHEMA_INVALID_FOR: {name}")

    if "enum" in spec and value not in spec["enum"]:
        raise SchemaValidationError(f"INVALID_ENUM_ARGUMENT: {name}")

    expected = spec.get("type")
    if expected is None:
        return

    checks = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "object": lambda v: isinstance(v, Mapping),
        "array": lambda v: isinstance(v, (list, tuple)),
        "null": lambda v: v is None,
    }
    check = checks.get(expected)
    if check is None:
        raise SchemaValidationError(f"UNSUPPORTED_SCHEMA_TYPE: {expected}")
    if not check(value):
        raise SchemaValidationError(
            f"INVALID_ARGUMENT_TYPE: {name} expected {expected}"
        )
