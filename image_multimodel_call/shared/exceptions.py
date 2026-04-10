"""
shared/exceptions.py
Named exception per failure mode. No imports from this project.
"""
from __future__ import annotations


class AEAConcurError(Exception):
    """Base exception for all pipeline errors."""


class ValidationError(AEAConcurError):
    """Input validation failed before pipeline starts."""


class PDFLoadError(AEAConcurError):
    """Failed to open or read a PDF file."""


class PDFRenderError(AEAConcurError):
    """Failed to render a page to image."""
    def __init__(self, message: str, page_num: int = None):
        self.page_num = page_num
        super().__init__(message)


class LLMCallError(AEAConcurError):
    """LLM API call failed after all retries."""
    def __init__(self, message: str, attempt: int = None):
        self.attempt = attempt
        super().__init__(message)


class LLMResponseParseError(AEAConcurError):
    """LLM returned a response that could not be parsed as valid JSON."""
    def __init__(self, message: str, raw_response: str = ""):
        self.raw_response = raw_response
        super().__init__(message)


class ExtractionError(AEAConcurError):
    """Extraction failed for a specific file."""
    def __init__(self, message: str, source_file: str = None):
        self.source_file = source_file
        super().__init__(message)


class GroupingError(AEAConcurError):
    """Receipt grouping step failed."""


class MatchingError(AEAConcurError):
    """Matching step failed for a specific pair."""
    def __init__(self, message: str, txn_index: int = None):
        self.txn_index = txn_index
        super().__init__(message)


class TrackerError(AEAConcurError):
    """Failed to read or write the tracker."""


class StorageError(AEAConcurError):
    """Failed to write an output file."""


class ConfigurationError(AEAConcurError):
    """Missing or invalid configuration."""
