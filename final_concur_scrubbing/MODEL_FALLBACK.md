# Model Fallback Implementation

## Overview

This implementation adds **model fallback support** to the AMEX extraction pipeline. If the primary Azure OpenAI model fails after all retry attempts, the system automatically falls back to alternative models without requiring manual intervention.

## Problem Solved

Previously, if the primary model (e.g., `gpt-4o`) failed to extract a PDF after 5 retries, the entire extraction would fail. There was no way to attempt extraction with a different model. This was problematic for:

- **Quota exhaustion**: If the primary model hit rate limits, all PDFs would fail
- **Regional availability**: If the primary model was temporarily unavailable in a region
- **Model degradation**: If the primary model had quality issues on certain PDFs
- **Cost optimization**: Some PDFs might extract successfully with a cheaper fallback model

## Changes Made

### 1. **config/settings.py** — New Configuration Field

Added `fallback_models` list to allow users to specify which models to try if the primary fails:

```python
fallback_models: List[str] = Field(
    default=["gpt-4o", "gpt-4-turbo", "gpt-4-vision"],
    description="List of models to try in order, if primary model fails after all retries"
)
```

**Usage**: In `.env` or environment:
```bash
FALLBACK_MODELS=["gpt-4-turbo", "gpt-4-vision"]
```

### 2. **src/amex_extractor.py** — New Function: `_call_with_model_fallback()`

This function wraps the retry logic and handles model switching:

```python
def _call_with_model_fallback(
    client: OpenAI,
    b64: str,
    pdf_filename: str,
    timeout_s: float,
    primary_model: str,
    fallback_models: list[str],
) -> str:
    """
    Try extraction with primary model, then fallback to other models if retries fail.
    
    Flow:
      1. Try primary_model with full retry loop
      2. If all retries exhausted, try each fallback_model in sequence with retries
      3. Log each model attempt and fallback transition
      4. Return on first success
      5. Raise final exception if all models fail
    """
```

**Key Features**:
- **Retry loop per model**: Each model gets the full retry count (e.g., 5 attempts)
- **Graceful fallback**: Only switches models after a model is fully exhausted
- **Detailed logging**: Logs each model attempt, success, and transition
- **Fast failure**: Returns immediately on first successful extraction

### 3. **src/amex_extractor.py** — Updated `extract_statement()`

Changed the function to call `_call_with_model_fallback()` instead of directly calling the retry-wrapped `_call_api()`:

```python
# Before
retried_call = retry_decorator(_call_api)
raw_text = retried_call(client, b64, settings.azure_openai_model, ...)

# After
raw_text = _call_with_model_fallback(
    client,
    b64,
    pdf_path.name,
    timeout_s,
    primary_model=settings.azure_openai_model,
    fallback_models=fallback_models,
)
```

## Behavior

### Example Flow (with 5 retries per model)

If a PDF fails extraction:

1. **Attempt 1-5**: Try `gpt-4o` (primary) with retries
   - If retry 1 fails, wait and retry 2
   - Continue through retry 5
   
2. **Attempt 6-10**: If primary fully fails, try `gpt-4-turbo` with retries
   - If retry 1 fails, wait and retry 2
   - Continue through retry 5
   
3. **Attempt 11-15**: If fallback 1 fails, try `gpt-4-vision` with retries
   - If retry 1 fails, wait and retry 2
   - Continue through retry 5

4. **Final outcome**: Return on first success OR raise error if all models exhausted

### Logging

Each model attempt is logged with structured context:

```json
{
  "event": "model_attempt_start",
  "pdf": "statement.pdf",
  "model": "gpt-4-turbo",
  "is_primary": false,
  "attempt_idx": 6,
  "total_models": 3
}

{
  "event": "model_attempt_success",
  "pdf": "statement.pdf",
  "model": "gpt-4-turbo",
  "chars": 4523,
  "attempt_idx": 6
}
```

## Configuration Examples

### Example 1: Default Fallback Chain

```bash
# Uses built-in defaults:
# Primary: gpt-4o
# Fallback: [gpt-4-turbo, gpt-4-vision]
```

### Example 2: Custom Fallback Chain

```bash
AZURE_OPENAI_MODEL=gpt-4o
FALLBACK_MODELS=["gpt-4-turbo", "gpt-3.5-turbo"]
```

### Example 3: Single Model (No Fallback)

```bash
AZURE_OPENAI_MODEL=gpt-4o
FALLBACK_MODELS=[]
```

## Testing the Feature

### Unit Test Example

```python
def test_model_fallback_tries_next_model_on_failure():
    """Verify fallback switching after primary exhaustion."""
    
    # Mock first model to always fail
    # Mock second model to succeed on 2nd retry
    
    result = _call_with_model_fallback(
        client,
        b64="...",
        pdf_filename="test.pdf",
        timeout_s=180,
        primary_model="gpt-4o",
        fallback_models=["gpt-4-turbo"],
    )
    
    # Should succeed with second model after primary retries exhausted
    assert result is not None
```

## Performance Impact

- **No overhead on success**: If primary model succeeds, fallback is never used
- **Retry counts preserved**: Each model gets the full retry allowance (not accumulated)
- **Graceful degradation**: Slower response if fallback needed, but better than complete failure

## Error Handling

If all models fail after all retries:
- Raises the last `OpenAIError` encountered
- Logs detailed context including:
  - Number of models tried
  - Each model's error type
  - Total time spent
  - PDF name and size

## Files Modified

1. **config/settings.py** — Added `fallback_models` field
2. **src/amex_extractor.py** — Added `_call_with_model_fallback()`, updated `extract_statement()`

## Backward Compatibility

✅ **Fully backward compatible**:
- If `fallback_models` is not set, sensible defaults are used
- If empty list provided, only primary model is used
- Existing logs and metrics continue to work
