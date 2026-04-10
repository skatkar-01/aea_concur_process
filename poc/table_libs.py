# from docling.document_converter import DocumentConverter

# converter = DocumentConverter()
# doc = converter.convert("../inputs/concur/Brown $875.18.pdf").document

# # All tables (handles overflow + unordered layouts)
# for table in doc.tables:
#     df = table.export_to_dataframe()
#     print(df)



import camelot

# lattice = bordered tables, stream = whitespace-based
tables = camelot.read_pdf(
    "../inputs/concur/Brown $875.18.pdf",
    flavor="stream",   # or "stream"
    pages="1,2,3"        # all pages (handles overflow)
)
print(f"Total tables extracted: {len(tables)}")
for table in tables:
    print(table.df)
    print(f"Accuracy: {table.accuracy}")
tables.export("extracted_tables.csv", f="csv")  
# Total tables extracted: 0


# import fitz  # pymupdf
# import pandas as pd

# doc = fitz.open("../inputs/concur/Brown $875.18.pdf")

# for page in doc:
#     for table in page.find_tables():
#         df = pd.DataFrame(table.extract())
#         print(df)
# #worst accuracy


# import pdfplumber
# import pandas as pd

# with pdfplumber.open("../inputs/concur/Brown $875.18.pdf") as pdf:
#     for page in pdf.pages:
#         for table in page.extract_tables():
#             df = pd.DataFrame(table[1:], columns=table[0])
#             print(df)
# #worst accuracy