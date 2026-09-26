"""Structural ports; implementations must enforce the supplied run authority."""

from typing import Protocol as StructuralProtocol

from ..contracts import RunEnvelope
from ..labels import DataLabel
from .models import Protocol


class ProtocolResolver(StructuralProtocol):
    """Resolve immutable content and its label from one authorized snapshot.

    Implementations must enforce project, current policy and record integrity.
    The compiler still checks the binding fingerprint and declared protocol.
    This interface is a trusted integration seam, not a sandbox or authorization.
    """

    def resolve_protocol(self, node_id: str, envelope: RunEnvelope) -> tuple[Protocol, DataLabel]:
        ...
