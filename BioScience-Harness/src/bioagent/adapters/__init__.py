"""Capability adapters — invoke upstream capabilities without copying their code."""

from .base import Adapter, AdapterError, CallResult, LicenseError
from .datalake import DataLakeAdapter
from .federated import FederatedProcessAdapter, NativeConnectorAdapter

__all__ = ["Adapter", "AdapterError", "CallResult", "LicenseError",
           "DataLakeAdapter", "FederatedProcessAdapter", "NativeConnectorAdapter"]
