"""
llm_formatter.py - LLM-based Description Formatter using Azure OpenAI

ERROR HANDLING STRATEGY:
======================
1. Unicode Logging: Windows console uses cp1252 encoding which can't display Unicode
   symbols. Logging configured with UTF-8 + fallback to ASCII symbols for console.
   
2. Batch Processing with Per-Row Fallback:
   - Attempts batch processing first (faster for multiple items)
   - If batch API fails (empty response or JSON parse error) → switches fallback model
   - If all models fail → automatically processes items individually (slower, more reliable)
   - Per-row processing handles each transaction separately
   
3. Model Fallback Chain:
   - Primary: gpt-5-mini
   - Fallback models: gpt-5.4-mini, gpt-4.1-mini-219211, etc.
   - Each failure triggers model switch before retrying
   - Resets to primary model on success
   
4. Logging Output:
   - Console: Uses [OK], [FAIL], [WARN] for Windows compatibility
   - File log (llm_api_errors.log): Full details with Unicode support
"""

import json
import os
import logging
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
from openai import OpenAI

# Configure logging with UTF-8 encoding for console to support Unicode symbols
import sys
import io

# Force UTF-8 output on Windows
if sys.platform == 'win32':
    # Use UTF-8 encoding for stdout/stderr
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('llm_api_errors.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class LLMFormatter:
    """
    Use Azure OpenAI (GPT-5-mini) to format descriptions with chain-of-thought reasoning
    """
    
    def __init__(
        self,
        azure_endpoint: str = None,
        api_key: str = None,
        api_version: str = "2024-02-15-preview",
        deployment_name: str = None
    ):
        """
        Initialize Azure OpenAI client with model fallback support
        
        Args:
            azure_endpoint: Azure OpenAI endpoint URL
            api_key: Azure OpenAI API key
            api_version: API version
            deployment_name: Primary deployment name (model) - optional, will use env vars
        """
        self.azure_endpoint = azure_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
        self.api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        
        if not self.azure_endpoint or not self.api_key:
            raise ValueError(
                "Azure OpenAI credentials required. Set AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_API_KEY environment variables."
            )
        
        # Initialize model fallback list
        self.model_fallback = []
        self.primary_model = deployment_name or os.getenv("AZURE_OPENAI_MODEL", "gpt-5-mini")
        self.model_fallback.append(self.primary_model)
        
        # Add fallback models from environment
        for i in range(1, 10):  # Support up to 9 fallback models
            model_env = f"AZURE_OPENAI_MODEL{i}"
            fallback_model = os.getenv(model_env)
            if fallback_model:
                self.model_fallback.append(fallback_model)
        
        self.current_model_index = 0
        self.deployment_name = self.model_fallback[0]
        
        # Initialize client
        self.client = OpenAI(
            base_url=self.azure_endpoint,
            api_key=self.api_key,
            # api_version=api_version
        )
        
        logger.info(f"[OK] Azure OpenAI initialized (primary: {self.deployment_name})")
        logger.info(f"     Model fallback list: {' -> '.join(self.model_fallback)}")
        # Print to console with fallback symbols for Windows compatibility
        try:
            print(f"✓ Azure OpenAI initialized (primary: {self.deployment_name})")
            print(f"  Model fallback: {' -> '.join(self.model_fallback)}")
        except UnicodeEncodeError:
            print(f"[OK] Azure OpenAI initialized (primary: {self.deployment_name})")
            print(f"     Model fallback: {' -> '.join(self.model_fallback)}")
        
        # Load system prompt
        self.system_prompt = self._build_system_prompt()
        
        # Debug attributes
        self.debug_mode = True  # Enable by default to capture issues
        self.response_count = 0
        self.debug_folder = Path('llm_debug_responses')
        self.debug_folder.mkdir(exist_ok=True)
        
        # API call tracking
        self.api_call_count = 0
        self.api_error_count = 0
    
    def set_debug_folder(self, folder_path: str):
        """Set custom debug folder for storing LLM responses"""
        self.debug_folder = Path(folder_path)
        self.debug_folder.mkdir(exist_ok=True)
        logger.info(f"Debug folder set to: {self.debug_folder.absolute()}")
        try:
            print(f"✓ Debug responses will be saved to: {self.debug_folder.absolute()}")
        except UnicodeEncodeError:
            print(f"[OK] Debug responses will be saved to: {self.debug_folder.absolute()}")
    
    def _switch_model(self) -> bool:
        """Switch to next model in fallback list. Returns True if successful, False if no more models."""
        if self.current_model_index + 1 < len(self.model_fallback):
            self.current_model_index += 1
            self.deployment_name = self.model_fallback[self.current_model_index]
            logger.warning(f"Switching to fallback model: {self.deployment_name}")
            print(f"⚠️  Switching to fallback model: {self.deployment_name}")
            return True
        return False
    
    def _reset_model(self):
        """Reset to primary model"""
        self.current_model_index = 0
        self.deployment_name = self.model_fallback[0]
    
    def _save_debug_response(self, txn: Dict, response_text: str, parsed_result: Dict = None, error: str = None):
        """Save LLM response for debugging"""
        if not self.debug_mode:
            return
        
        self.response_count += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"response_{self.response_count:04d}_{timestamp}.txt"
        
        debug_file = self.debug_folder / filename
        
        with open(debug_file, 'w') as f:
            f.write("="*80 + "\n")
            f.write("TRANSACTION INPUT\n")
            f.write("="*80 + "\n")
            f.write(f"Description: {txn.get('description', '')}\n")
            f.write(f"Vendor: {txn.get('vendor', '')}\n")
            f.write(f"Amount: ${txn.get('amount', 0):.2f}\n")
            f.write(f"Expense: {txn.get('expense_code', '')}\n")
            f.write("\n")
            
            f.write("="*80 + "\n")
            f.write("RAW LLM RESPONSE\n")
            f.write("="*80 + "\n")
            f.write(response_text if response_text else "[EMPTY RESPONSE]")
            f.write("\n\n")
            
            if error:
                f.write("="*80 + "\n")
                f.write("PARSING ERROR\n")
                f.write("="*80 + "\n")
                f.write(error)
                f.write("\n\n")
            
            if parsed_result:
                f.write("="*80 + "\n")
                f.write("PARSED RESULT\n")
                f.write("="*80 + "\n")
                f.write(json.dumps(parsed_result, indent=2))
                f.write("\n")
    

    def _build_system_prompt(self) -> str:
        """Build comprehensive system prompt for expense scrubbing"""
        return """You are an expert AmEx expense data scrubber for AEA Investors LP.
Apply ALL rules from the AEA Scrubbing Rules Documentation (rules_docs folder).

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 1: DESCRIPTION FORMATTING RULES (13 core rules)
═══════════════════════════════════════════════════════════════════════════════

GLOBAL ABBREVIATIONS:
  - "Business " → "Bus." (applies to all descriptions)
  - "Meeting" → "Mtg" (word boundary)
  - "Meetings" → "Mtgs" (word boundary)
  - "Ticketing Fee" → "Tkt Fee" (word boundary)
  - "Ticket/" → "Tkt/" (prefix, no word boundary)

LODGING CLEANUP:
  - "Bus. Lodging" → "Lodging"
  - "Bus.Lodging" → "Lodging"

CAR SERVICE CLEANUP:
  - Remove "Transportation" entirely
  - "Home to Office"/"office to home" → use dash format
  - "Home to Office" → "Home-Office"
  - "Office to Home" → "Office-Home"
  - Ensure compact format: no spaces around dashes or slashes

INFLIGHT WIFI CASING (exact casing required):
  - "Inflight WiFi" → "Inflight Wifi"
  - "inflight wifi" → "Inflight Wifi"
  - "Inflight WIFI" → "Inflight Wifi"
  - "In-flight Wifi" → "Inflight Wifi"

PERSONAL CLEANUP:
  - "Personal expense" → "Personal"
  - "Personal Expense" → "Personal"

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 2: TRANSACTION TYPE FORMATTING
═══════════════════════════════════════════════════════════════════════════════

FLIGHTS:
  Format: RT:<from>-<to>/<purpose>/<deal or company> (round trip)
  Format: <from>-<to>/<purpose>/<deal or company> (one-way)
  Example: "RT:JFK-STO/Fundraising/Growth Fund"
  Example: "EWR-SLC/Strategy Mtg/Amateras"

FLIGHT FEES/CHANGES:
  - Ticketing Fee: "Tkt Fee/RT:<route>/<purpose>/<deal>"
  - Seat Upgrade: "Seat Upgrade/<purpose>/<deal>"
  - Checked Bag: "Checked Bag/<purpose>/<deal>"
  - Exchange: "Exch Tkt/<route>/<purpose>/<deal>"

REFUNDS:
  - MUST start with "Refund/"
  - Format: "Refund/<original format>"
  - Example: "Refund/RT:JFK-SLC/Strategy Mtg"

CAR SERVICE:
  Format: <from>-<to>/<purpose>/<deal or company>
  Example: "JFK-Hotel/BOD Mtg/Chemical Guys"
  
  SPECIAL CAR FORMATS (do NOT modify):
    - Work Late: "Work Late/Office-Home" (evening ≥7:30pm)
    - Early Arrival: "Early Arrival/Home-Office" (morning ≤7:00am)
    - Weekend Saturday/Sunday: "Weekend/Home-Office" or "Weekend/Office-Home"

BUSINESS MEALS:
  Format: Bus.Lunch or Bus.Dinner/<attendee initials>/<deal or company>
  Example: "Bus.Lunch/B.Gallagher & K.Carbonez/Goldman Sachs"
  Example: "Bus.Dinner/BOD Dinner/17 ppl/Redwood"
  Rules:
    - Use "Bus.Lunch" or "Bus.Dinner" (not "Business")
    - ATTENDEE NAMES MUST BE INITIALS ONLY (e.g., "B.Gallagher" not "Brendan Gallagher")
    - Multiple attendees: use "&" or commas between initials (e.g., "B.Gallagher & K.Carbonez")
    - Optionally include guest count
    - End with deal/company name

TRAVEL MEALS (meals during business trips):
  Format: Travel Meal/<purpose>/<deal or company>
  Example: "Travel Meal/BOD Mtg/Numotion"

OFFICE/IN-HOUSE MEALS:
  - "Working Lunch" or "Working Dinner"
  - Overage: "Working Lunch/Overage/Personal"

LODGING:
  Format: Lodging/<purpose>/<deal or company>
  Example: "Lodging/BOD Mtg/AmeriVet"
  Rules:
    - Never use "Bus. Lodging" (remove Bus. prefix)
    - Always lowercase: "Lodging" not "LODGING"

INFO SERVICES:
  - Inflight Wifi: "Inflight Wifi" (exact casing, no other text)
  - Research: "Research Subscription/<purpose>/<deal>"

OTHER TRAVEL:
  - Parking: "Bus.Parking/<purpose>/<deal>"
  - Fuel: "Bus.Fuel/<route>/<event>"
  - Train: "Train/<route>/<purpose>/<company>"
  - Bus: "Bus Ticket/<route>/<purpose>/<company>"

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 3: EXPENSE CODE RULES
═══════════════════════════════════════════════════════════════════════════════

SMART DETECTION (remap expense codes based on description patterns):
  - If description contains "inflight wifi" → use "Info Services"
  - If description contains "tkt fee", "ticketing fee", "seat upgrade", "checked bag" → use "Airline"
  - If description contains "booking fee" AND "lodging|hotel" → use "Lodging"
  - If description contains "train/", "bus.parking", "bus.fuel", "bus.rental", "travel insurance" → use "Other Travel"

EXPENSE CODE REMAPPING (convert invalid codes):
  - "Miscellaneous" → "Other" (disallowed)
  - "Cell Phone" → "Phones" (disallowed)
  - "Telephones" → "Phones" (disallowed)
  - "Furn & Equip" → "Equipment"
  - "Furn & Equipment" → "Equipment"
  - "Seminars" → "Conferences"
  - "Seminars and Conferences" → "Conferences"
  - "Info Service" → "Info Services"

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 4: PAY TYPE CLEANUP
═══════════════════════════════════════════════════════════════════════════════

  - "American Express Corporate Card CBCP" → "American Express"

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 5: CHARACTER LENGTH LIMIT
═══════════════════════════════════════════════════════════════════════════════

  Length Formula: LEN(description + vendor) + 12 ≤ 70 characters
  
  If exceeded, flag for human review. DO NOT truncate.
  Example: "RT:JFK-STO/Fundraising/Growth Fund" (34) + "United Airlines" (15) + 12 = 61 ✓

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 6: MEAL AMOUNT LIMITS
═══════════════════════════════════════════════════════════════════════════════

  - Working Lunch: max $25.00
  - Working Dinner: max $35.00
  
  If exceeded, flag: "Working Lunch $X.XX exceeds $25.00 limit"

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 7: CAR SERVICE POLICY
═══════════════════════════════════════════════════════════════════════════════

  EARLY ARRIVAL: ≤7:00am → "Early Arrival/Home-Office"
    - Flag: "Early arrival car - verify receipt timestamp ≤ 7:00am"
  
  WORK LATE: ≥7:30pm → "Work Late/Office-Home"
    - Flag: "Work late car - verify receipt timestamp ≥ 7:30pm"
  
  WEEKEND (Saturday/Sunday) → "Weekend/Home-Office" or "Weekend/Office-Home"
    - Flag: "Weekend car service should use Weekend/Home-Office or Weekend/Office-Home format"

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 8: G/L OVERRIDE FLAGS (Review at posting time)
═══════════════════════════════════════════════════════════════════════════════

  Projects 1035, 1003, 1012 (Prepaid):
    → Flag: "Prepaid project - review G/L 14000 at posting"
  
  Expense "Other" + Projects 1001, 3500, 7500, 1105 (Corporate event):
    → Flag: "Corporate event - review G/L 58120 at posting"
  
  Project 1013 (Intern event):
    → Flag: "Intern event - review G/L 58230 at posting"
  
  Holiday Party car service:
    → Flag: "Holiday Party car - review G/L 58140"

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 9: PROJECT & DEPARTMENT ALIGNMENT
═══════════════════════════════════════════════════════════════════════════════

  REQUIRED DEPT BY PROJECT:
    - 1055 → "AMA"
    - 1010 → "UK" OR "GMBH"
    - 4246 → "CONS"
    - 6006 → "VAIP"
    - 6001 → "VAIP"
    - 3500 → "SBF"
    - 7500 → "DEBT"
    - 1105 → "GROWTH"
  
  PROJECT METADATA RULES:
    - 1008 (Personal) → REQUIRES Employee ID (flag if missing)
    - 4200-B (Traeger Board) → ONLY for board meetings (flag if description lacks BOD/Board)
    - 1003, 1012, 1035 (Annual events) → PREPAID (flag with G/L 14000)

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 10: WHAT THE SCRUBBER ALREADY DOES (Don't Flag These)
═══════════════════════════════════════════════════════════════════════════════

✓ Description: Applies all 13 rules above BEFORE LLM
✓ Abbreviations: Converts Meeting→Mtg, Tkt Fee→Tkt Fee BEFORE LLM
✓ Expense code: Smart detection + remapping BEFORE LLM
✓ Pay type: CBCP → American Express BEFORE LLM
✓ Vendor: NOT normalized (user preference)

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 11: WHAT TO FLAG (Requires Human Review)
═══════════════════════════════════════════════════════════════════════════════

✓ Description missing business purpose or deal name
✓ Inflight Wifi with wrong expense code
✓ Ticketing fees coded as "Other Travel" (should be "Airline")
✓ Hotel fees NOT coded as "Lodging"
✓ Train/bus/boat fees NOT coded as "Other Travel"
✓ Project 1008 (Personal) WITHOUT Employee ID
✓ Negative amounts/refunds that don't mirror original charge
✓ LEN(description + vendor) + 12 > 70
✓ Car service early_arrival/work_late/weekend formats
✓ Trip rows with inconsistent project codes (flight + lodging + car + meals)
✓ Low confidence descriptions

═══════════════════════════════════════════════════════════════════════════════
RULE CATEGORY 12: ANALYSIS PROCESS
═══════════════════════════════════════════════════════════════════════════════

1. Identify transaction type (flight, car, meal, lodging, refund, etc.)
2. Check description format against the template for that type
3. Check expense code matches the transaction type
4. Check character length (formula: len(desc) + len(vendor) + 12 ≤ 70)
5. Check for policy violations (meal limits, project dept, personal project ID, etc.)
6. Assess confidence:
   - 0.95-1.0: Perfect match to rules, all flags green
   - 0.80-0.94: Minor issues, some ambiguity
   - 0.50-0.79: Significant issues, needs review
   - Below 0.50: Very uncertain, flag heavily
7. Compile flags list and reasoning

═══════════════════════════════════════════════════════════════════════════════
OUTPUT SCHEMA
═══════════════════════════════════════════════════════════════════════════════

Return ONLY valid JSON with these fields:

```json
{
    "transaction_type": "flight|refund|car_service|meal_business|meal_travel|meal_office|lodging|info_services|other",
    "formatted_description": "corrected description (or original if compliant)",
    "description_changed": true|false,
    "expense_code": "validated/corrected expense code",
    "expense_code_changed": true|false,
    "confidence": 0.50-1.00 (float),
    "reasoning": "Brief workbook note: what changed, why, and business context preserved",
    "flags": ["list of issues requiring human review"],
    "is_refund": true|false,
    "error": "error message if parsing/validation failed"
}
```

═══════════════════════════════════════════════════════════════════════════════
KEY PRINCIPLES
═══════════════════════════════════════════════════════════════════════════════

✓ BE CONSERVATIVE: Only change when description violates a rule
✓ PRESERVE CONTEXT: Keep all business purpose and deal names
✓ NEVER MODIFY VENDOR: Only review description and expense code
✓ USE EXACT CASING: Especially for "Inflight Wifi", "Bus.Lunch", "Mtg"
✓ ATTENDEE NAMES AS INITIALS ONLY: Meal descriptions must use initials (B.Gallagher, K.Carbonez) - never full names
✓ APPLY ALL 12 RULES: Use every rule category when applicable
✓ CONFIDENCE IS KEY: Set confidence based on how well all rules align
✓ FLAG THOROUGHLY: Don't be silent about violations; flag everything needing review"""

    def format_description(
        self,
        txn: Dict,
        similar_txns: List[Dict] = None,
        max_retries: int = 2
    ) -> Dict:
        """
        Format transaction description using LLM with chain-of-thought
        
        Args:
            txn: Transaction dictionary
            similar_txns: Similar historical transactions for context
            max_retries: Number of retry attempts for invalid JSON
            
        Returns:
            Dict with formatting results
        """
        # Build prompt
        prompt = self._build_formatting_prompt(txn, similar_txns or [])
        
        # Call Azure OpenAI
        for attempt in range(max_retries + 1):
            result_text = None
            try:
                self.api_call_count += 1
                logger.debug(f"API Call #{self.api_call_count} - Model: {self.deployment_name}, Attempt: {attempt + 1}")
                
                response = self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    # temperature=0.1,  # Deterministic
                    max_completion_tokens=1000,
                    response_format={"type": "json_object"}  # Force JSON output
                )
                
                # Get raw response text
                result_text = response.choices[0].message.content
                
                if not result_text or result_text.strip() == "":
                    error_msg = "Empty response from API"
                    logger.error(f"[FAIL] {error_msg} - Model: {self.deployment_name}, Txn: {txn.get('description', 'N/A')}")
                    self.api_error_count += 1
                    self._save_debug_response(txn, "[EMPTY_RESPONSE]", None, error_msg)
                    
                    if self._switch_model():
                        continue  # Try with fallback model
                    else:
                        return self._fallback_result(txn, "Empty response from all models")
                
                # Parse response
                result = json.loads(result_text)
                
                # Validate required fields
                required = ['formatted_description', 'confidence', 'reasoning']
                if all(field in result for field in required):
                    result.setdefault("error", "")
                    # Save successful response for reference
                    self._save_debug_response(txn, result_text, result)
                    logger.debug(f"✓ Success on attempt {attempt + 1} with {self.deployment_name}")
                    self._reset_model()  # Reset to primary model for next transaction
                    return result
                else:
                    error_msg = "Missing required fields in response"
                    logger.error(f"[FAIL] {error_msg} - Expected: {required}, Got: {list(result.keys())}")
                    self._save_debug_response(txn, result_text, result, error_msg)
                    raise ValueError(error_msg)
                    
            except json.JSONDecodeError as e:
                # Save raw response even on parse error to debug
                error_msg = f"JSON parse error: {e}"
                logger.error(f"[FAIL] {error_msg} - Model: {self.deployment_name}, Txn: {txn.get('description', 'N/A')}")
                logger.error(f"       Response text: {result_text[:200] if result_text else '[NONE]'}...")
                self.api_error_count += 1
                
                if result_text is not None:
                    self._save_debug_response(txn, result_text, None, error_msg)
                
                if self._switch_model():
                    continue  # Try with fallback model
                    
                if attempt < max_retries:
                    continue  # Retry with same model
                else:
                    # Return safe fallback
                    return self._fallback_result(txn, error_msg)
            
            except Exception as e:
                error_msg = f"API Error: {str(e)}"
                logger.error(f"[FAIL] {error_msg} - Model: {self.deployment_name}, Txn: {txn.get('description', 'N/A')}")
                logger.error(f"       Exception type: {type(e).__name__}")
                self.api_error_count += 1
                
                if result_text is not None:
                    try:
                        self._save_debug_response(txn, result_text, None, error_msg)
                    except:
                        pass
                
                if self._switch_model():
                    continue  # Try with fallback model
                    
                if attempt < max_retries:
                    continue
                else:
                    return self._fallback_result(txn, error_msg)
        
        # Should not reach here
        return self._fallback_result(txn, "Max retries exceeded")

    def format_description_batch(
        self,
        items: List[Dict],
        max_retries: int = 2
    ) -> List[Dict]:
        """
        Format multiple transactions in one LLM call.

        Args:
            items: List of dictionaries with keys:
                - index: 1-based position in the batch
                - txn: transaction dictionary
                - similar_txns: similar historical transactions
            max_retries: Number of retry attempts for invalid JSON

        Returns:
            List of result dicts in the same order as input items
        """
        if not items:
            return []
        if len(items) == 1:
            item = items[0]
            return [self.format_description(item["txn"], item.get("similar_txns") or [], max_retries=max_retries)]

        prompt = self._build_batch_formatting_prompt(items)

        for attempt in range(max_retries + 1):
            result_text = None
            try:
                self.api_call_count += 1
                logger.debug(f"Batch API Call #{self.api_call_count} - Model: {self.deployment_name}, Items: {len(items)}, Attempt: {attempt + 1}")
                
                response = self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    max_completion_tokens=2000,
                    response_format={"type": "json_object"}
                )

                result_text = response.choices[0].message.content
                
                if not result_text or result_text.strip() == "":
                    error_msg = "Empty response from batch API"
                    logger.error(f"[FAIL] {error_msg} - Model: {self.deployment_name}, Items: {len(items)}")
                    self.api_error_count += 1
                    self._save_debug_response(items[0]["txn"], "[EMPTY_RESPONSE]", None, error_msg)
                    
                    if self._switch_model():
                        continue  # Try with fallback model
                    else:
                        # Fallback: Process items individually
                        logger.warning(f"[WARN] Batch failed with all models. Processing {len(items)} items individually.")
                        return [self.format_description(item["txn"], item.get("similar_txns") or [], max_retries=max_retries) for item in items]

                # Attempt to parse JSON
                payload = json.loads(result_text)
                batch_results = payload.get("results", [])
                if not isinstance(batch_results, list):
                    raise ValueError("Batch response missing results list")

                by_index = {}
                for res in batch_results:
                    if not isinstance(res, dict):
                        continue
                    try:
                        idx = int(res.get("index"))
                    except (TypeError, ValueError):
                        continue
                    res.setdefault("error", "")
                    by_index[idx] = res

                ordered = []
                for input_item in items:
                    idx = int(input_item["index"])
                    result = by_index.get(idx)
                    if not result:
                        raise ValueError(f"Missing batch result for index {idx}")
                    ordered.append(result)
                
                # Success! Save debug info for first transaction
                self._save_debug_response(items[0]["txn"], result_text, payload)
                logger.debug(f"✓ Batch success on attempt {attempt + 1} with {self.deployment_name} - {len(ordered)} results")
                self._reset_model()  # Reset to primary model for next batch
                return ordered

            except json.JSONDecodeError as e:
                # Save raw response and error for debugging
                error_msg = f"JSON parse error: {e}"
                logger.error(f"[FAIL] {error_msg} - Model: {self.deployment_name}, Items: {len(items)}")
                logger.error(f"       Response text: {result_text[:200] if result_text else '[NONE]'}...")
                self.api_error_count += 1
                
                if result_text is not None:
                    self._save_debug_response(items[0]["txn"], result_text, None, error_msg)
                
                if self._switch_model():
                    continue  # Try with fallback model
                    
                if attempt < max_retries:
                    continue
                # Fallback: Process items individually when batch parsing fails
                logger.warning(f"[WARN] Batch JSON parsing failed. Processing {len(items)} items individually.")
                return [self.format_description(item["txn"], item.get("similar_txns") or [], max_retries=max_retries) for item in items]

            except Exception as e:
                error_msg = f"Batch API Error: {str(e)}"
                logger.error(f"[FAIL] {error_msg} - Model: {self.deployment_name}, Items: {len(items)}")
                logger.error(f"       Exception type: {type(e).__name__}")
                self.api_error_count += 1
                
                if result_text is not None:
                    try:
                        self._save_debug_response(items[0]["txn"], result_text, None, error_msg)
                    except:
                        pass
                
                if self._switch_model():
                    continue  # Try with fallback model
                        
                if attempt < max_retries:
                    continue
                # Fallback: Process items individually when batch API fails
                logger.warning(f"[WARN] Batch API error after {attempt + 1} attempts. Processing {len(items)} items individually.")
                return [self.format_description(item["txn"], item.get("similar_txns") or [], max_retries=max_retries) for item in items]

        return [self._fallback_result(item["txn"], "Max retries exceeded") for item in items]
    
    def _build_formatting_prompt(self, txn: Dict, similar_txns: List[Dict]) -> str:
        """Build detailed prompt for transaction formatting with complete transaction & receipt details"""
        
        # Format current transaction with ALL available fields
        prompt = f"""# Transaction to Format

## EMPLOYEE DETAILS
- First Name: {txn.get('employee_first_name', '')}
- Middle Name: {txn.get('employee_middle_name', '')}
- Last Name: {txn.get('employee_last_name', '')}
- Employee ID: {txn.get('employee_id', '')}

## TRANSACTION DETAILS (from Concur)
- Transaction Date: {txn.get('transaction_date', '')}
- Description: {txn.get('description', '')}
- Amount: ${txn.get('amount', 0):.2f}
- Payment Type: {txn.get('pay_type', '')}
- Expense Type: {txn.get('expense_code', '')}
- Vendor (short): {txn.get('vendor', '')}
- Vendor (description): {txn.get('vendor_desc', '')}
- Project: {txn.get('project', '')}
- Cost Center/Department: {txn.get('cost_center', '')}
- Report Purpose: {txn.get('report_purpose', '')}

## RECEIPT DETAILS (from Concur receipt data)
- Receipt ID: {txn.get('receipt_id', '')}
- Order ID: {txn.get('order_id', '')}
- Receipt Date: {txn.get('receipt_date', '')}
- Receipt Vendor: {txn.get('receipt_vendor', '')}
- Receipt Amount: ${txn.get('receipt_amount', 0):.2f}
- Receipt Summary: {txn.get('receipt_summary', '')}
- Ticket Number: {txn.get('receipt_ticket_number', '')}
- Passenger Name: {txn.get('receipt_passenger', '')}
- Travel Route: {txn.get('receipt_route', '')}

## YOUR TASK

Analyze this transaction using ALL the details above and format the description according to AEA rules.

CRITICAL: Use BOTH transaction details AND receipt details together to understand:
1. What type of transaction is this? (flight, meal, car service, lodging, refund, etc.)
2. Does the current description follow the correct format template?
3. If not, what corrections are needed? (use receipt details to validate)
4. Is the expense code correct for this transaction type?
5. Are there any issues that need human review? (mismatches, missing data, policy issues?)

Keep the reasoning short and human-readable, like a workbook note.
Only describe changes that were actually made or flagged.
Do not mention vendor normalization.

Return your analysis as JSON matching the output schema.
"""
        return prompt

    def _build_batch_formatting_prompt(self, items: List[Dict]) -> str:
        """Build a batch prompt for multiple transactions with full details."""
        batch_sections = []
        for item in items:
            txn = item["txn"]
            similar_txns = item.get("similar_txns") or []

            batch_sections.append(f"""### Item {item['index']}
Employee: {txn.get('employee_first_name', '')} {txn.get('employee_last_name', '')} (ID: {txn.get('employee_id', '')})
Txn Date: {txn.get('transaction_date', '')}; Txn Description: {txn.get('description', '')}
Txn Vendor: {txn.get('vendor', '')} ({txn.get('vendor_desc', '')}); Amount: ${txn.get('amount', 0):.2f}
Pay Type: {txn.get('pay_type', '')}; Expense: {txn.get('expense_code', '')}; Project: {txn.get('project', '')}; Cost Center: {txn.get('cost_center', '')}
Receipt ID: {txn.get('receipt_id', '')}; Receipt Date: {txn.get('receipt_date', '')}; Receipt Vendor: {txn.get('receipt_vendor', '')}; Rcpt Amount: ${txn.get('receipt_amount', 0):.2f}
Receipt Summary: {txn.get('receipt_summary', '')}; Ticket: {txn.get('receipt_ticket_number', '')}; Route: {txn.get('receipt_route', '')}; Passenger: {txn.get('receipt_passenger', '')}""")

        schema_block = (
            '```json\n'
            '{\n'
            '  "results": [\n'
            '    {\n'
            '      "index": 1,\n'
            '      "transaction_type": "flight|refund|car_service|meal_business|meal_travel|lodging|info_services|other",\n'
            '      "formatted_description": "corrected description or original if already correct",\n'
            '      "description_changed": true,\n'
            '      "expense_code": "validated expense code",\n'
            '      "expense_code_changed": false,\n'
            '      "confidence": 0.0,\n'
            '      "reasoning": "brief workbook note explaining what changed and why",\n'
            '      "flags": ["list any issues requiring human review"],\n'
            '      "is_refund": false,\n'
            '      "error": ""\n'
            '    }\n'
            '  ]\n'
            '}\n'
            '```'
        )

        transactions_text = "\n".join(batch_sections)

        prompt = (
            "# Transactions to Format\n\n"
            "Analyze each transaction independently using ALL available details (Concur + Receipt data) and return one JSON result per item.\n\n"
            "## Rules\n"
            "- Keep vendor untouched.\n"
            "- Be conservative.\n"
            "- Use BOTH transaction and receipt data to validate and format descriptions.\n"
            "- Only change description or expense code when clearly required.\n"
            "- Keep reasoning short and workbook-style.\n"
            "- Return every item in the same order.\n\n"
            "## Output Format\n"
            "Return ONLY valid JSON:\n"
            f"{schema_block}\n\n"
            "## Transactions\n\n"
            f"{transactions_text}\n\n"
            "Return your analysis as JSON matching the output schema.\n"
        )
        return prompt
    
    def _fallback_result(self, txn: Dict, error_msg: str) -> Dict:
        """Return safe fallback result when LLM fails"""
        return {
            "transaction_type": "unknown",
            "formatted_description": txn.get('description', ''),
            "description_changed": False,
            "expense_code": txn.get('expense_code', ''),
            "expense_code_changed": False,
            "confidence": 0.5,
            "reasoning": f"LLM formatting failed: {error_msg}",
            "flags": ["LLM processing error - needs manual review"],
            "is_refund": float(txn.get('amount', 0)) < 0,
            "error": error_msg
        }
    
    def batch_format(
        self,
        transactions: List[Dict],
        memory = None,
        use_cache: bool = True,
        batch_size: int = 10
    ) -> List[Dict]:
        """
        Format multiple transactions with intelligent batching and optional caching
        
        Args:
            transactions: List of transaction dicts
            memory: TransactionMemory instance for finding similar txns
            use_cache: Whether to use caching
            batch_size: Number of transactions per LLM call (default 10)
            
        Returns:
            List of formatting results in original order
        """
        if not transactions:
            return []
        
        total = len(transactions)
        results = []
        
        # Process in batches to reduce LLM API calls
        num_batches = (total + batch_size - 1) // batch_size
        
        for batch_idx in range(num_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, total)
            batch_txns = transactions[start_idx:end_idx]
            
            print(f"  Processing batch {batch_idx + 1}/{num_batches} ({len(batch_txns)} txns)...", end='\r')
            
            # Prepare batch items with indices
            batch_items = []
            for i, txn in enumerate(batch_txns, 1):
                batch_items.append({
                    "index": i,
                    "txn": txn,
                    "similar_txns": []  # Not used in prompts anymore
                })
            
            # Format batch in single LLM call
            batch_results = self.format_description_batch(batch_items)
            results.extend(batch_results)
        
        print(f"  [BATCHING STATS] Processed {total} transactions in {num_batches} API calls " + 
              f"(batch_size={batch_size}, reduction={100 * (1 - num_batches / total):.1f}%)" + " " * 10)
        return results
    
    def batch_format_streaming(
        self,
        transactions: List[Dict],
        memory = None,
        batch_size: int = 10,
        on_batch_complete = None
    ) -> List[Dict]:
        """
        Format transactions in batches with callback for streaming results.
        Useful for large datasets where you want to process and save results incrementally.
        
        Args:
            transactions: List of transaction dicts
            memory: TransactionMemory instance for finding similar txns
            batch_size: Number of transactions per LLM call (default 10)
            on_batch_complete: Callback function(batch_idx, results) called after each batch
            
        Returns:
            List of all formatting results in original order
        """
        if not transactions:
            return []
        
        total = len(transactions)
        all_results = []
        num_batches = (total + batch_size - 1) // batch_size
        
        for batch_idx in range(num_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, total)
            batch_txns = transactions[start_idx:end_idx]
            
            # Prepare batch items
            batch_items = []
            for i, txn in enumerate(batch_txns, 1):
                batch_items.append({
                    "index": i,
                    "txn": txn,
                    "similar_txns": []  # Not used in prompts anymore
                })
            
            # Process batch
            batch_results = self.format_description_batch(batch_items)
            all_results.extend(batch_results)
            
            # Call callback if provided
            if on_batch_complete:
                on_batch_complete(batch_idx + 1, num_batches, batch_results)
            else:
                pct = 100 * (batch_idx + 1) / num_batches
                print(f"  [{pct:3.0f}%] Batch {batch_idx + 1}/{num_batches} complete ({len(batch_results)} results)")
        
        return all_results
    
    @staticmethod
    def estimate_batching_benefit(num_transactions: int, batch_size: int = 10) -> Dict:
        """
        Calculate API call reduction and cost savings from batching.
        
        Args:
            num_transactions: Total number of transactions to process
            batch_size: Transactions per batch (default 10)
            
        Returns:
            Dict with metrics showing API call reduction
        """
        sequential_calls = num_transactions  # One call per transaction
        batch_calls = (num_transactions + batch_size - 1) // batch_size
        calls_saved = sequential_calls - batch_calls
        reduction_pct = 100 * (1 - batch_calls / sequential_calls) if sequential_calls > 0 else 0
        
        return {
            "total_transactions": num_transactions,
            "batch_size": batch_size,
            "sequential_api_calls": sequential_calls,
            "batched_api_calls": batch_calls,
            "api_calls_saved": calls_saved,
            "reduction_percentage": round(reduction_pct, 1),
            "estimated_time_reduction": f"~{round(reduction_pct * 0.9, 1)}%",  # LLM overhead factor
            "estimated_cost_reduction": f"~{round(reduction_pct * 0.9, 1)}%"
        }