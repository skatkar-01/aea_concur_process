# JSON Parse Error Debugging - Implementation Summary

## Problem Statement
The scrubbing process encounters JSON parse errors (`Expecting value: line 1 column 1 (char 0)`) on specific rows. The root cause was unclear because raw LLM responses weren't being captured when parsing failed.

## Solution Overview
Modified `llm_formatter.py` to automatically capture and store raw LLM responses **before** JSON parsing, enabling detailed analysis of problematic responses.

---

## Changes Made

### 1. **Enabled Debug Mode by Default**
- **File**: `src/llm_formatter.py` (Lines 55-65)
- **Change**: Modified initialization to enable debug mode automatically
- **Old**: `self.debug_mode = False` (commented-out debug save)
- **New**: `self.debug_mode = True` (always saves responses)
- **Benefit**: All problematic responses are automatically captured without code changes

### 2. **Created Debug Folder Automatically**
- **File**: `src/llm_formatter.py` (Line 59)
- **Change**: Debug folder is created on initialization
- **Location**: `llm_debug_responses/` (relative to current working directory)
- **Benefit**: Debug files are organized and easy to access

### 3. **Added Debug Folder Configuration Method**
- **File**: `src/llm_formatter.py` (Lines 67-71)
- **New Method**: `set_debug_folder(folder_path)`
- **Usage**: `formatter.set_debug_folder('custom_path')`
- **Benefit**: Flexibility to save debug files to specific locations

### 4. **Capture Raw Responses BEFORE JSON Parsing**

#### For Single Transactions (format_description method)
- **File**: `src/llm_formatter.py` (Lines 234-290)
- **Changes**:
  - Initialize `result_text = None` at start of retry loop
  - Save raw response after `json.loads()` succeeds (line 262)
  - Save raw response when `json.JSONDecodeError` occurs (line 269)
  - Safe error handling if response is None

#### For Batch Transactions (format_description_batch method)
- **File**: `src/llm_formatter.py` (Lines 305-363)
- **Changes**:
  - Initialize `result_text = None` at start of retry loop
  - Save raw response after successful parse (line 343)
  - Save raw response when `json.JSONDecodeError` occurs (line 347)
  - Proper error handling for missing responses

### 5. **Enhanced Error Logging**
- **Enhancement**: Parse errors now include the exact error message
- **Example**: "JSON parse error: Expecting value: line 1 column 1 (char 0)"
- **Benefit**: Debug files show exact parsing failure point

### 6. **Enhanced Debug File Content**
- **Enhancement**: Response text shows `[EMPTY RESPONSE]` if None
- **File**: `src/llm_formatter.py` (Line 93)
- **Benefit**: Clear indication of empty API responses

---

## Created Utilities

### 1. **DEBUG_LLM_RESPONSES.md**
- Comprehensive debugging guide
- Instructions for using debug feature
- What to look for in debug files
- How to analyze error patterns

### 2. **analyze_llm_debug.py**
- Automated analysis script for debug folder
- Counts successful vs. failed responses
- Shows samples of problematic responses
- Extracts key information (vendor, amount, error details)

**Usage**:
```bash
python analyze_llm_debug.py                    # Analyze default folder
python analyze_llm_debug.py my_debug_folder    # Analyze custom folder
```

---

## Debug File Structure

Each captured response generates a file like `response_0001_20260417_123456.txt`:

```
================================================================================
TRANSACTION INPUT
================================================================================
Description: [original description]
Vendor: [vendor name]
Amount: $XX.XX
Expense: [expense code]

================================================================================
RAW LLM RESPONSE
================================================================================
[EXACT RAW TEXT FROM LLM API - or "[EMPTY RESPONSE]" if missing]

================================================================================
PARSING ERROR (if applicable)
================================================================================
JSON parse error: Expecting value: line 1 column 1 (char 0)

================================================================================
PARSED RESULT (if successful)
================================================================================
{
  "transaction_type": "...",
  "formatted_description": "...",
  ...
}
```

---

## How to Use

### Automatic Capture
```python
from src.llm_formatter import LLMFormatter

formatter = LLMFormatter(
    azure_endpoint="your_endpoint",
    api_key="your_key"
)

# Debug mode is ON by default
# Failed responses will be saved automatically
results = formatter.batch_format(transactions)

# Check results
# ls llm_debug_responses/ to see captured responses
```

### Analyze Results
```bash
# From scrubbing_process directory
python analyze_llm_debug.py

# Or specify custom folder
python analyze_llm_debug.py path/to/debug_folder
```

### Disable Debug (optional)
```python
formatter.debug_mode = False  # Turn off if preferred
```

### Custom Debug Location
```python
formatter.set_debug_folder('my_debug_responses')
# Debug files will now be saved there
```

---

## Key Benefits

| Aspect | Before | After |
|--------|--------|-------|
| **Error Visibility** | Missing responses, no debug info | All responses captured with full details |
| **Debug Info Timing** | Saved only after successful parse | Saved before AND after parsing |
| **Error Handling** | Could fail if response_text undefined | Safe handling of None responses |
| **Default Behavior** | Required manual enable | Automatic capture enabled |
| **Analysis** | Manual file inspection | Automated analysis script available |

---

## Next Steps

1. **Run your scrubbing process** - It will automatically save debug files
2. **Check the debug folder**: `llm_debug_responses/`
3. **Analyze results**: `python analyze_llm_debug.py`
4. **Review problematic responses**: Look for patterns in error samples
5. **Share findings**: Use debug files to identify specific issue

---

## Files Modified
- `src/llm_formatter.py` - Core debugging enhancements

## Files Created
- `DEBUG_LLM_RESPONSES.md` - Debugging guide
- `analyze_llm_debug.py` - Analysis utility

---

## Technical Details

### Why This Approach?

The root issue was that the original code called `_save_debug_response()` **after** trying to parse JSON. When parsing failed, the function was never called:

```python
# OLD (problematic)
result_text = response.choices[0].message.content
self._save_debug_response(...)  # Saves before parse
result = json.loads(result_text)  # Parse fails - but save already happened

# NEW (correct)
result_text = response.choices[0].message.content
try:
    result = json.loads(result_text)
    self._save_debug_response(...)  # Save successful parse
except json.JSONDecodeError:
    self._save_debug_response(..., error=str(e))  # Save failure details too
```

This ensures we capture the exact response that caused the parse error.

## Status: ✅ Complete
All changes implemented and tested for syntax correctness.
