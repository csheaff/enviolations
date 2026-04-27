from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from ..models import Facility, Violation

class DataSource(ABC):
    """Abstract base for all data source connectors."""

    name: str

    @abstractmethod
    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield normalized Facility records for the given state."""

    @abstractmethod
    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield normalized Violation records for the given state."""

    def close(self) -> None:
        """Release resources. Override if the connector holds connections."""
