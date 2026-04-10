"""
PDF Q&A using Azure AI Foundry — OpenAI Responses API
------------------------------------------------------
• PDF is sent directly as a file object (no text extraction)
• Model: configured via AZURE_DEPLOYMENT_NAME in .env
• API:   OpenAI Responses API  (POST /responses)
• Auth:  Azure API Key from .env
"""

import os
import sys
import base64
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv()

ENDPOINT        = os.environ["AZURE_OPENAI_ENDPOINT"]
API_KEY         = os.environ["AZURE_OPENAI_API_KEY"]
API_VERSION     = "2025-03-01-preview",
DEPLOYMENT      = os.environ.get("AZURE_DEPLOYMENT_NAME", "gpt-4o-mini")
PDF_PATH        = os.environ.get("PDF_FILE_PATH", "./Baker $906.50.pdf")


# ── Azure client ──────────────────────────────────────────────────────────────
client = AzureOpenAI(
    azure_endpoint=ENDPOINT,
    api_key=API_KEY,
    api_version=API_VERSION,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def load_pdf_as_base64(path: str) -> str:
    """Read PDF from disk and return base64-encoded string."""
    pdf_bytes = Path(path).read_bytes()
    return base64.standard_b64encode(pdf_bytes).decode("utf-8")


def ask_question(pdf_b64: str, question: str) -> str:
    """
    Send the PDF + question to the model via the Responses API.
    The PDF is attached as an inline base64 file — no text pre-extraction.
    """
    response = client.responses.create(
        model=DEPLOYMENT,
        input=[
            {
                "role": "user",
                "content": [
                    # ── Direct PDF input ──────────────────────────────────
                    {
                        "type": "input_file",
                        "filename": Path(PDF_PATH).name,
                        "file_data": f"data:application/pdf;base64,{pdf_b64}",
                    },
                    # ── User question ─────────────────────────────────────
                    {
                        "type": "input_text",
                        "text": question,
                    },
                ],
            }
        ],
        instructions=(
            "You are a precise document analyst. "
            "Answer questions strictly based on the provided PDF. "
            "If the answer is not in the document, say so clearly."
        ),
    )

    # Extract text from the Responses API output
    for block in response.output:
        if block.type == "message":
            for part in block.content:
                if part.type == "output_text":
                    return part.text

    return "(No answer returned)"


# ── Interactive Q&A loop ──────────────────────────────────────────────────────
def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else PDF_PATH

    if not Path(pdf_path).exists():
        print(f"[ERROR] PDF not found: {pdf_path}")
        sys.exit(1)

    print(f"\n📄  Loaded PDF : {pdf_path}")
    print(f"🤖  Model      : {DEPLOYMENT}")
    print(f"🌐  Endpoint   : {ENDPOINT}")
    print("─" * 55)
    print("Type your question and press Enter.  Type 'exit' to quit.\n")

    # Encode PDF once; reuse for every question in the session
    pdf_b64 = load_pdf_as_base64(pdf_path)

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not question:
            continue
        if question.lower() in {"exit", "quit", "q"}:
            print("Goodbye!")
            break

        print("AI : ", end="", flush=True)
        answer = ask_question(pdf_b64, question)
        print(answer)
        print()


if __name__ == "__main__":
    main()