# LLM Formatter - Error Resolution & Per-Row Fallback Documentation

## Errors Fixed

### 1. **Unicode Encoding Error in Windows Console**

**Problem:**
```
UnicodeEncodeError: 'charmap' codec can't encode character '\u2713' in position 49
```

Windows PowerShell/Console uses `cp1252` encoding by default, which doesn't support Unicode symbols like ✓ and ❌.

**Solution:**
- Configured logging with UTF-8 encoding
- Console output now uses ASCII fallback symbols: `[OK]`, `[FAIL]`, `[WARN]`
- File logs (`llm_api_errors.log`) preserve full Unicode for debugging

### 2. **Empty Batch API Response**

**Problem:**
```
Empty response from batch API - Model: gpt-5-mini, Items: 15
```

When batch processing fails (returns empty response), the system now properly falls back to:
1. Attempt with fallback models (gpt-5.4-mini, etc.)
2. If all models fail, process items **individually** (per-row)

**Solution:**
```python
# Enhanced fallback logic in format_description_batch()
if not result_text or result_text.strip() == "":
    error_msg = "Empty response from batch API"
    logger.error(f"[FAIL] {error_msg} - Model: {self.deployment_name}, Items: {len(items)}")
    
    if self._switch_model():
        continue  # Try with fallback model
    else:
        # Fallback: Process items individually
        logger.warning(f"[WARN] Batch failed with all models. Processing {len(items)} items individually.")
        return [self.format_description(item["txn"], item.get("similar_txns") or []) for item in items]
```

## Error Handling Strategy

### Batch Processing Flow

```
┌─────────────────────────────────┐
│  format_description_batch()     │ Try batch processing (15 items)
│  Attempt with primary model     │
└──────────────┬──────────────────┘
               │
         ┌─────▼──────────┐
         │ Success?       │
         └─────┬──────────┘
               │ No
         ┌─────▼─────────────────────┐
         │ Switch to fallback model  │
         │ (gpt-5.4-mini, etc.)      │
         └─────┬─────────────────────┘
               │
         ┌─────▼──────────┐
         │ Success?       │
         └─────┬──────────┘
               │ No (all models exhausted)
         ┌─────▼────────────────────────────┐
         │ Fall back to per-row processing  │
         │ Call format_description() for    │
         │ each item individually           │
         └────────────────────────────────────┘
```

### Model Fallback Chain

```
Primary:  gpt-5-mini
  ↓ (if fails)
Fallback: gpt-5.4-mini
  ↓ (if fails)
Fallback: gpt-4.1-mini-219211
  ↓ (if all fail)
Per-row processing with individual retries
```

## Configuration

### Model Fallback (Environment Variables)

```bash
# Primary model
AZURE_OPENAI_MODEL=gpt-5-mini

# Fallback models (cascading)
AZURE_OPENAI_MODEL1=gpt-5.4-mini
AZURE_OPENAI_MODEL2=gpt-4.1-mini-219211
AZURE_OPENAI_MODEL3=... (additional fallbacks)
```

### Logging

**Console Output:**
```
[OK] Azure OpenAI initialized (primary: gpt-5-mini)
     Model fallback: gpt-5-mini -> gpt-5.4-mini -> gpt-4.1-mini-219211
[FAIL] Empty response from batch API
[WARN] Batch failed with all models. Processing 15 items individually.
```

**File Log (`llm_api_errors.log`):**
```
2026-04-17 19:21:24,467 - llm_formatter - INFO - ✓ Azure OpenAI initialized (primary: gpt-5-mini)
2026-04-17 19:23:03,212 - llm_formatter - ERROR - ❌ Empty response from batch API - Model: gpt-5-mini, Items: 15
2026-04-17 19:23:03,219 - llm_formatter - WARNING - Switching to fallback model: gpt-5.4-mini
```

## Processing Modes

### Mode 1: Batch Processing (Default)
- **When:** Multiple items (>1) in batch
- **Speed:** ~5.14s per item for 15 items = 77.6s total
- **Benefit:** Efficient API usage, lower cost
- **Risk:** Single batch failure affects all items

### Mode 2: Per-Row Fallback
- **When:** Batch fails or initial condition is 1 item
- **Speed:** Slower but more reliable
- **Benefit:** Individual retry logic, isolated failures
- **Implementation:** Automatic fallback when batch fails

## Confidence Levels & Manual Review

```python
Auto-Approved (≥95% confidence):  4 items (26.7%)
Needs Review (80-95%):             10 items (66.7%)
Flagged (<80%):                    1 item (6.7%)
```

## Status Codes in Logs

| Symbol | Meaning | Console | File |
|--------|---------|---------|------|
| ✓ | Success | [OK] | ✓ |
| ✗ | Failure | [FAIL] | ❌ |
| ⚠ | Warning | [WARN] | ⚠️ |

## Debugging

### View LLM Debug Responses
```
cache/llm_results/
├── txn_hash_1.json      (successful response)
├── txn_hash_2_ERROR.json (failed response)
└── txn_hash_3_DEBUG.json (debug info)
```

### Enable Debug Mode
```python
formatter.debug_mode = True  # Already enabled by default
formatter.set_debug_folder('cache/llm_results')
```

### Check API Call Metrics
```python
print(f"API calls: {formatter.api_call_count}")
print(f"API errors: {formatter.api_error_count}")
```

## Performance Expectations

### Batch Processing
- **15 items:** 77.6s (5.17s per item)
- **API overhead:** ~2s per call
- **Processing:** ~3-4s per item average

### Per-Row Fallback
- **Expected:** 6-8s per item (additional overhead from per-item retries)
- **Reliability:** Much higher (individual error isolation)

## Next Steps

1. **Monitor Performance:** Track batch vs. per-row ratios
2. **Optimize Batch Size:** Adjust based on timeout patterns
3. **Model Selection:** Monitor which fallback models are used most
4. **Cost Analysis:** Compare batch vs. per-row API costs

## Key Changes in `llm_formatter.py`

1. **Lines 13-18:** UTF-8 encoding configuration
2. **Lines 82-87:** Unicode console fallback
3. **Lines 456-460:** Empty batch response fallback to per-row
4. **Lines 504-512:** JSON parse error fallback to per-row
5. **Lines 519-533:** Generic exception fallback to per-row
