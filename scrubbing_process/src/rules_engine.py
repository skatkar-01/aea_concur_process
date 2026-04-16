"""
rules_engine.py - Deterministic Rules Engine with YAML Configuration
"""

import re
import yaml
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class RulesEngine:
    """Load and apply rules from YAML configuration files"""
    
    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)
        print(f"Loading rules from {config_dir}...")
        
        # Load all rule configurations
        self.description_rules = self._load_yaml('description_rules.yaml')
        self.expense_rules = self._load_yaml('expense_rules.yaml')
        self.vendor_rules = self._load_yaml('vendor_rules.yaml')
        self.policy_rules = self._load_yaml('policy_rules.yaml')
        
        print(f"✓ Loaded rules from {config_dir}")
    
    def _load_yaml(self, filename: str) -> Dict:
        """Load YAML configuration file"""
        filepath = self.config_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Config file not found: {filepath}")
        
        with open(filepath, 'r') as f:
            return yaml.safe_load(f)
    
    def scrub_description(self, desc: str, expense_code: str) -> Tuple[str, bool]:
        """
        Apply description formatting rules
        Returns: (scrubbed_description, changed)
        """
        original = str(desc or "")
        result = original
        expense_lower = expense_code.lower()
        
        # Apply find-replace rules (ordered by priority)
        rules = sorted(
            self.description_rules.get('rules', []),
            key=lambda r: r.get('priority', 999)
        )
        
        for rule in rules:
            if not rule.get('enabled', True):
                continue
            
            scope = rule.get('scope', 'all')
            if not self._matches_scope(scope, expense_lower, result.lower()):
                continue
            
            find = rule.get('find', '')
            replace = rule.get('replace', '')
            result = result.replace(find, replace)
        
        # Apply word abbreviations (whole word only)
        for abbr in self.description_rules.get('abbreviations', []):
            long_form = abbr.get('long', '')
            short_form = abbr.get('short', '')
            # Use word boundary regex
            result = re.sub(
                r'\b' + re.escape(long_form) + r'\b',
                short_form,
                result,
                flags=re.IGNORECASE
            )
        
        # Apply prefix abbreviations
        for prefix in self.description_rules.get('prefix_abbreviations', []):
            long_form = prefix.get('long', '')
            short_form = prefix.get('short', '')
            result = result.replace(long_form, short_form)
        
        # Clean up car service spacing
        if 'car service' in expense_lower:
            result = re.sub(r'\s*-\s*', '-', result)  # Remove spaces around dashes
            result = re.sub(r'\s*/\s*', '/', result)  # Remove spaces around slashes
        
        # Clean up extra spaces
        result = re.sub(r'  +', ' ', result).strip()
        
        return result, result != original
    
    def _matches_scope(self, scope: str, expense_lower: str, desc_lower: str) -> bool:
        """Check if rule scope matches current transaction"""
        scope_checks = {
            "all": True,
            "info_services": bool(re.search(r'\binfo\s*service', expense_lower)),
            "personal": bool(re.search(r'\bpersonal\b', desc_lower)),
            "airline": bool(re.search(r'\bairline\b', expense_lower)),
            "car_service": bool(re.search(r'\bcar\s*service\b', expense_lower)),
            "lodging": bool(re.search(r'\blodging\b', expense_lower)),
        }
        return scope_checks.get(scope, True)
    
    def scrub_pay_type(self, pay_type: str) -> Tuple[str, bool]:
        """Clean up payment type"""
        original = str(pay_type or "")
        find = self.vendor_rules['pay_type']['find']
        replace = self.vendor_rules['pay_type']['replace']
        result = original.replace(find, replace)
        return result, result != original
    
    def scrub_expense_code(self, code: str, description: str) -> Tuple[str, bool]:
        """
        Remap expense codes based on rules and description patterns
        Returns: (corrected_code, changed)
        """
        original = str(code or "").strip()
        desc_lower = description.lower()
        
        # Check smart detection patterns first
        for detection in self.expense_rules.get('smart_detection', []):
            pattern = detection.get('pattern', '')
            if re.search(pattern, desc_lower):
                # Check exclude patterns
                exclude = detection.get('exclude_patterns', '')
                if exclude and re.search(exclude, desc_lower):
                    continue
                
                # Check include patterns
                include = detection.get('include_patterns', '')
                if include and not re.search(include, desc_lower):
                    continue
                
                # Pattern matches
                correct_code = detection.get('correct_code', '')
                if correct_code and correct_code != original:
                    return correct_code, True
        
        # Apply simple remapping
        remap = self.expense_rules.get('expense_code_remap', {})
        if original in remap:
            return remap[original], True
        
        return original, False
    
    def normalize_vendor(self, vendor_desc: str, vendor_list: Dict = None) -> str:
        """
        Normalize vendor name using rules and optional vendor list
        """
        original = str(vendor_desc or "").strip()
        
        # 1. Check vendor list exact match (if provided)
        if vendor_list and original.upper() in vendor_list:
            return vendor_list[original.upper()]
        
        # 2. Check keyword mapping (case-insensitive substring)
        keyword_map = self.vendor_rules.get('keyword_mapping', {})
        for keyword, canonical in keyword_map.items():
            if keyword.lower() in original.lower():
                return canonical
        
        # 3. Apply title() casing
        result = original.title()
        
        # 4. Apply title fixes
        title_fixes = self.vendor_rules.get('title_fixes', {})
        if result in title_fixes:
            result = title_fixes[result]
        
        # 5. Apply abbreviations
        abbreviations = self.vendor_rules.get('abbreviations', {})
        if result in abbreviations:
            result = abbreviations[result]
        
        return result
    
    def compute_length(self, description: str, vendor: str) -> int:
        """Compute combined character length"""
        overhead = self.policy_rules['length_limits']['overhead']
        return len(str(description or "")) + len(str(vendor or "")) + overhead
    
    def validate_transaction(self, txn: Dict) -> List[str]:
        """
        Run policy validation checks on a transaction
        Returns: List of flag messages
        """
        flags = []
        
        desc = str(txn.get('description', '')).lower()
        expense_code = str(txn.get('expense_code', ''))
        project = str(txn.get('project', '')).strip()
        cost_center = str(txn.get('cost_center', '')).strip()
        amount = float(txn.get('amount', 0))
        employee_id = str(txn.get('employee_id', '')).strip()
        
        # Check disallowed expense codes
        disallowed = self.expense_rules.get('disallowed_codes', [])
        if expense_code in disallowed:
            flags.append(f"Disallowed expense code: {expense_code}")
        
        # Check inflight wifi expense code
        if 'inflight wifi' in desc and expense_code.lower() != 'info services':
            flags.append(f"Inflight Wifi should use 'Info Services', not '{expense_code}'")
        
        # Check ticketing fees
        if ('tkt fee' in desc or 'ticketing fee' in desc) and expense_code.lower() == 'other travel':
            flags.append("Ticketing fees should use 'Airline' expense code")
        
        # Check project-department alignment
        proj_dept_rules = self.policy_rules.get('project_dept_rules', {})
        if project in proj_dept_rules:
            required = proj_dept_rules[project]
            if isinstance(required, list):
                if cost_center not in required:
                    flags.append(f"Project {project} requires dept {required}, got '{cost_center}'")
            else:
                if cost_center != required:
                    flags.append(f"Project {project} requires dept '{required}', got '{cost_center}'")
        
        # Check project metadata requirements
        proj_meta = self.policy_rules.get('project_metadata', {}).get(project, {})
        if proj_meta.get('requires_emp_id') and not employee_id:
            flags.append(f"Project {project} (Personal) requires Employee ID")
        
        if proj_meta.get('board_only'):
            if 'bod' not in desc and 'board' not in desc:
                flags.append(f"Project {project} should only be used for board meetings")
        
        # Check G/L overrides
        gl_overrides = self.policy_rules.get('gl_overrides', {})
        
        # Prepaid projects
        prepaid_projects = gl_overrides.get('14000', {}).get('trigger_projects', [])
        if project in prepaid_projects:
            flags.append("Prepaid project - review G/L 14000 at posting")
        
        # Corporate events
        corp_event_trigger = gl_overrides.get('58120', {})
        if (expense_code == corp_event_trigger.get('trigger_expense') and
            project in corp_event_trigger.get('trigger_projects', [])):
            flags.append("Corporate event - review G/L 58120 at posting")
        
        # Intern events
        intern_projects = gl_overrides.get('58230', {}).get('trigger_projects', [])
        if project in intern_projects:
            flags.append("Intern event - review G/L 58230 at posting")
        
        # Holiday party
        if 'holiday party' in desc and expense_code.lower() == 'car service':
            flags.append("Holiday Party car - review G/L 58140")
        
        # Meal limits
        meal_limits = self.policy_rules.get('meal_limits', {})
        for meal_type, limit in meal_limits.items():
            if meal_type.lower() in desc and amount > limit:
                flags.append(f"{meal_type} ${amount:.2f} exceeds ${limit:.2f} limit")
        
        # Car service policy (needs receipt verification)
        if 'car service' in expense_code.lower():
            if 'early arrival' in desc:
                flags.append("Early arrival car - verify receipt timestamp ≤ 7:00am")
            elif 'work late' in desc:
                flags.append("Work late car - verify receipt timestamp ≥ 7:30pm")
        
        # Character length check
        desc_str = txn.get('description', '')
        vendor_str = txn.get('vendor', '')
        total_len = self.compute_length(desc_str, vendor_str)
        limit = self.policy_rules['length_limits']['description_vendor_combined']
        
        if total_len > limit:
            flags.append(f"Character length {total_len} exceeds {limit} limit")
        
        return flags
