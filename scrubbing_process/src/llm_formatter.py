"""
llm_formatter.py - LLM-based Description Formatter using Azure OpenAI
"""

import json
import os
from typing import Dict, List, Optional
from openai import AzureOpenAI


class LLMFormatter:
    """
    Use Azure OpenAI (GPT-5-mini) to format descriptions with chain-of-thought reasoning
    """
    
    def __init__(
        self,
        azure_endpoint: str = None,
        api_key: str = None,
        api_version: str = "2024-02-15-preview",
        deployment_name: str = "gpt-5-mini"
    ):
        """
        Initialize Azure OpenAI client
        
        Args:
            azure_endpoint: Azure OpenAI endpoint URL
            api_key: Azure OpenAI API key
            api_version: API version
            deployment_name: Deployment name (model)
        """
        self.azure_endpoint = azure_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
        self.api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        self.deployment_name = deployment_name or os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
        
        if not self.azure_endpoint or not self.api_key:
            raise ValueError(
                "Azure OpenAI credentials required. Set AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_API_KEY environment variables."
            )
        
        # Initialize client
        self.client = AzureOpenAI(
            azure_endpoint=self.azure_endpoint,
            api_key=self.api_key,
            api_version=api_version
        )
        
        print(f"✓ Azure OpenAI initialized (deployment: {self.deployment_name})")
        
        # Load system prompt
        self.system_prompt = self._build_system_prompt()
    
    def _build_system_prompt(self) -> str:
        """Build comprehensive system prompt for expense scrubbing"""
        return """You are an expert AmEx expense data scrubber for AEA Investors LP.

Your task is to format transaction descriptions according to AEA business rules.

# AEA Description Formatting Rules

## Transaction Types & Formats

### Flights
Format: RT:<airports>/<purpose>/<deal or company>
Example: "RT:JFK-STO/Fundraising/Growth Fund"
- Include RT: for round trips
- One-way: "EWR-SLC/Strategy Mtg/Amateras"

### Flight Fees/Changes
- Ticketing Fee: "Tkt Fee/RT:LGA-ORD/Mgmt Mtg/Tangent"
- Seat Upgrade: "Seat Upgrade/<purpose>/<deal>"
- Exchange: "Exch Tkt/<route>/<purpose>/<deal>"

### Refunds
MUST start with "Refund/" followed by original description format
Example: "Refund/RT:JFK-SLC/Strategy Mtg"

### Car Service
Format: <from>-<to>/<purpose>/<deal>
Example: "JFK-Hotel/BOD Mtg/Chemical Guys"

Special formats (do NOT modify):
- "Work Late/Office-Home" (evening departures)
- "Early Arrival/Home-Office" (morning arrivals ≤7am)
- "Weekend/Home-Office" or "Weekend/Office-Home"

### Business Meals
Format: Bus.Lunch or Bus.Dinner/<attendees>/<deal>
Example: "Bus.Lunch/K.Carbonez/Goldman Sachs"
- Include attendee names/initials
- Can include guest count: "Bus.Dinner/BOD Dinner/17 ppl/Redwood"

### Travel Meals
Format: Travel Meal/<purpose>/<deal>
Example: "Travel Meal/BOD Mtg/Numotion"

### Office Meals
- "Working Lunch" or "Working Dinner"
- Overage: "Working Lunch/Overage/Personal"

### Lodging
Format: Lodging/<purpose>/<deal>
Example: "Lodging/BOD Mtg/AmeriVet"

### Info Services
- "Inflight Wifi" (exact casing)
- Or: "Research Subscription/<purpose>/<deal>"

### Other Travel
- "Bus.Parking/<purpose>/<deal>"
- "Bus.Fuel/<route>/<event>"
- "Train/<route>/<purpose>/<company>"

# Key Rules
1. Keep descriptions concise but complete
2. Include business purpose when applicable
3. Include deal/company name at end when relevant
4. Use proper abbreviations: Mtg, Tkt, Bus.
5. No spaces around dashes in routes: "JFK-STO" not "JFK - STO"
6. Maximum length: description + vendor + 12 chars ≤ 70 total

# Analysis Process
1. Identify transaction type
2. Check if description follows correct format
3. If incorrect, reformat according to rules
4. Validate expense code matches transaction type
5. Assess confidence level

# Output Format
Return ONLY valid JSON:
```json
{
    "transaction_type": "flight|refund|car_service|meal_business|meal_travel|lodging|info_services|other",
    "formatted_description": "corrected description or original if already correct",
    "description_changed": true/false,
    "expense_code": "validated expense code",
    "expense_code_changed": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation of changes made",
    "flags": ["list any issues requiring human review"],
    "is_refund": true/false
}
```

Be conservative - only change descriptions that violate formatting rules.
Keep all business context and deal names from the original."""

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
            try:
                response = self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,  # Deterministic
                    max_tokens=1000,
                    response_format={"type": "json_object"}  # Force JSON output
                )
                
                # Parse response
                result_text = response.choices[0].message.content
                result = json.loads(result_text)
                
                # Validate required fields
                required = ['formatted_description', 'confidence', 'reasoning']
                if all(field in result for field in required):
                    return result
                else:
                    raise ValueError(f"Missing required fields in response")
                    
            except json.JSONDecodeError as e:
                if attempt < max_retries:
                    continue  # Retry
                else:
                    # Return safe fallback
                    return self._fallback_result(txn, f"JSON parse error: {e}")
            
            except Exception as e:
                if attempt < max_retries:
                    continue
                else:
                    return self._fallback_result(txn, f"Error: {e}")
        
        # Should not reach here
        return self._fallback_result(txn, "Max retries exceeded")
    
    def _build_formatting_prompt(self, txn: Dict, similar_txns: List[Dict]) -> str:
        """Build detailed prompt for transaction formatting"""
        
        # Build similar transactions context
        similar_context = ""
        if similar_txns:
            similar_context = "\n## Similar Historical Transactions\n"
            for i, sim in enumerate(similar_txns[:3], 1):
                similar_context += f"\nExample {i}:\n"
                similar_context += f"Description: {sim.get('description', 'N/A')}\n"
                similar_context += f"Vendor: {sim.get('vendor', 'N/A')}\n"
                similar_context += f"Expense: {sim.get('expense_code', 'N/A')}\n"
                similar_context += f"Amount: ${sim.get('amount', 0):.2f}\n"
                if sim.get('receipt_summary'):
                    similar_context += f"Receipt: {sim.get('receipt_summary')}\n"
                if sim.get('receipt_ticket_number'):
                    similar_context += f"Ticket: {sim.get('receipt_ticket_number')}\n"
                if sim.get('receipt_route'):
                    similar_context += f"Route: {sim.get('receipt_route')}\n"
        
        prompt = f"""# Transaction to Format

## Current Transaction
Employee: {txn.get('employee_first_name', '')} {txn.get('employee_last_name', '')}
Date: {txn.get('transaction_date', '')}
Description: {txn.get('description', '')}
Vendor: {txn.get('vendor', '')}
Amount: ${txn.get('amount', 0):.2f}
Expense Type: {txn.get('expense_code', '')}
Project: {txn.get('project', '')}
Department: {txn.get('cost_center', '')}
{similar_context}

## Your Task

Analyze this transaction and format the description according to AEA rules.

Use step-by-step reasoning:
1. What type of transaction is this? (flight, meal, car service, etc.)
2. Does the current description follow the correct format template?
3. If not, what corrections are needed?
4. Is the expense code correct for this transaction type?
5. Are there any issues that need human review?

Return your analysis as JSON matching the output schema.
"""
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
            "is_refund": float(txn.get('amount', 0)) < 0
        }
    
    def batch_format(
        self,
        transactions: List[Dict],
        memory = None,
        use_cache: bool = True
    ) -> List[Dict]:
        """
        Format multiple transactions with optional caching
        
        Args:
            transactions: List of transaction dicts
            memory: TransactionMemory instance for finding similar txns
            use_cache: Whether to use caching
            
        Returns:
            List of formatting results
        """
        results = []
        
        for i, txn in enumerate(transactions, 1):
            print(f"  Formatting {i}/{len(transactions)}...", end='\r')
            
            # Find similar transactions if memory available
            similar = []
            if memory:
                similar = memory.find_similar(txn, top_k=3)
            
            # Format
            result = self.format_description(txn, similar)
            results.append(result)
        
        print(f"  ✓ Formatted {len(transactions)} transactions" + " " * 20)
        return results
