"""
extractors/base.py
Abstract base class for all extractors.
Contract: PDF path in → structured model out. No validation, no I/O.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path


class BaseExtractor(ABC):
    """
    All extractors follow this contract:
      - Accept a PDF path
      - Return a structured model object
      - Never write files
      - Never validate business rules
      - Isolate per-file errors — never crash the batch
    """

    def __init__(self, azure_client):
        self._llm = azure_client

    @abstractmethod
    def extract(self, pdf_path: Path):
        """Extract structured data from a PDF. Returns a model object."""
        ...
