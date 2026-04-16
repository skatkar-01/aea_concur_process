"""
config.py
─────────
Central configuration loader. Reads all YAML rule files once at import time
and exposes typed dataclass instances so the rest of the pipeline never
hardcodes rule constants.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


# ── Locate config directory ────────────────────────────────────────────────────
_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load(name: str) -> dict:
    path = _CONFIG_DIR / name
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ── Raw YAML dicts ─────────────────────────────────────────────────────────────
_DESC  = _load("description_rules.yaml")
_EXP   = _load("expense_rules.yaml")
_VND   = _load("vendor_rules.yaml")
_PROJ  = _load("project_rules.yaml")


# ══════════════════════════════════════════════════════════════════════════════
#  Column definitions (never changes)
# ══════════════════════════════════════════════════════════════════════════════
RAW_COLS = [
    "Employee First Name",
    "Employee Middle Name",
    "Employee Last Name",
    "Blank/Placeholder",
    "Report Entry Transaction Date",
    "Report Entry Description",
    "Journal Amount",
    "Report Entry Payment Type Name",
    "Report Entry Expense Type Name",
    "Report Entry Vendor Description",
    "Report Entry Vendor Name",
    "Project",
    "Cost Center",
    "Report Purpose",
    "Employee ID",
]

AMEX_ALL_COLS = RAW_COLS + [None, "LEN"]

ENTITY_COLS = [
    "Employee First Name",
    "Employee Middle Name",
    "Employee Last Name",
    "Blank/Placeholder",
    "Report Entry Transaction Date",
    "Report Entry Description",
    "Journal Amount",
    "Report Entry Payment Type Name",
    "Report Entry Expense Type Name",
    "Report Entry Vendor Name",
    "Project",
    "Cost Center",
    "Report Purpose",
    "Employee ID",
    None,   # Comments — human fills in; scrubber writes only when flagged
    "LEN",
]


# ══════════════════════════════════════════════════════════════════════════════
#  Description rules
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class DescRule:
    id: str
    find: str
    replace: str
    scope: str
    priority: int
    note: str = ""


@dataclass
class Abbreviation:
    long: str
    short: str
    priority: int = 0


@dataclass
class PrefixAbbrev:
    find: str
    replace: str


@dataclass
class EntityTabRule:
    find: str
    replace: str


@dataclass
class DescriptionConfig:
    rules: List[DescRule]
    abbreviations: List[Abbreviation]
    prefix_abbreviations: List[PrefixAbbrev]
    entity_tab_rules: List[EntityTabRule]


def _build_desc_config() -> DescriptionConfig:
    rules = [
        DescRule(**r)
        for r in sorted(_DESC.get("rules", []), key=lambda x: x.get("priority", 99))
    ]
    abbrevs = [
        Abbreviation(**a)
        for a in sorted(_DESC.get("abbreviations", []), key=lambda x: x.get("priority", 99))
    ]
    prefix = [PrefixAbbrev(**p) for p in _DESC.get("prefix_abbreviations", [])]
    entity = [EntityTabRule(**e) for e in _DESC.get("entity_tab_rules", [])]
    return DescriptionConfig(rules=rules, abbreviations=abbrevs,
                              prefix_abbreviations=prefix, entity_tab_rules=entity)


# ══════════════════════════════════════════════════════════════════════════════
#  Expense rules
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class DescOverride:
    keywords: List[str]
    expense_code: str
    priority: int = 0
    exclude_keywords: List[str] = field(default_factory=list)
    context_keywords: List[str] = field(default_factory=list)


@dataclass
class GlOverride:
    gl: str
    comment: str
    trigger_projects: List[str] = field(default_factory=list)
    trigger_expense: Optional[str] = None
    trigger_keywords: List[str] = field(default_factory=list)


@dataclass
class ExpenseConfig:
    remap: Dict[str, str]
    disallowed: List[str]
    description_overrides: List[DescOverride]
    gl_overrides: List[GlOverride]
    meal_limits: Dict[str, float]
    personal_project: str


def _build_expense_config() -> ExpenseConfig:
    overrides = [
        DescOverride(**{
            "keywords": o["keywords"],
            "expense_code": o["expense_code"],
            "priority": o.get("priority", 0),
            "exclude_keywords": o.get("exclude_keywords", []),
            "context_keywords": o.get("context_keywords", []),
        })
        for o in sorted(_EXP.get("description_overrides", []), key=lambda x: x.get("priority", 99))
    ]
    gl = [
        GlOverride(**{
            "gl": g["gl"],
            "comment": g["comment"],
            "trigger_projects": g.get("trigger_projects", []),
            "trigger_expense": g.get("trigger_expense"),
            "trigger_keywords": g.get("trigger_keywords", []),
        })
        for g in _EXP.get("gl_overrides", [])
    ]
    return ExpenseConfig(
        remap=_EXP.get("remap", {}),
        disallowed=_EXP.get("disallowed", []),
        description_overrides=overrides,
        gl_overrides=gl,
        meal_limits=_EXP.get("meal_limits", {}),
        personal_project=str(_EXP.get("personal_project", "1008")),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Vendor rules
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class VendorConfig:
    title_fixes: Dict[str, str]
    abbreviations: Dict[str, str]
    keyword_map: Dict[str, str]   # lowercase key → canonical name
    project_overrides: Dict[str, dict]


def _build_vendor_config() -> VendorConfig:
    return VendorConfig(
        title_fixes=_VND.get("title_fixes", {}),
        abbreviations=_VND.get("abbreviations", {}),
        keyword_map={k.lower(): v for k, v in _VND.get("keyword_map", {}).items()},
        project_overrides=_VND.get("project_overrides", {}),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Project rules
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class CarPolicy:
    early_arrival_cutoff_hour: int
    early_arrival_cutoff_minute: int
    work_late_cutoff_hour: int
    work_late_cutoff_minute: int
    weekend_days: List[str]


@dataclass
class ProjectConfig:
    project_dept_rules: Dict[str, List[str]]
    project_meta: Dict[str, dict]
    car_policy: CarPolicy
    car_formats: Dict[str, str]
    entity_tabs: Dict[str, str]
    entity_default: str
    pay_type_find: str
    pay_type_replace: str
    len_overhead: int
    len_limit: int


def _build_project_config() -> ProjectConfig:
    pdr = {}
    for k, v in _PROJ.get("project_dept_rules", {}).items():
        pdr[str(k)] = v if isinstance(v, list) else [v]
    cp_raw = _PROJ.get("car_policy", {})
    car_policy = CarPolicy(
        early_arrival_cutoff_hour=cp_raw.get("early_arrival_cutoff_hour", 7),
        early_arrival_cutoff_minute=cp_raw.get("early_arrival_cutoff_minute", 0),
        work_late_cutoff_hour=cp_raw.get("work_late_cutoff_hour", 19),
        work_late_cutoff_minute=cp_raw.get("work_late_cutoff_minute", 30),
        weekend_days=cp_raw.get("weekend_days", ["Saturday", "Sunday"]),
    )
    pt = _PROJ.get("pay_type", {})
    return ProjectConfig(
        project_dept_rules=pdr,
        project_meta={str(k): v for k, v in _PROJ.get("project_meta", {}).items()},
        car_policy=car_policy,
        car_formats=_PROJ.get("car_formats", {}),
        entity_tabs=_PROJ.get("entity_tabs", {}),
        entity_default=_PROJ.get("entity_default", "AEA"),
        pay_type_find=pt.get("find", "American Express Corporate Card CBCP"),
        pay_type_replace=pt.get("replace", "American Express"),
        len_overhead=_PROJ.get("len_overhead", 12),
        len_limit=_PROJ.get("len_limit", 70),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Singleton config instances — import these everywhere
# ══════════════════════════════════════════════════════════════════════════════
DESC_CFG    = _build_desc_config()
EXPENSE_CFG = _build_expense_config()
VENDOR_CFG  = _build_vendor_config()
PROJECT_CFG = _build_project_config()


# ══════════════════════════════════════════════════════════════════════════════
#  LLM / Azure settings (read from env or .env)
# ══════════════════════════════════════════════════════════════════════════════
AZURE_API_KEY  = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "https://api.openai.com/v1")
AZURE_MODEL    = os.environ.get("AZURE_OPENAI_MODEL", "gpt-4o-mini")
LLM_APPLY_THRESHOLD = float(os.environ.get("LLM_APPLY_THRESHOLD", "0.80"))
LLM_BATCH_SIZE      = int(os.environ.get("LLM_BATCH_SIZE", "10"))


# ══════════════════════════════════════════════════════════════════════════════
#  LLM system prompt
# ══════════════════════════════════════════════════════════════════════════════
LLM_SYSTEM_PROMPT = """You are an expert AmEx expense data scrubber for AEA Investors LP.
Review each expense row and propose policy-compliant scrubbing updates.
Written rules are the source of truth.

