import json
from collections.abc import Callable
from typing import Any

GATE_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: bool(a == b),
    "ne": lambda a, b: bool(a != b),
    "gt": lambda a, b: bool(a > b),
    "gte": lambda a, b: bool(a >= b),
    "lt": lambda a, b: bool(a < b),
    "lte": lambda a, b: bool(a <= b),
    "in": lambda a, b: bool(a in b),  # field value is a member of `value`
    "contains": lambda a, b: bool(b in a),  # field value (list/str) contains `value`
}


def evaluate_gate(gate: dict[str, Any], source_data: str | None) -> tuple[bool, str]:
    """(passed, reason). Any problem — no source output, non-JSON, missing field,
    unknown op, or a type mismatch — fails the gate rather than raising."""
    source, field, op, value = gate.get("source"), gate.get("field"), gate.get("op"), gate.get("value")
    label = f"{source}.{field} {op} {value!r}"
    if source_data is None:
        return False, f"gate: {source} produced no output"
    try:
        parsed = json.loads(source_data)
    except (ValueError, TypeError):
        return False, f"gate: {source} output is not JSON"
    if not isinstance(parsed, dict) or field not in parsed:
        return False, f"gate: {source}.{field} missing"
    fn = GATE_OPS.get(str(op))
    if fn is None:
        return False, f"gate: unknown op {op!r}"
    try:
        passed = bool(fn(parsed[field], value))
    except TypeError:
        return False, f"gate: {label} type mismatch"
    return passed, (f"gate passed: {label}" if passed else f"gate not met: {label} (got {parsed[field]!r})")
