# Run data

Each folder in `results/runs/` is one 75-minute run. Minutes 15 to 74 are scored (`scored_s` = [900, 4500]).

## summary.json

| Field | Meaning |
|---|---|
| `name`, `controller`, `trace` | Run name, controller and its settings, per-second trace replayed |
| `entries.<app>.requests`, `error_rate` | Requests sent in the scored hour and the share that failed |
| `entries.<app>.latency_ms` | Latency percentiles over the scored hour (ms) |
| `entries.<app>.slo_ms` | SLO: 3 times the no-queue P95 of the application |
| `entries.<app>.violation_rate.k2/k3/k5` | Share of scored minutes whose P95 exceeds 2, 3 or 5 times the no-queue P95 |
| `overall.violation_rate_k3` | Mean of the four applications' `violation_rate.k3`: the violation rate of the paper |
| `cost.mean_ready_total` | Mean ready copies of the scaled services: the copies of the paper |
| `cost.share_time_pending` | Share of scored time with at least one copy pending |
| `stability.scaling_actions_per_hour`, `reversals_per_hour` | Changes of a service's copy count, and changes in the opposite direction of its previous change |
| `energy.gpu_wh`, `host_pkg_total_wh` | GPU energy (sum of both GPUs) and host CPU package energy over the run (Wh) |
| `health` | Control-node stalls during the run; a stall of 2 s or more makes the run invalid |
| `decisions` | Decision count, decision time (mean, P95, max in s), late ticks, invalid replies, LLM calls |

## timeline.csv

One row per minute of the run, warm-up included. For each application: request rate (`<app>_rps`), error share, P95 of all requests (`<app>_p95_all_ms`, used for the violation rate) and of successful ones (`<app>_p95_ok_ms`). For each scaled service: ready copies (`ready_<service>`).

## decisions.jsonl

One record per 60-s decision:

| Field | Meaning |
|---|---|
| `k`, `t` | Decision index and time since the run start (s) |
| `observe_s`, `decide_s`, `apply_s`, `tick_late_s` | Time to observe, decide and apply, and lateness of the tick (s) |
| `obs` | What the controller observed: per application and per service |
| `want`, `applied` | Copies the controller asked for, and the services whose count changed, with the count set after the 1 to 12 limit |
| `controller.calls` | LLM controllers only: one entry per model call |

Each LLM call holds `prompt` (the user message; the system prompt is in `meta.json`), `reply`, `decision`, `seconds`, token counts, and in the reasoning-first mode `stages`, the reasoning the model wrote before its reply.

## Other files

| File | Content |
|---|---|
| `meta.json` | Controller, settings, seed, trace, and for the LLMs the model and system prompt |
| `fast.jsonl` | Every 5 s: load generator statistics, copies and pod states |
| `metrics.jsonl` | Every 15 s: CPU and memory per service |
| `events.jsonl` | Run events: preflight checks, resets, faults |
| `gpu.csv` | Every 1 s, per GPU: power, utilization, memory |
| `host_power.csv` | Every 5 s: CPU package power of the physical host and CPU use per VM |
| `loadgen.log`, `host_power.log` | Logs of the load generator and the power probe |
