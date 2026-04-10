# SAP Concur Report Extractor

Production-ready LLM-powered extractor for SAP Concur expense reports.

## Project Structure

```
sap_concur_extractor/
├── main.py                        # Entry point & orchestrator
├── config.py                      # All configuration (env-driven)
├── requirements.txt
├── .env.example
│
├── clients/                       # Pluggable LLM clients
│   ├── __init__.py
│   ├── base_client.py             # Abstract base
│   ├── gemini_client.py           # Google Gemini
│   ├── claude_client.py           # Anthropic Claude
│   ├── azure_openai_client.py     # Azure OpenAI
│   └── client_factory.py          # Factory (swap with 1 env var)
│
├── extractor/
│   ├── __init__.py
│   └── concur_extractor.py        # Core extraction logic
│
├── processor/
│   ├── __init__.py
│   └── output_processor.py        # Save CSV/Excel outputs
│
├── prompts/
│   └── concur_prompt.py           # Prompt template
│
└── utils/
    ├── __init__.py
    ├── cost_tracker.py            # Per-call cost tracking & reports
    ├── logger.py                  # Structured JSON logging
    └── file_utils.py              # File reading (PDF, images, text)
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill env vars
cp .env.example .env

# 3. Drop SAP Concur reports in ./input/

# 4. Run
python main.py

# Optional flags
python main.py --input ./my_reports --output ./results --provider gemini
```

## Switching LLM Provider

Change just ONE env var:

```env
LLM_PROVIDER=gemini          # Google Gemini (default)
LLM_PROVIDER=claude          # Anthropic Claude
LLM_PROVIDER=azure_openai    # Azure OpenAI
```

## Output Files

Each report produces 4 files named `{emp_name}_{report_date}_{type}.csv`:

```
output/
  john_doe_2024-03-15_transactions.csv
  john_doe_2024-03-15_receipts.csv
  john_doe_2024-03-15_reconciliation.csv
  john_doe_2024-03-15_approvals.csv
```

## Cost Tracking

After each run, a cost report is saved to `cost_reports/run_{timestamp}.json`:

```json
{
  "run_id": "run_20240315_143022",
  "total_cost_usd": 0.0042,
  "total_input_tokens": 12400,
  "total_output_tokens": 3200,
  "calls": [...]
}
```
