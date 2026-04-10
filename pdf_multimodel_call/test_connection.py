

import os
import base64
from dotenv import load_dotenv
from openai import AzureOpenAI
from openai import OpenAI

# =========================
# LOAD ENV
# =========================
load_dotenv()

client = OpenAI(
   )

deployment = "gpt-5-mini"
pdf_path = "./Baker $906.50.pdf"

# =========================
# READ PDF → BASE64
# =========================
# with open(pdf_path, "rb") as f:
#     pdf_base64 = base64.b64encode(f.read()).decode("utf-8")

# =========================
# USER QUESTION
# =========================
# question = input("Ask a question about the PDF: ")
import base64
from openai import OpenAI

with open(pdf_path, "rb") as f:
    data = f.read()

base64_string = base64.b64encode(data).decode("utf-8")

response = client.responses.create(
    model="gpt-5-mini",
    input=[
        {
            "role": "user",
            "content": [
                {
                    "type": "input_file",
                    "filename": "Baker $906.50.pdf",
                    "file_data": f"data:application/pdf;base64,{base64_string}",
                },
                {
                    "type": "input_text",
                    "text": "extract the data from the pdf and summarize",
                },
            ],
        },
    ]
)

print(response.output_text)












# # =========================
# # CALL MODEL WITH ATTACHMENT
# # =========================
# response = client.responses.create(
#     model=deployment,
#     input=[{
#         "role": "user",
#         "content": [
#             {
#                 "type": "input_text",
#                 "text": question
#             },
#             {
#                 "type": "input_file",
#                 "file": {
#                     "name": "Baker $906.50.pdf",
#                     "data": pdf_base64
#                 }
#             }
#         ]
#     }]
# )
# # =========================
# # OUTPUT
# # =========================
# print("\n===== ANSWER =====\n")
# print(response.output[0].content[0].text)

# import os
# import sys
# import base64
# from openai import AzureOpenAI
# from dotenv import load_dotenv

# load_dotenv()

# # ── Azure AI Foundry client ──────────────────────────────────────────────────
# client = AzureOpenAI(
#     api_key=os.getenv("AZURE_OPENAI_API_KEY"),
#     api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
#     azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
# )

# DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")  # set "gpt-5-mini" when available


# # ── Load PDF as base64 ───────────────────────────────────────────────────────
# def load_pdf(pdf_path: str) -> tuple[str, str]:
#     """Returns (filename, base64_data)"""
#     with open(pdf_path, "rb") as f:
#         data = base64.standard_b64encode(f.read()).decode("utf-8")
#     return os.path.basename(pdf_path), data


# # ── Single question → answer ─────────────────────────────────────────────────
# def ask(filename: str, pdf_b64: str, question: str) -> str:
#     """Send PDF file directly to the model along with the question."""
#     response = client.chat.completions.create(
#         model=DEPLOYMENT,
#         messages=[
#             {
#                 "role": "user",
#                 "content": [
#                     {
#                         "type": "file",                          # ← PDF sent as-is, no text extraction
#                         "file": {
#                             "filename": filename,
#                             "file_data": f"data:application/pdf;base64,{pdf_b64}",
#                         },
#                     },
#                     {
#                         "type": "text",
#                         "text": question,
#                     },
#                 ],
#             }
#         ],
#     )
#     return response.choices[0].message.content


# # ── CLI entry point ───────────────────────────────────────────────────────────
# def main():
#     # Accept PDF path as CLI arg or prompt
#     if len(sys.argv) > 1:
#         pdf_path = sys.argv[1]
#     else:
#         pdf_path = input("PDF path: ").strip()

#     if not os.path.isfile(pdf_path):
#         print(f"[ERROR] File not found: {pdf_path}")
#         sys.exit(1)

#     print(f"\n✅ Loaded: {pdf_path}")
#     print(f"🤖 Model : {DEPLOYMENT}")
#     print("Type 'exit' to quit.\n")

#     filename, pdf_b64 = load_pdf(pdf_path)

#     while True:
#         try:
#             question = input("Question: ").strip()
#         except (EOFError, KeyboardInterrupt):
#             break

#         if not question:
#             continue
#         if question.lower() in {"exit", "quit"}:
#             break

#         print("\nAnswer:", ask(filename, pdf_b64, question), "\n")


# if __name__ == "__main__":
#     main()