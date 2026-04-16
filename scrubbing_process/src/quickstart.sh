#!/bin/bash

# Quick Start Script for AmEx Scrubber

echo "==================================================================="
echo "AmEx Expense Scrubber - Quick Start"
echo "==================================================================="
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version || { echo "Error: Python 3 not found"; exit 1; }
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  No .env file found!"
    echo "   Creating .env from template..."
    cp .env.template .env
    echo ""
    echo "✏️  Please edit .env and add your Azure OpenAI credentials:"
    echo "   AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/"
    echo "   AZURE_OPENAI_API_KEY=your-api-key-here"
    echo ""
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt --quiet
echo "✓ Dependencies installed"
echo ""

# Check for input file
if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: ./quickstart.sh <input-file.xlsx> <memory-folder>"
    echo ""
    echo "Example:"
    echo "  ./quickstart.sh 'Batch # 1 - \$119,802.46.xlsx' historical_transactions/"
    echo ""
    exit 1
fi

INPUT_FILE="$1"
MEMORY_FOLDER="$2"

# Check if input file exists
if [ ! -f "$INPUT_FILE" ]; then
    echo "❌ Error: Input file not found: $INPUT_FILE"
    exit 1
fi

# Run scrubber
echo "🚀 Starting AmEx Scrubber..."
echo "   Input: $INPUT_FILE"

echo "   Memory: $MEMORY_FOLDER"
python main.py --input "$INPUT_FILE" --memory-folder "$MEMORY_FOLDER"

echo ""
echo "✅ Processing complete!"
