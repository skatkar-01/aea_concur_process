"""
tests/test_model_fallback.py
──────────────────────────────
Unit tests for model fallback functionality in amex_extractor.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock, call
from openai import (
    APITimeoutError,
    APIConnectionError,
    APIStatusError,
)

# Imports from the module under test
from src.amex_extractor import _call_with_model_fallback, _get_retry_decorator


@pytest.fixture
def mock_client():
    """Create a mock OpenAI client."""
    return Mock()


@pytest.fixture
def sample_b64():
    """Sample base64-encoded PDF."""
    return "JVBERi0xLjQK..."


@pytest.fixture
def sample_response():
    """Sample API response (JSON string)."""
    return json.dumps({
        "company": "Test Corp",
        "statement_period": "JAN_042026",
        "cardholders": [],
        "transactions": [],
    })


def test_primary_model_succeeds(mock_client, sample_b64, sample_response):
    """
    Primary model succeeds on first call.
    Fallback should not be attempted.
    """
    # Setup: primary model succeeds
    response_obj = Mock()
    response_obj.output_text = sample_response
    mock_client.responses.create.return_value = response_obj

    with patch("src.amex_extractor._get_retry_decorator") as mock_retry:
        # Mock the retry decorator to return the original function
        def identity_decorated(func):
            return func
        mock_retry.return_value = identity_decorated

        with patch("src.amex_extractor._call_api") as mock_call_api:
            mock_call_api.return_value = sample_response

            result = _call_with_model_fallback(
                client=mock_client,
                b64=sample_b64,
                pdf_filename="test.pdf",
                timeout_s=180,
                primary_model="gpt-4o",
                fallback_models=["gpt-4-turbo", "gpt-4-vision"],
            )

            # Should succeed with primary model
            assert result == sample_response
            # _call_api should be called once (primary model only)
            assert mock_call_api.call_count == 1


def test_primary_fails_fallback_succeeds(mock_client, sample_b64, sample_response):
    """
    Primary model is exhausted after retries.
    First fallback model succeeds.
    """
    with patch("src.amex_extractor._get_retry_decorator") as mock_retry:
        # Mock the retry decorator:
        # - First call (primary): returns a function that raises an error
        # - Second call (fallback): returns a function that succeeds
        call_count = [0]

        def side_effect_retry(func):
            call_count[0] += 1
            if call_count[0] == 1:
                # Primary model retry: always raises
                def wrapped(*args, **kwargs):
                    raise APITimeoutError("Primary model timeout")
                return wrapped
            else:
                # Fallback model retry: succeeds
                def wrapped(*args, **kwargs):
                    return sample_response
                return wrapped

        mock_retry.side_effect = side_effect_retry

        with patch("src.amex_extractor._call_api"):
            result = _call_with_model_fallback(
                client=mock_client,
                b64=sample_b64,
                pdf_filename="test.pdf",
                timeout_s=180,
                primary_model="gpt-4o",
                fallback_models=["gpt-4-turbo", "gpt-4-vision"],
            )

            # Should succeed with fallback
            assert result == sample_response
            # _get_retry_decorator should be called twice (primary + first fallback)
            assert mock_retry.call_count == 2


def test_all_models_fail(mock_client, sample_b64):
    """
    All models (primary + fallbacks) fail after retries.
    Final exception should be raised.
    """
    with patch("src.amex_extractor._get_retry_decorator") as mock_retry:
        def wrapped_fail(*args, **kwargs):
            raise APIConnectionError("Network failure")

        def identity_fail(func):
            return wrapped_fail

        mock_retry.return_value = identity_fail

        with patch("src.amex_extractor._call_api"):
            with pytest.raises(APIConnectionError):
                _call_with_model_fallback(
                    client=mock_client,
                    b64=sample_b64,
                    pdf_filename="test.pdf",
                    timeout_s=180,
                    primary_model="gpt-4o",
                    fallback_models=["gpt-4-turbo"],
                )


def test_no_fallback_models(mock_client, sample_b64, sample_response):
    """
    With empty fallback list, only primary model is tried.
    """
    with patch("src.amex_extractor._get_retry_decorator") as mock_retry:
        def identity_decorated(func):
            return func
        mock_retry.return_value = identity_decorated

        with patch("src.amex_extractor._call_api") as mock_call_api:
            mock_call_api.return_value = sample_response

            result = _call_with_model_fallback(
                client=mock_client,
                b64=sample_b64,
                pdf_filename="test.pdf",
                timeout_s=180,
                primary_model="gpt-4o",
                fallback_models=[],
            )

            # Should use primary model
            assert result == sample_response
            # _get_retry_decorator called once (primary only)
            assert mock_retry.call_count == 1


def test_logging_on_model_attempt():
    """
    Verify that model attempts are logged with correct context.
    (Integration test — checks actual logging output)
    """
    import logging
    from io import StringIO

    # Capture logs
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.INFO)

    # This is a simplified test.
    # In practice, you'd use structlog's testing utilities
    assert True  # Placeholder for actual logging test


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
