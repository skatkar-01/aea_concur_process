from pathlib import Path
import pandas as pd
import logging

from scrubber.rules_engine import RulesEngine
from scrubber.llm_formatter import LLMFormatter
from scrubber.validator import Validator
from scrubber.cache import LLMCache
from scrubber.models import Row
from scrubber.reference_loader import ReferenceData
from scrubber.transaction_memory import TransactionMemory
from scrubber.checkpointing import Checkpoint
from scrubber.writer import ExcelWriter

logging.basicConfig(level=logging.INFO)


INPUT_FILE = "C:\\Users\\SKatkar\\OneDrive\\GPFS\\aea_concur_scrubbing\\scrubbing_process\\Batch # 1 - $119,802.46.xlsx"
MEMORY_FILE = "C:\\Users\\SKatkar\\OneDrive\\GPFS\\aea_concur_scrubbing\\final_concur_scrubbing\\outputs\\concur\\03-26\\Alers -$2,802.00.xlsx"


def load_rows(df):
    rows = []
    for i, r in enumerate(df.to_dict(orient="records")):
        rows.append(
            Row(
                idx=i,
                first_name=r.get("Employee First Name", ""),
                middle_name=r.get("Employee Middle Name", ""),
                last_name=r.get("Employee Last Name", ""),
                tran_dt=r.get("Report Entry Transaction Date"),
                description=r.get("Report Entry Description", ""),
                amount=float(r.get("Journal Amount", 0) or 0),
                expense_code=r.get("Report Entry Expense Type Name", ""),
                vendor_desc=r.get("Report Entry Vendor Description", ""),
                vendor_name=r.get("Report Entry Vendor Name", ""),
                project=r.get("Project"),
                cost_center=r.get("Cost Center", ""),
                report_purpose=r.get("Report Purpose", ""),
                employee_id=r.get("Employee ID", ""),
            )
        )
    return rows

def main():
    df = pd.read_excel(INPUT_FILE)
    raw = df.values.tolist()

    rows = load_rows(df)

    # components
    rules = RulesEngine()
    cache = LLMCache(Path("cache"))
    llm = LLMFormatter(cache)
    validator = Validator(rules)
    writer = ExcelWriter(rules)

    ref = ReferenceData(INPUT_FILE)

    memory = TransactionMemory()
    memory.load_file(Path(MEMORY_FILE))

    checkpoint = Checkpoint(Path("checkpoints"), Path(INPUT_FILE))
    cp = checkpoint.load()

    if cp:
        print("Resuming from checkpoint")
        rows = cp["rows"]

    # STEP 1: deterministic
    for r in rows:
        rules.apply(r, ref)

    checkpoint.save("deterministic", rows)

    # STEP 2: LLM
    llm.format_rows(rows)

    checkpoint.save("llm", rows)

    # STEP 3: validation
    validator.validate(rows)

    checkpoint.save("validate", rows)

    # STEP 4: output
    writer.write(
        Path("output.xlsx"),
        rows,
        raw,
        "Batch Output"
    )

    checkpoint.clear()
    print("Done")


if __name__ == "__main__":
    main()