WHAT THE SCRUBBER ALREADY DOES (don't re-flag these):
- Pay type: 'American Express Corporate Card CBCP' → 'American Express' ✓
- Expense: 'Miscellaneous' → 'Other' ✓
- Description: 'Meeting' → 'Mtg', 'Meetings' → 'Mtgs', 'Ticketing Fee' → 'Tkt Fee' ✓
- Vendor names: cleaned from Vendor List ✓

WHAT TO FLAG (needs human review):
- Description missing business purpose or deal name
- Inflight Wifi with wrong expense code (should be Info Services)
- Ticketing fees coded as Other Travel (should be Airline)
- Hotel fees not coded as Lodging
- Project 1008 (Personal) without Employee ID
- Negative amounts/refunds that may not mirror original charge
- LEN(description+vendor)+12 > 70
- Car service without clear To/From destination
- Trip rows with inconsistent project codes across airline/lodging/car/meals
- Prepaid/event rows for 1035/1003/1012 needing G/L 14000 at posting
- Intern-event rows under 1013 needing G/L 58230 note
- Early Arrival / Work Late / Weekend car service rows needing receipt-time verification

DESCRIPTION FORMAT:
- Flights: RT:JFK-STO/Fundraising/Growth Fund
- Flight fees: Tkt Fee/RT:LGA-DTW/Deal Name
- Travel meals: Travel Meal/BOD Mtg/Company
- Business meals: Bus.Lunch/4 ppl/SMBC, Golub or Bus.Dinner w/D.Ryan/MidOcean
- Office meals: Working Lunch | Working Lunch/Overage/Personal
- Car service: JFK-Hotel/BOD Mtg/Company or Work Late/Office-Home
- Lodging: Lodging/BOD Mtg/AmeriVet
- Info Services: Inflight Wifi or Research Subscription/Purpose/Company
- Other Travel: Bus.Parking/BOD Mtg/Company | Train/PHL-Penn/Purpose/Company

Return ONLY valid JSON matching the schema provided."""

LLM_ROW_SCHEMA = {
    "description_ok":     "bool",
    "description_fixed":  "string | null",
    "vendor_ok":          "bool",
    "vendor_fixed":       "string | null",
    "expense_code_ok":    "bool",
    "expense_code_fixed": "string | null",
    "project_fixed":      "string | null",
    "cost_center_fixed":  "string | null",
    "rule_ids_applied":   "list[string]",
    "confidence":         "float 0-1",
    "conflict_note":      "string | null",
    "len_ok":             "bool",
    "len_value":          "int",
    "flag":               "bool",
    "flag_reason":        "string | null",
    "comments":           "string | null",
}
