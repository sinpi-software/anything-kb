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


def evaluate_gate(gate: dict[str, Any], output: str | None) -> tuple[bool, str]:
    """Evaluate a step's outgoing gate against its own output. (passed, reason).
    Any problem — no output, non-JSON, missing field, unknown op, or a type
    mismatch — fails the gate rather than raising."""
    field, op, value = gate.get("field"), gate.get("op"), gate.get("value")
    label = f"{field} {op} {value!r}"
    if output is None:
        return False, "gate: step produced no output"
    try:
        parsed = json.loads(output)
    except (ValueError, TypeError):
        return False, "gate: output is not JSON"
    if not isinstance(parsed, dict) or not isinstance(field, str) or field not in parsed:
        return False, f"gate: field {field!r} missing from output"
    fn = GATE_OPS.get(str(op))
    if fn is None:
        return False, f"gate: unknown op {op!r}"
    try:
        passed = bool(fn(parsed[field], value))
    except TypeError:
        return False, f"gate: {label} type mismatch"
    return passed, (f"gate passed: {label}" if passed else f"gate not met: {label} (got {parsed[field]!r})")
