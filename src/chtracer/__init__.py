"""Trace UK beneficial ownership chains via the Companies House API."""

__version__ = "0.1.0"

from .client import CompaniesHouseClient, RequestsTransport
from .models import Entity, Edge, Trace
from .traverse import OwnershipTracer

__all__ = [
    "CompaniesHouseClient",
    "RequestsTransport",
    "OwnershipTracer",
    "Entity",
    "Edge",
    "Trace",
]
