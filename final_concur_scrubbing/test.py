from openai import OpenAI
import os
from dotenv import load_dotenv
load_dotenv()
# -----------------------------
# CONFIG
# -----------------------------
# PDF_PATH = r"C:\Users\SKatkar\OneDrive\GPFS\aea_concur_scrubbing\final_concur_scrubbing\inputs\2026\03-MARCH\Final Concur Reports with Receipts and Approvals\Smith $12,367.39.pdf"
PDF_PATH = r"C:\Users\SKatkar\OneDrive\GPFS\aea_concur_scrubbing\final_concur_scrubbing\inputs\2026\04-APRIL\AmEx Statements\Individual Statements\SENKFOR_H_APR052026.pdf"
MODEL_NAME = "gpt-5-mini"

# -----------------------------
# INIT CLIENT
# -----------------------------
def get_client():
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise ValueError("AZURE_OPENAI_API_KEY not set")
    return OpenAI(api_key=api_key,base_url=os.getenv("AZURE_OPENAI_BASE_URL"))


# -----------------------------
# UPLOAD PDF
# -----------------------------
def upload_pdf(client, pdf_path):
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"File not found: {pdf_path}")

    with open(pdf_path, "rb") as f:
        file = client.files.create(
            file=f,
            purpose="assistants"   # important
        )

    print(f"✅ Uploaded PDF | File ID: {file.id}")
    return file.id


# -----------------------------
# EXTRACT DATA FROM PDF
# -----------------------------
def extract_pdf_data(client, file_id):
    response = client.responses.create(
        model=MODEL_NAME,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": """
Extract total row amount current closing.

Return ONLY number amount.
"""
                    },
                    {
                        "type": "input_file",
                        "file_id": file_id
                    }
                ]
            }
        ]
    )

    print("\n📊 Extracted Data:\n")
    print(response.output_text)

    return response.output_text


# -----------------------------
# MAIN
# -----------------------------
def main():
    client = get_client()

    # Step 1: Upload PDF
    file_id = upload_pdf(client, PDF_PATH)

    # Step 2: Extract Data
    extract_pdf_data(client, file_id)
    response = client.files.delete(file_id)
    print(f"🗑️ Deleted file from OpenAI | File ID: {file_id}, {response}")

if __name__ == "__main__":
    main()