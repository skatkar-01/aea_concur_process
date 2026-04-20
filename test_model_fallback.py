#!/usr/bin/env python3
"""
Test script to verify model fallback configuration and logging
"""

import os
import sys
import logging
from pathlib import Path

def test_environment_setup():
    """Test that environment variables are properly configured"""
    
    print("\n" + "="*80)
    print("MODEL FALLBACK SETUP VERIFICATION")
    print("="*80 + "\n")
    
    # Check Azure credentials
    print("1️⃣  AZURE CREDENTIALS CHECK")
    print("-" * 80)
    
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    
    if endpoint:
        print(f"✓ AZURE_OPENAI_ENDPOINT: {endpoint[:50]}...")
    else:
        print("❌ AZURE_OPENAI_ENDPOINT: NOT SET")
        return False
    
    if api_key:
        print(f"✓ AZURE_OPENAI_API_KEY: {api_key[:20]}...")
    else:
        print("❌ AZURE_OPENAI_API_KEY: NOT SET")
        return False
    
    # Check model configuration
    print("\n2️⃣  MODEL CONFIGURATION CHECK")
    print("-" * 80)
    
    models = []
    for i in range(10):
        if i == 0:
            model_env = "AZURE_OPENAI_MODEL"
            default_msg = " (default: gpt-5-mini)"
        else:
            model_env = f"AZURE_OPENAI_MODEL{i}"
            default_msg = ""
        
        model = os.getenv(model_env)
        if model:
            models.append(model)
            if i == 0:
                print(f"✓ PRIMARY MODEL ({model_env}): {model}")
            else:
                print(f"✓ FALLBACK MODEL{i} ({model_env}): {model}")
        elif i == 0:
            # Check default
            print(f"⚠️  PRIMARY MODEL ({model_env}): Using default")
        else:
            break
    
    if not models:
        models = ["gpt-5-mini"]  # default
    
    print(f"\n   Fallback sequence: {' → '.join(models)}")
    
    # Check logging
    print("\n3️⃣  LOGGING CONFIGURATION CHECK")
    print("-" * 80)
    
    log_file = Path("llm_api_errors.log")
    if log_file.exists():
        size = log_file.stat().st_size
        print(f"✓ Log file exists: {log_file.absolute()}")
        print(f"  Size: {size:,} bytes")
    else:
        print(f"ℹ️  Log file will be created on first run: {log_file.absolute()}")
    
    # Check debug folder
    print("\n4️⃣  DEBUG FOLDER CHECK")
    print("-" * 80)
    
    debug_folder = Path("llm_debug_responses")
    if debug_folder.exists():
        files = list(debug_folder.glob("response_*.txt"))
        print(f"✓ Debug folder exists: {debug_folder.absolute()}")
        print(f"  Responses captured: {len(files)}")
    else:
        print(f"ℹ️  Debug folder will be created on first run: {debug_folder.absolute()}")
    
    # Try to import and initialize
    print("\n5️⃣  FORMATTER INITIALIZATION TEST")
    print("-" * 80)
    
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from llm_formatter import LLMFormatter
        
        formatter = LLMFormatter()
        print(f"✓ LLMFormatter initialized successfully")
        print(f"  Primary model: {formatter.primary_model}")
        print(f"  Fallback sequence: {' → '.join(formatter.model_fallback)}")
        print(f"  API calls tracked: {formatter.api_call_count}")
        print(f"  API errors tracked: {formatter.api_error_count}")
        
    except Exception as e:
        print(f"❌ Failed to initialize LLMFormatter: {e}")
        return False
    
    # Summary
    print("\n" + "="*80)
    print("✅ SETUP VERIFICATION COMPLETE")
    print("="*80)
    print("\nNext steps:")
    print("1. Run your scrubbing process")
    print("2. Monitor: tail -f llm_api_errors.log")
    print("3. Check: ls -la llm_debug_responses/")
    print("4. Analyze: python scrubbing_process/analyze_llm_debug.py")
    print("\n")
    
    return True


if __name__ == "__main__":
    try:
        success = test_environment_setup()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
