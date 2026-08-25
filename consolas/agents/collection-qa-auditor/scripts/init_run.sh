#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${1:-agents/collection-qa-auditor/runs/latest}"
mkdir -p "$RUN_DIR"

touch "$RUN_DIR/stage1_functional.jsonl"
touch "$RUN_DIR/stage2_data.jsonl"
touch "$RUN_DIR/stage3_visual.jsonl"

echo "Run initialized at: $RUN_DIR"
echo "- $RUN_DIR/stage1_functional.jsonl"
echo "- $RUN_DIR/stage2_data.jsonl"
echo "- $RUN_DIR/stage3_visual.jsonl"
