#!/usr/bin/env python3
"""
Analyze LLM debug response files to identify error patterns
"""

import json
from pathlib import Path
from collections import defaultdict


def analyze_debug_folder(debug_folder="llm_debug_responses"):
    """Analyze all debug response files in the folder"""
    
    debug_path = Path(debug_folder)
    
    if not debug_path.exists():
        print(f"❌ Debug folder not found: {debug_path.absolute()}")
        print("   No debug responses have been captured yet.")
        print("   Run your scrubbing process with llm_formatter to generate them.")
        return
    
    debug_files = sorted(debug_path.glob("response_*.txt"))
    
    if not debug_files:
        print(f"❌ No debug response files found in: {debug_path.absolute()}")
        return
    
    total = len(debug_files)
    successful = 0
    parse_errors = 0
    other_errors = 0
    empty_responses = 0
    
    print(f"\n📊 Analyzing {total} debug response files...\n")
    print("=" * 80)
    
    parse_error_samples = []
    
    for file_path in debug_files:
        with open(file_path, 'r') as f:
            content = f.read()
        
        if "JSON parse error" in content:
            parse_errors += 1
            # Collect sample
            if len(parse_error_samples) < 3:
                lines = content.split('\n')
                parse_error_samples.append({
                    'file': file_path.name,
                    'content': content
                })
        elif "[EMPTY RESPONSE]" in content:
            empty_responses += 1
        elif "PARSING ERROR" in content:
            other_errors += 1
        elif "PARSED RESULT" in content:
            successful += 1
    
    # Summary
    print(f"✅ Successful parses:    {successful}/{total}")
    print(f"❌ JSON parse errors:    {parse_errors}/{total}")
    print(f"⚠️  Empty responses:      {empty_responses}/{total}")
    print(f"⚠️  Other errors:        {other_errors}/{total}")
    print("=" * 80)
    
    if parse_errors > 0:
        print(f"\n🔍 Analyzing {min(3, parse_errors)} parse error samples:\n")
        for sample in parse_error_samples:
            print(f"📄 File: {sample['file']}")
            print("-" * 80)
            
            # Extract sections
            lines = sample['content'].split('\n')
            
            in_transaction = False
            in_response = False
            in_error = False
            
            transaction_lines = []
            response_lines = []
            error_lines = []
            
            for line in lines:
                if "TRANSACTION INPUT" in line:
                    in_transaction = True
                    in_response = False
                    in_error = False
                    continue
                elif "RAW LLM RESPONSE" in line:
                    in_transaction = False
                    in_response = True
                    in_error = False
                    continue
                elif "PARSING ERROR" in line:
                    in_transaction = False
                    in_response = False
                    in_error = True
                    continue
                elif "PARSED RESULT" in line or "=" * 20 in line:
                    in_transaction = False
                    in_response = False
                    in_error = False
                    continue
                
                if in_transaction and line.strip():
                    transaction_lines.append(line)
                elif in_response and line.strip():
                    response_lines.append(line)
                elif in_error and line.strip():
                    error_lines.append(line)
            
            if transaction_lines:
                print("📋 Transaction:")
                for line in transaction_lines[:5]:
                    print(f"   {line}")
            
            if error_lines:
                print("\n❌ Error:")
                for line in error_lines[:3]:
                    print(f"   {line}")
            
            if response_lines:
                print("\n📨 Raw Response (first 300 chars):")
                raw_response = '\n'.join(response_lines)
                print(f"   {raw_response[:300]}")
                if len(raw_response) > 300:
                    print(f"   ... ({len(raw_response) - 300} chars omitted)")
            
            print()
    
    print("💡 Next Steps:")
    print("   1. Review the sample error details above")
    print("   2. Check if errors occur for specific vendors, amounts, or descriptions")
    print(f"   3. Look in: {debug_path.absolute()}")
    print("   4. Examine individual response_XXXX.txt files for more details")
    

if __name__ == "__main__":
    import sys
    
    debug_folder = sys.argv[1] if len(sys.argv) > 1 else "llm_debug_responses"
    
    analyze_debug_folder(debug_folder)
