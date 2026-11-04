# Analysis plan

Draft 2026-10-06; fixed 2026-10-08 (docs/decisions.md), before the first test-matrix run. Anything
decided after that is reported as exploratory.

## Units and design

- **Run**: one controller on one held-out test day (75 min: 15 warm-up, 60 scored).
- **Controllers (12)**: HPA at 70% (the Kubernetes default, also the tuned pick); KEDA and the
  predictive HPA at their tuned settings; AWARE and FIRM (fine-tuned, median training seed); our
  agent (picked variant, median seed, pretrained or from scratch as the gate decides); TelecomGPT-R1
  and Qwen3.5 (27B, FP8), each in three modes: joint, per-service and ORACL-style. If the clean-run
  re-pick moves HPA's target off 70%, the tuned HPA is added as a 13th.
- **Test matrix**: every controller on each of the 5 test days, 2 repeats (10 runs per controller).
  A block is one (test day, repeat); the repeat sets the load generator seed (1 or 2), the same for
  every controller in the block. Order: random within each block, fixed by seed and written before
  the first run with the picks (`results/test_order_v2.json`, `results/test_picks_v2.json`;
  `harness/chains/test_matrix_v2.sh`).
- **LLM GPU layout**: both models served at once, one per GPU. Each test day has one block in layout
  A (TelecomGPT on the GPU on the control VM's socket) and one in B (swapped), so each model runs half
  its runs on each GPU and the layout cancels within every day. Repeat 1 is in A on even days and in
  B on odd days; blocks are ordered A B | B A | A B | B A | A B (5 switches, between runs).
- **Drift reference**: the fixed-copies reference run (15 min, evenly spaced arrivals, as the
  interference check) at the start of each block. A reference P95 more than 10% from the first one
  flags the block; flagged blocks are reported, not dropped.

## KPIs (per run, scored window)

Co-primary (2026-10-08: at the nominal load nearly every controller meets the SLOs, so a controller
is judged on what its SLO level costs):
1. **SLO violation rate**, share of 60 s intervals whose P95 (errors count as slower than any
   answer) is over k x the no-queue P95, k = 3, averaged over the 4 applications.
2. **Cost**, mean ready copies of the scaled services over the scored hour (copy-seconds / 3600).

Secondary, in this order: stability (scaling actions and
reversals per hour); SLO attainment (share of requests within the SLO); fairness (Jain index of
per-application attainment); decision time (mean, P95, late ticks); invalid decisions (LLMs);
energy (each GPU, their sum, host packages).

Sensitivity, no new runs: k = 2 and k = 5.

## Comparisons

1. Every controller against **HPA at its default target (70%)**, the reference.
2. The LLM controllers against each other: TelecomGPT against Qwen3.5 in each mode (joint,
   per-service, ORACL-style), the telecom model against its own base at equal size and serving;
   and the modes within each model.
3. Our agent against the literature RL agents (AWARE, FIRM).

## Statistics

- **Effect**: mean paired difference to the reference (same test day and repeat), per KPI.
- **Uncertainty**: 95% confidence interval by a two-level bootstrap (resample test days, then
  repeats within each day; 10,000 draws).
- **Test**: Friedman test over all controllers with the 10 (day, repeat) pairs as blocks; then
  Wilcoxon signed-rank tests of each comparison on the 10 pairs, Holm-corrected within each of
  the three comparison families, separately for each co-primary KPI (both are reported for every
  comparison; neither is dropped for its result).
- **Wording**: a difference is called a difference only when its interval excludes 0 and the Holm-
  corrected test passes; LLM results are reported as a characterization (what they do, at what cost),
  not as a ranking claim beyond what the intervals support.

## Robustness checks (reported alongside)

- **LLM interference**: fixed copies at 50% load, both LLMs idle against both answering back to
  back, 3 pairs (`harness/chains/interference_check.sh`); difference in per-application P95 with
  its interval.
- **Higher load** (extended 2026-10-07: at the nominal load nearly every controller meets the SLOs, so SLO
  protection is compared here): every rate x 1.25 (`run.py --scale 1.25`), all 5 test days, 1 repeat, for
  HPA (reference), both tuned baselines (KEDA and the predictive HPA; 2026-10-12, before these runs: HPA's
  tuned pick is its default, so both are kept rather than one chosen), AWARE, FIRM, our agent, TelecomGPT
  joint and Qwen3.5 joint (40 runs).
- **Prompt wording**: one alternative system prompt (`SYSTEM_ALT` in `harness/controllers/llm.py`: the
  same facts and numbers, reworded and reordered), both LLMs, joint mode, 5 test days, 1 repeat, each run
  paired with the default-prompt run of the main matrix's repeat 1 (same day, seed and GPU layout).
- **Prompt ablation** (2026-10-13, before these runs): the strategies of our earlier
  LLM-autoscaling papers, on joint mode, both LLMs, 5 test days, 1 repeat: zero-shot (the task, replica
  range and goal only, current interval only), + history (the current and previous 5 intervals), + domain
  (the cluster, budget and start-up facts, current interval only), and + CoT (the full prompt, step-by-step
  reasoning first, at most 700 tokens, then the counts). The full prompt (history and domain) is the main
  matrix's joint run of repeat 1, paired as above. Full thinking mode is not run: Qwen3.5 needs about
  215 s per decision against a 60 s interval (`results/llm_timing/`).
- **Statistics of the robustness checks**: with 5 pairs a Wilcoxon signed-rank test cannot reach p < 0.05
  (smallest two-sided p = 0.0625), so these checks report the mean paired difference with its bootstrap
  interval (resampling test days) and no test; they are read as effect sizes, not significance.
- All run after the main matrix in 5 day blocks, each with its drift reference
  (`harness/chains/after_matrix.sh`, `results/test_order_after.json`).
- **Training seeds**: each RL agent trained with 3 seeds in the simulator; all seeds' simulator
  results reported; the cluster gets the median seed (`rl/eval_lit.py`, `rl/eval_ours.py`).
- **Simulator validity**: simulated against measured violation rate and copies for the tuning runs.

## Exclusions

A run is rerun (not dropped silently) only for a harness failure: load generator error, node not
Ready, a missing log, or a control-node stall. Stalls: the recorder samples the cluster every 5 s
from the control node; normal jitter is under 0.01 s (99.9% of 89,101 samples), so any gap of 2 s or
more anywhere in the run (warm-up included) flags it (`health.harness_failure` in summary.json).
Cause seen on 2026-10-06: the whole control VM paused for about 5 s, the API stalled and the load
generator with it. Every rerun and its reason goes to `results/invalid_runs.json`. Runs are never
dropped for their results.
