# LLM JSON Parse Error Debugging Guide

## Problem Identified
The scrubbing process encounters "JSON parse error: Expecting value: line 1 column 1 (char 0)" for certain rows. This error occurs when:
- The LLM returns an empty response
- The LLM response is not valid JSON
- The response contains unexpected formatting

## Solution Implemented

### Changes Made to `llm_formatter.py`:

1. **Enabled Debug Mode by Default**
   - Debug responses are now automatically captured
   - Debug folder: `llm_debug_responses/` (created automatically in working directory)

2. **Raw Response Capture BEFORE JSON Parsing**
   - Previously: Debug save happened AFTER successful parsing (missed failures)
   - Now: Raw LLM response is saved BEFORE and AFTER parsing attempts
   - This captures exactly what the LLM returned, even if it's invalid JSON

3. **Added Debug Folder Configuration**
   - New method: `formatter.set_debug_folder(path)` to customize where debug files are saved
   - Example: `formatter.set_debug_folder('debug_responses')`

4. **Enhanced Error Handling**
   - Both single transaction and batch processing now safely save raw responses
   - Handles cases where response is empty or None

### Debug Files Structure

Each debug file contains:
```
================================================================================
TRANSACTION INPUT
================================================================================
Description: [original description]
Vendor: [vendor name]
Amount: [amount]
Expense: [expense code]

================================================================================
RAW LLM RESPONSE
================================================================================
[EXACT RAW RESPONSE FROM LLM - or "[EMPTY RESPONSE]" if missing]

================================================================================
PARSING ERROR (if applicable)
================================================================================
[Error details: JSON parse error: Expecting value...]

================================================================================
PARSED RESULT (if successful)
================================================================================
[Successfully parsed JSON result]
```

## How to Use

### Option 1: Run with automatic debug capture
```python
from llm_formatter import LLMFormatter

formatter = LLMFormatter(
    azure_endpoint="your_endpoint",
    api_key="your_key"
)

# Debug mode is enabled by default
# Raw responses for problematic rows will be saved to: llm_debug_responses/
results = formatter.batch_format(transactions)
```

### Option 2: Customize debug folder
```python
formatter = LLMFormatter(
    azure_endpoint="your_endpoint",
    api_key="your_key"
)

formatter.set_debug_folder('my_debug_folder')  # Custom location
results = formatter.batch_format(transactions)
```

### Option 3: Disable debug if you prefer
```python
formatter.debug_mode = False  # Turn off if needed
```

## What to Look For in Debug Files

When investigating JSON parse errors:

1. **Empty Response**
   - File shows `[EMPTY RESPONSE]`
   - Indicates API call issue or timeout

2. **Invalid JSON**
   - Shows garbled text or incomplete JSON fragments
   - Check if response is cut off mid-sentence

3. **Malformed JSON**
   - Shows JSON with syntax errors (missing quotes, braces, etc.)
   - Check the exact character position from the error

4. **Pattern Analysis**
   - Check if errors happen for specific transaction types
   - Look for common characteristics (amount, vendor, description format)

## Next Steps

1. **Run your scrubbing process** - it will automatically create debug files
2. **Check the `llm_debug_responses/` folder** for any response files with PARSING ERROR sections
3. **Analyze the RAW LLM RESPONSE** to identify the pattern
4. **Share problematic response files** for further investigation

## Performance Note

- Debug files are light (~1-2KB per transaction)
- Can be safely deleted after investigation: `rm -r llm_debug_responses/`
- Enabling debug has minimal performance impact
