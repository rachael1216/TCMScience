"""Resolving a task's input bindings against what its dependencies produced.

The loop handed a dependent tool every upstream result under one payload key,
``upstream``, and left the arguments the component actually takes to the planner's
literals. The 2026-09-18 review showed what that meant in practice: a plan writing
``"symbol": "$fetch.gene"`` ran with the literal string ``$fetch.gene`` as the symbol, and
the BioScience bridge — whose entrypoints take named keyword arguments — dropped
``upstream`` because no argument is called that. Data crossed the edges of the graph only
as a blob that no step could read.

An ``InputBinding`` (``plan.py``) says which argument a step fills from which task's
result, and where in that result the value sits. This module resolves it: a JSON pointer
(RFC 6901) into the upstream value, a type and cardinality check, and — the part that
matters to the gates — the resolved value keeps the label of the result it was read from,
so the join is not lost at the edge. A pointer that finds nothing is a ``BindingError``,
a contract violation like any other malformed input, never a silently missing argument.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts import ContractViolation
from ..labels import Labeled, label_of, unwrap
from .plan import InputBinding, PlanTask

__all__ = ["BindingError", "resolve_pointer", "resolve_bindings", "resolve_input_bindings"]


class BindingError(ContractViolation):
    """An input binding could not be resolved against the upstream result."""


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def resolve_pointer(value: Any, pointer: str) -> Any:
    """Follow a JSON pointer into ``value``. ``""`` is the whole value.

    Keys are reported on failure, values never are: a failed binding is a plan defect and
    the message must be safe to feed back to a planner running remotely.
    """
    if not pointer:
        return value
    if not pointer.startswith("/"):
        raise BindingError(f"pointer {pointer!r} must be empty or start with '/'")
    current = value
    walked = ""
    for raw in pointer.split("/")[1:]:
        token = _unescape(raw)
        walked += "/" + raw
        if isinstance(current, Mapping):
            if token not in current:
                keys = sorted(str(k) for k in current)[:20]
                raise BindingError(
                    f"pointer {walked!r} names a key the result does not have; "
                    f"available keys: {keys}")
            current = current[token]
        elif isinstance(current, (list, tuple)):
            try:
                index = int(token)
            except ValueError:
                raise BindingError(
                    f"pointer {walked!r} indexes a list with {token!r}, which is not an "
                    "integer") from None
            if not -len(current) <= index < len(current):
                raise BindingError(
                    f"pointer {walked!r} is out of range for a list of {len(current)}")
            current = current[index]
        else:
            raise BindingError(
                f"pointer {walked!r} descends into a {type(current).__name__}, which has "
                "no members")
    return current


def _type_ok(value: Any, expected: str) -> bool:
    if not expected:
        return True
    from .evaluator import _is_type

    return _is_type(value, expected)


def resolve_bindings(task: PlanTask, upstream: Mapping[str, Any]) -> dict[str, Labeled]:
    """Every binding of ``task`` as ``{argument: Labeled(value)}``.

    ``upstream`` is ``graph.labeled_results()``: each dependency's value wrapped in the
    label the broker gave it. The resolved value carries that label, whatever its own text
    would classify as — a count derived from a PHI cohort is PHI.
    """
    return resolve_input_bindings(task.inputs, upstream, task_id=task.task_id)


def resolve_input_bindings(bindings, upstream: Mapping[str, Any], *,
                           task_id: str) -> dict[str, Labeled]:
    """Resolve declared inputs without requiring sources to share the target DAG.

    Graph membership and dependency validation belong to the invoking compiler.
    Dynamic stage bindings read a preceding visit, not the current task graph.
    """
    out: dict[str, Labeled] = {}
    for binding in bindings:
        item = upstream.get(binding.source)
        source_value = unwrap(item)
        if item is None or source_value is None:
            if binding.required:
                raise BindingError(
                    f"task {task_id!r} binds {binding.argument!r} to task "
                    f"{binding.source!r}, which produced no result")
            continue
        try:
            value = resolve_pointer(source_value, binding.pointer)
        except BindingError as exc:
            if binding.required:
                raise BindingError(
                    f"task {task_id!r}, argument {binding.argument!r} from "
                    f"{binding.source!r}: {exc}") from None
            continue
        if binding.cardinality == "many":
            if not isinstance(value, (list, tuple)):
                raise BindingError(
                    f"task {task_id!r}, argument {binding.argument!r}: expected a "
                    f"list from {binding.source!r}{binding.pointer}, got "
                    f"{type(value).__name__}")
            bad = [i for i, v in enumerate(value) if not _type_ok(v, binding.expected_type)]
            if bad:
                raise BindingError(
                    f"task {task_id!r}, argument {binding.argument!r}: "
                    f"{len(bad)} element(s) of the list are not {binding.expected_type}")
            value = list(value)
        elif not _type_ok(value, binding.expected_type):
            raise BindingError(
                f"task {task_id!r}, argument {binding.argument!r}: expected "
                f"{binding.expected_type}, got {type(value).__name__} from "
                f"{binding.source!r}{binding.pointer}")
        out[binding.argument] = Labeled(
            value=value, label=label_of(item),
            origin=f"binding:{binding.source}{binding.pointer}")
    return out
