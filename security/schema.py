from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


MAX_ARGUMENT_BYTES = 64 * 1024
MAX_STRING_CHARS = 32 * 1024
MAX_ARRAY_ITEMS = 256
MAX_OBJECT_PROPERTIES = 128
MAX_NESTING_DEPTH = 8
MAX_VALUE_NODES = 4096


class SchemaValidationError(ValueError):
    """Raised when tool arguments do not satisfy the bounded registry schema."""


def _is_finite_number(value: int | float) -> bool:
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _check_bounded_value(value: Any, *, path: str, depth: int, state: list[int]) -> None:
    state[0] += 1
    if state[0] > MAX_VALUE_NODES:
        raise SchemaValidationError("ARGUMENT_COMPLEXITY_LIMIT_EXCEEDED")
    if depth > MAX_NESTING_DEPTH:
        raise SchemaValidationError(f"ARGUMENT_NESTING_LIMIT_EXCEEDED: {path}")

    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not _is_finite_number(value):
            raise SchemaValidationError(f"NON_FINITE_NUMBER: {path}")
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            raise SchemaValidationError(f"STRING_TOO_LONG: {path}")
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_OBJECT_PROPERTIES:
            raise SchemaValidationError(f"TOO_MANY_PROPERTIES: {path}")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise SchemaValidationError(f"INVALID_OBJECT_KEY: {path}")
            _check_bounded_value(item, path=f"{path}.{key}", depth=depth + 1, state=state)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ARRAY_ITEMS:
            raise SchemaValidationError(f"TOO_MANY_ITEMS: {path}")
        for index, item in enumerate(value):
            _check_bounded_value(item, path=f"{path}[{index}]", depth=depth + 1, state=state)
        return
    raise SchemaValidationError(f"NON_JSON_ARGUMENT_VALUE: {path}")


