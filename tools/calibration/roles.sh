#!/bin/bash
# Prints a sed script that replaces node names by roles: workers by their llm-autoscaler/slot
# label (w0..w3, tools/pin_fixed_pods.py), the control node by "control". Keeps names out of results.
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.labels.llm-autoscaler/slot}{"\n"}{end}' \
  | awk '{printf "s/\\<%s\\>/%s/g\n", $1, ($2 == "" ? "control" : $2)}'
