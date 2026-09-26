"""Turn a PSH payload into BioScience keyword arguments. No PSH import: the isolated
entrypoint uses this in a child process that has BioScience and nothing else."""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["ArgumentError", "arguments_for"]

#: Keys PSH's runtime adds for its own bookkeeping; never a component's business.
_RUNTIME_PREFIX = "_psh_"


class ArgumentError(ValueError):
    """The payload does not fit the component. Named so the bridge can translate it."""


def arguments_for(payload: Any, *, source: Any = None, component_id: str = "") -> dict[str, Any]:
    """Keyword arguments for ``Runtime.invoke`` from a PSH payload.

    For a connector, ``operation`` selects the typed template and the remaining keys are
    its arguments. The template's example values fill optional parameters the caller
    left out (``species``, page sizes) and **never a required argument**: a call that
    forgot the gene symbol must fail, not look up the example's gene and return a
    plausible answer to a different question. For everything else the payload's keys are
    the entrypoint's keyword arguments. ``upstream`` — the loop's convention for dependent
    results — is dropped for both: a Python entrypoint would refuse an unexpected keyword
    and a template has no slot for it; a plan that wants an upstream value passed must
    name the argument it fills.
    """
    if not isinstance(payload, Mapping):
        raise ArgumentError(
            f"component {component_id!r} takes a mapping of arguments, got "
            f"{type(payload).__name__}")
    kwargs = {str(k): v for k, v in payload.items()
              if not str(k).startswith(_RUNTIME_PREFIX)}
    kwargs.pop("upstream", None)
    if source is None:
        return kwargs

    operation = kwargs.pop("operation", None)
    names = [op.name for op in source.operations]
    if not operation:
        raise ArgumentError(
            f"connector {component_id!r} needs an 'operation'; one of {names}")
    try:
        op = source.op(str(operation))
    except KeyError:
        raise ArgumentError(
            f"connector {component_id!r} has no operation {operation!r}; one of {names}"
        ) from None
    defaults = {k: v for k, v in op.example.items() if k not in op.args}
    try:
        return op.render(**{**defaults, **kwargs})
    except ValueError as exc:                     # a required argument is missing
        raise ArgumentError(f"connector {component_id!r}: {exc}") from None
