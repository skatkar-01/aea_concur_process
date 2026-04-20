# Model Fallback Configuration Guide

## Environment Variables Setup

The LLM formatter now supports automatic model fallback when the primary model fails. Configure it using environment variables:

### Primary Model (Required)
```bash
AZURE_OPENAI_MODEL=gpt-5-mini
```

### Fallback Models (Optional)
```bash
AZURE_OPENAI_MODEL1=gpt-5.4-mini
AZURE_OPENAI_MODEL2=gpt-4.1-mini-219211
```

You can specify up to 9 fallback models (AZURE_OPENAI_MODEL through AZURE_OPENAI_MODEL9).

## How Model Fallback Works

1. **Primary Model**: Uses `AZURE_OPENAI_MODEL` (or default "gpt-5-mini")
2. **Fallback Sequence**: If the primary model fails, automatically tries Model1, Model2, etc.
3. **Failure Reasons Triggering Fallback**:
   - Empty API response
   - JSON parse errors
   - Missing required fields
   - API connection errors
4. **Recovery**: Resets to primary model after successful response

## Configuration Examples

### Minimal Setup (Single Model)
```bash
AZURE_OPENAI_ENDPOINT=https://your-instance.openai.azure.com/
AZURE_OPENAI_API_KEY=sk-xxx...
AZURE_OPENAI_MODEL=gpt-5-mini
```

### With Fallback (Recommended)
```bash
AZURE_OPENAI_ENDPOINT=https://your-instance.openai.azure.com/
AZURE_OPENAI_API_KEY=sk-xxx...
AZURE_OPENAI_MODEL=gpt-5-mini
AZURE_OPENAI_MODEL1=gpt-5.4-mini
AZURE_OPENAI_MODEL2=gpt-4.1-mini-219211
```

### `.env` File Format
```
AZURE_OPENAI_ENDPOINT=https://your-instance.openai.azure.com/
AZURE_OPENAI_API_KEY=sk-xxx...
AZURE_OPENAI_MODEL=gpt-5-mini
AZURE_OPENAI_MODEL1=gpt-5.4-mini
AZURE_OPENAI_MODEL2=gpt-4.1-mini-219211
```

## Error Logging

All API errors are logged to: **`llm_api_errors.log`**

Log includes:
- API call sequence number
- Current model being used
- Error type (empty response, JSON parse error, API error, etc.)
- Error message with details
- First 200 characters of response for context
- Exception type for debugging

### Log File Location
- Created in the working directory
- Automatically appended for each run
- Contains both console and file output

### Log Levels
- **DEBUG**: API call details (model, attempt number)
- **INFO**: Initialization info
- **WARNING**: Model fallback switching
- **ERROR**: API failures with full context

## Usage in Code

```python
from src.llm_formatter import LLMFormatter

# Initialize with fallback support
formatter = LLMFormatter(
    azure_endpoint="https://your-instance.openai.azure.com/",
    api_key="sk-xxx..."
)

# Model fallback is automatic
# If AZURE_OPENAI_MODEL fails, it will try AZURE_OPENAI_MODEL1, etc.
results = formatter.batch_format(transactions)

# Check API statistics
print(f"Total API calls: {formatter.api_call_count}")
print(f"API errors: {formatter.api_error_count}")
```

## Monitoring

### View Real-Time Logs
```bash
tail -f llm_api_errors.log
```

### Check for Empty Responses
```bash
grep "Empty response" llm_api_errors.log
```

### Check for JSON Parse Errors
```bash
grep "JSON parse error" llm_api_errors.log
```

### Model Usage Statistics
```bash
echo "Primary model calls:" && grep "Model: gpt-5-mini" llm_api_errors.log | wc -l
echo "Fallback model calls:" && grep "Model: gpt-5.4-mini" llm_api_errors.log | wc -l
```

## Debugging Empty Responses

If you see "Empty response from API" errors:

1. **Check Debug Files**: Look in `llm_debug_responses/` for related requests
2. **Review API Logs**: Check `llm_api_errors.log` for patterns
3. **API Status**: Verify Azure OpenAI service is operational
4. **Model Availability**: Ensure specified models are deployed
5. **Quotas**: Check if model quota has been exceeded

## Troubleshooting

### No Fallback Happening
- Ensure AZURE_OPENAI_MODEL1 is properly set in environment
- Check that environment variables are loaded before initializing LLMFormatter
- Verify models are actually deployed in Azure OpenAI

### All Models Failing
- Check `llm_api_errors.log` for underlying error type
- Review debug files in `llm_debug_responses/` for response details
- Verify Azure credentials and endpoint
- Check Azure OpenAI service status

### Performance
- Model fallback happens on failure, not in parallel
- Each failed attempt adds latency (typically 10-30 seconds per model)
- Use retry limits to prevent excessive retries

## Statistics and Monitoring

After processing:
```python
print(f"API Calls: {formatter.api_call_count}")
print(f"API Errors: {formatter.api_error_count}")
print(f"Error Rate: {formatter.api_error_count / formatter.api_call_count * 100:.1f}%")
```

Check logs for detailed error analysis:
```bash
grep "ERROR" llm_api_errors.log | cut -d' ' -f6- | sort | uniq -c
```