def validate_tool_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """Validate a deliberately small, bounded JSON-schema subset for tool inputs."""
    if not isinstance(schema, Mapping):
        raise SchemaValidationError("TOOL_SCHEMA_INVALID")
    if not isinstance(arguments, Mapping):
        raise SchemaValidationError("TOOL_ARGUMENTS_MUST_BE_OBJECT")
    if schema.get("type") not in (None, "object"):
        raise SchemaValidationError("TOOL_SCHEMA_ROOT_MUST_BE_OBJECT")

    _check_bounded_value(arguments, path="$", depth=0, state=[0])
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise SchemaValidationError("TOOL_ARGUMENTS_NOT_JSON_SERIALIZABLE") from error
    try:
        encoded_size = len(encoded.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise SchemaValidationError("TOOL_ARGUMENTS_INVALID_UNICODE") from error
    if encoded_size > MAX_ARGUMENT_BYTES:
        raise SchemaValidationError("TOOL_ARGUMENT_BYTES_LIMIT_EXCEEDED")

    _validate_object(schema, arguments, path="$", depth=0)


def _validate_object(schema: Mapping[str, Any], value: Mapping[str, Any], *, path: str, depth: int) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise SchemaValidationError(f"TOOL_SCHEMA_PROPERTIES_INVALID: {path}")
    required = schema.get("required", ())
    if not isinstance(required, (list, tuple)) or any(not isinstance(x, str) for x in required):
        raise SchemaValidationError(f"TOOL_SCHEMA_REQUIRED_INVALID: {path}")
    if len(required) > MAX_OBJECT_PROPERTIES:
        raise SchemaValidationError(f"TOOL_SCHEMA_REQUIRED_TOO_LARGE: {path}")
    for name in required:
        if name not in value:
            raise SchemaValidationError(f"MISSING_REQUIRED_ARGUMENT: {name}")

    additional = schema.get("additionalProperties", False)
    if additional is not False:
        raise SchemaValidationError(f"UNSUPPORTED_ADDITIONAL_PROPERTIES_SCHEMA: {path}")
    unknown = sorted(set(value) - set(properties))
    if unknown:
        raise SchemaValidationError("UNKNOWN_ARGUMENTS: " + ", ".join(unknown))

    for name, item in value.items():
        spec = properties.get(name)
        if not isinstance(spec, Mapping):
            raise SchemaValidationError(f"TOOL_SCHEMA_INVALID_FOR: {name}")
        _validate_value(name, item, spec, path=f"{path}.{name}", depth=depth + 1)


def _validate_value(name: str, value: Any, spec: Mapping[str, Any], *, path: str, depth: int) -> None:
    if "enum" in spec:
        enum = spec["enum"]
        if not isinstance(enum, (list, tuple)) or value not in enum:
            raise SchemaValidationError(f"INVALID_ENUM_ARGUMENT: {name}")

    expected = spec.get("type")
    if expected is None:
        _check_bounded_value(value, path=path, depth=depth, state=[0])
        return

    checks = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and _is_finite_number(v),
        "boolean": lambda v: isinstance(v, bool),
        "object": lambda v: isinstance(v, Mapping),
        "array": lambda v: isinstance(v, (list, tuple)),
        "null": lambda v: v is None,
    }
    check = checks.get(expected)
    if check is None:
        raise SchemaValidationError(f"UNSUPPORTED_SCHEMA_TYPE: {expected}")
    if not check(value):
        raise SchemaValidationError(f"INVALID_ARGUMENT_TYPE: {name} expected {expected}")

    if expected == "string":
        minimum = spec.get("minLength", 0)
        maximum = spec.get("maxLength", MAX_STRING_CHARS)
        if (
            isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0
            or isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < minimum
        ):
            raise SchemaValidationError(f"INVALID_STRING_LENGTH_SCHEMA: {name}")
        if len(value) < minimum:
            raise SchemaValidationError(f"STRING_TOO_SHORT: {name}")
        if len(value) > min(maximum, MAX_STRING_CHARS):
            raise SchemaValidationError(f"STRING_TOO_LONG: {name}")
    elif expected in {"integer", "number"}:
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        for bound in (minimum, maximum):
            if bound is not None and (
                isinstance(bound, bool) or not isinstance(bound, (int, float))
                or not _is_finite_number(bound)
            ):
                raise SchemaValidationError(f"INVALID_NUMERIC_BOUND_SCHEMA: {name}")
        if minimum is not None and value < minimum:
            raise SchemaValidationError(f"NUMBER_TOO_SMALL: {name}")
        if maximum is not None and value > maximum:
            raise SchemaValidationError(f"NUMBER_TOO_LARGE: {name}")
    elif expected == "object":
        _validate_object(spec, value, path=path, depth=depth)
    elif expected == "array":
        minimum = spec.get("minItems", 0)
        maximum = spec.get("maxItems", MAX_ARRAY_ITEMS)
        if (
            isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0
            or isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < minimum
        ):
            raise SchemaValidationError(f"INVALID_ARRAY_LENGTH_SCHEMA: {name}")
        if len(value) < minimum:
            raise SchemaValidationError(f"TOO_FEW_ITEMS: {name}")
        if len(value) > min(maximum, MAX_ARRAY_ITEMS):
            raise SchemaValidationError(f"TOO_MANY_ITEMS: {name}")
        item_schema = spec.get("items")
        if item_schema is not None:
            if not isinstance(item_schema, Mapping):
                raise SchemaValidationError(f"INVALID_ARRAY_ITEM_SCHEMA: {name}")
            for index, item in enumerate(value):
                _validate_value(name, item, item_schema, path=f"{path}[{index}]", depth=depth + 1)
        if spec.get("uniqueItems") is True:
            try:
                encoded_items = [json.dumps(x, sort_keys=True, allow_nan=False) for x in value]
            except (TypeError, ValueError) as error:
                raise SchemaValidationError(f"INVALID_ARRAY_ITEM: {name}") from error
            if len(set(encoded_items)) != len(value):
                raise SchemaValidationError(f"DUPLICATE_ARRAY_ITEM: {name}")
