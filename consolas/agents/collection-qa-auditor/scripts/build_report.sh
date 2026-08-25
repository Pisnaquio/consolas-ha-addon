#!/usr/bin/env bash
set -euo pipefail

INPUT_DIR="${1:-agents/collection-qa-auditor/runs/latest}"
OUTPUT_FILE="${2:-QA_REPORT.md}"

python3 agents/collection-qa-auditor/scripts/consolidate_findings.py \
  --input-dir "$INPUT_DIR" \
  --output "$OUTPUT_FILE"
