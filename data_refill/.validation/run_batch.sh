#!/usr/bin/env bash
# Run the validator on every binary in the given batch and write the report.
# Usage: ./run_batch.sh <batch_index 0-9>
set -euo pipefail
batch="${1:?batch index required}"
cd "$(dirname "$0")"
ids=$(/home/cliu57/anaconda3/bin/python3 -c "
import json
with open('batch_${batch}.json') as f:
    b = json.load(f)
print(' '.join(str(r['id']) for r in b))
")
exec /home/cliu57/anaconda3/bin/python3 validate_binary.py $ids > "report_${batch}.json" 2>&1
