#!/bin/sh
# Rebuild every table and figure of the paper from the committed run data, then list the outputs
# that differ from the committed ones (none expected). No cluster needed.
# Usage (from the repo root, after: python -m venv .venv && .venv/bin/pip install -r requirements.txt):
#   sh tools/reproduce.sh
set -e
PY=${PY:-.venv/bin/python}
$PY tools/analyze.py
$PY tools/new_service.py
$PY tools/faithfulness.py
$PY tools/history_order.py
$PY tools/slo_sensitivity.py
$PY paper/figures/make_results_figs.py
echo "Outputs that differ from the committed ones:"
git status --short --untracked-files=no results/analysis paper/figures
