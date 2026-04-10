# Expense Reconciliation Pipeline

Extracts structured financial data from expense PDFs using Azure OpenAI and outputs formatted Excel reports.

## Project Structure

```
expense_reconciliation/
├── main.py               # Entry point — iterates input folder
├── config.py             # All settings loaded from environment
├── extractor.py          # LLM extraction logic
├── excel_writer.py       # Excel report generation
├── metrics.py            # LLM cost & token tracking
├── .env.example          # Environment variable template
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env values
python main.py
```

## Output Naming Convention

`{original_filename_stem}_reconciliation_{YYYYMMDD_HHMMSS}.xlsx`

Example: `Baker_$906.50_reconciliation_20260326_143022.xlsx`

## Logs

Logs are written to both console and `logs/pipeline.log`.
Per-file metrics (tokens, cost, latency) are appended to `logs/metrics.jsonl`.
