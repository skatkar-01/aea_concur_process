# AMEX Statement Processor

Production-ready pipeline that extracts data from American Express corporate PDF statements using Azure OpenAI (GPT-4o) and outputs formatted XLSX workbooks.

---

## Project Structure

```
amex_processor/
├── config/
│   ├── __init__.py
│   └── settings.py          # Pydantic-settings; all config from env/.env
├── src/
│   ├── __init__.py
│   ├── models.py            # Pydantic domain models (Statement, Cardholder, Transaction)
│   ├── extractor.py         # PDF → JSON via Azure OpenAI (cache, retry, metrics)
│   ├── writer.py            # Validated Statement → formatted XLSX
│   └── pipeline.py          # Orchestrator: single file or batch
├── utils/
│   ├── __init__.py
│   ├── logging_config.py    # structlog: JSON (prod) or console (dev), rotating files
│   └── metrics.py           # Prometheus counters, histograms, gauges
├── tests/
│   ├── test_models.py       # Unit tests for Pydantic models
│   └── test_writer.py       # Integration tests for XLSX writer
├── inputs/
│   └── amex/                # Drop PDFs here (or set INPUT_DIR)
├── outputs/                 # XLSX files are written here
├── cache/                   # Content-addressed JSON extraction cache
├── logs/                    # Rotating log files (amex_processor.log)
├── main.py                  # CLI entry point
├── requirements.txt
├── .env.example
└── pytest.ini
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your Azure OpenAI key and endpoint
```

### 3. Add PDFs

```bash
cp /path/to/statement.pdf inputs/amex/
```

### 4. Run

```bash
# Process all PDFs in inputs/amex/
python main.py

# Process a single file
python main.py --file inputs/amex/BAKER_C_Feb_042026.pdf

# Force fresh API call (ignore cache)
python main.py --no-cache

# Enable Prometheus metrics server on :9090
python main.py --metrics
```

---

## Configuration

All settings are read from environment variables or a `.env` file.

| Variable                 | Default          | Description                          |
|--------------------------|------------------|--------------------------------------|
| `AZURE_OPENAI_API_KEY`   | *(required)*     | Azure OpenAI API key                 |
| `AZURE_OPENAI_BASE_URL`  | *(required)*     | Azure OpenAI endpoint URL            |
| `AZURE_OPENAI_MODEL`     | `gpt-4o`         | Model / deployment name              |
| `INPUT_DIR`              | `inputs/amex`    | Folder scanned for PDFs (batch mode) |
| `OUTPUT_DIR`             | `outputs`        | Destination for XLSX files           |
| `CACHE_DIR`              | `cache`          | JSON extraction cache directory      |
| `LOG_DIR`                | `logs`           | Log file directory                   |
| `CACHE_ENABLED`          | `true`           | Skip API if cache hit exists         |
| `MAX_RETRIES`            | `3`              | Max OpenAI retry attempts            |
| `RETRY_WAIT_SECONDS`     | `2`              | Base seconds between retries         |
| `LOG_LEVEL`              | `INFO`           | Root log level                       |
| `LOG_FORMAT`             | `json`           | `json` (prod) or `console` (dev)     |
| `METRICS_ENABLED`        | `true`           | Start Prometheus HTTP server         |
| `METRICS_PORT`           | `9090`           | Prometheus metrics port              |

---

## Features

### Caching
Extraction results are cached by **SHA-256 of the PDF content** — the same file always hits the same cache entry regardless of its filename. Delete `cache/` or pass `--no-cache` to force a fresh API call.

### Retry with Exponential Back-off
Azure OpenAI calls are retried up to `MAX_RETRIES` times with exponential back-off (via [tenacity](https://github.com/jd/tenacity)). Each retry attempt is counted in the `amex_api_retries_total` Prometheus metric.

### Structured Logging
All logs are emitted via [structlog](https://www.structlog.org/) as JSON (production) or coloured console (development). Logs rotate at 10 MB with 7 retained files.

```json
{"event": "extraction_complete", "cardholders": 3, "transactions": 47, "pdf": "BAKER_C.pdf", "level": "info", "timestamp": "2026-01-15T14:23:01Z"}
```

### Prometheus Metrics
When enabled, a metrics server is exposed at `http://localhost:9090/metrics`.

| Metric                              | Type      | Description                       |
|-------------------------------------|-----------|-----------------------------------|
| `amex_files_processed_total`        | Counter   | Files successfully processed      |
| `amex_files_failed_total`           | Counter   | Files that raised an error        |
| `amex_files_cached_total`           | Counter   | Files served from cache           |
| `amex_transactions_extracted_total` | Counter   | Total transaction rows extracted  |
| `amex_cardholders_extracted_total`  | Counter   | Total cardholder blocks extracted |
| `amex_extraction_duration_seconds`  | Histogram | API call latency per file         |
| `amex_xlsx_write_duration_seconds`  | Histogram | XLSX write latency per file       |
| `amex_pipeline_duration_seconds`    | Histogram | End-to-end latency per file       |
| `amex_api_retries_total`            | Counter   | Number of API retry attempts      |
| `amex_api_failures_total`           | Counter   | API calls that ultimately failed  |

---

## Running Tests

```bash
pytest tests/ -v
```

Tests do **not** require an Azure OpenAI key — the writer and model tests are fully offline.

---

## Development Tips

```bash
# Console-mode logging for readable local output
LOG_FORMAT=console LOG_LEVEL=DEBUG python main.py --file inputs/amex/sample.pdf

# Delete cache to test a fresh extraction
rm -rf cache/
python main.py
```

---

## Architecture

```
main.py (CLI)
    │
    ├── config/settings.py          ← environment config
    │
    └── src/pipeline.py             ← orchestrator
            │
            ├── src/extractor.py    ← PDF → validated Statement
            │       ├── cache layer (SHA-256 content hash)
            │       ├── Azure OpenAI call (tenacity retry)
            │       └── src/models.py (Pydantic validation)
            │
            └── src/writer.py       ← Statement → XLSX
                    └── openpyxl (styled workbook)

utils/
    ├── logging_config.py           ← structlog JSON/console + file rotation
    └── metrics.py                  ← Prometheus counters + histograms
```
