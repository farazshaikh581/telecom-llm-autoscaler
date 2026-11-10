# Design decisions

Newest first. Each entry says what was decided and why.

## 2026-11-09: checks asked by an internal review; drift from 2026-10-30

**Drift from 2026-10-30.** The drift-reference rule of `docs/analysis_plan.md` (a reference P95 more than 10% from the first flags the block) was never applied in the analysis. Applied now to all 36 references, it flags 29, mostly on TeaStore, whose reference P95 ranged from 56 to 87 ms before 2026-10-30: the rule is too tight for a 15-min reference. One shift is large. In the five new-service blocks that ran after 2026-10-30 (test days 2 r2, 3 and 4), the reference P95 was 60-132 ms for hotel (20-26 ms in every earlier reference) and 88-242 ms for TeaStore; the functions changed little. Two runs of block 2 r1 (predictive HPA, TelecomGPT joint) also ran after that date. The cause is not known: the hypervisor pinning and the placement of the fixed pods match the plan today, and the control node already ran its present kernel before 2026-10-30. All other tests ran before 2026-10-30. As the plan says, the flagged blocks are reported, not dropped; the new-service results are also given for the four blocks before 2026-10-30 (0 r1/r2, 1 r1/r2). The KEDA loss on the hotel application comes mostly from the drifted blocks (+22.3 points against +5.6 before 2026-10-30).

**Checks without new runs.** (1) Equivalence bound of the main comparison: the lowest interval bound of any controller against HPA in violation rate is -2.0 points (TelecomGPT reasoning-first). (2) Actions and reversals recounted on the 60-s grid of the decisions (desired copies at each tick) for all controllers: at most 9% lower than the 5-s count, except the predictive HPA (53 to 35 actions per hour). (3) Wanted against ready copies under +25% load: Qwen3.5-27B asked for more than the 18-copy budget in 55% of minutes (22% at nominal load), TelecomGPT-R1 in 29% (2%). (4) Chance agreement of the stated and taken directions, from their marginal frequencies: 38% to 61% (Cohen kappa 0.21 to 0.50); in the history-order runs 80% and 96% (kappa 0.52 and 0.50). (5) RL training budgets equal the published ones; FIRM reward flat over the last 40% of training, AWARE still rising. AWARE (seed 2, the pick) retrained in the simulator for 2 and 3 times the budget (same seed, so the first 360 runs repeat), then evaluated on the development days as in `rl/eval_lit.py`. (6) Thinking mode on: the first 100 replay pairs of each model, original layout, max 6,000 tokens; stale share of the thinking text and of the answer. (7) ORACL does not name its base model (arXiv 2602.05292); the paper text naming Qwen3-32B is corrected.

**New runs, fixed before any of them.** (a) Labels only: order=labeled (oldest first, each interval labeled with its age), both models, joint and reasoning-first, 5 test days, paired with the main-matrix run in layout A as the history-order test (20 runs). (b) Headroom: HPA and both joint LLMs at 0.8 of the nominal load (15 runs); the main-matrix run at nominal load is then the +25% step, with copies left in the budget. One block per test day, drift reference first; a block stops if the reference P95 is above 26 ms for hotel or 90 ms for TeaStore. Random order with seed 2031. Measures as in the history-order test and the +25% load test; five pairs each, effects with intervals.

**Blind rating.** Planned with two human raters, blind to the verdicts of the script, on a fresh sample of 80 reasoning-first calls: 30 random calls per model with a number, and 10 per model among calls with an SLO-breach claim. Items: every number with a unit (404) and every sentence that names an SLO (133). Reported: agreement and Cohen kappa between the raters and with the script, and precision and recall of the stale rule.

**Rating done by two LLMs (2026-11-10), not by humans.** No human rater was available before submission. Two LLMs from different vendors labeled the sheet, each blind to the verdicts of the script and to the other: Claude Opus 5.5 (2026-11-09; it also helped write the rules) and Gemini 3.6 Flash (thinking level high, web app, 2026-11-10). Labels in `results/rating/claude_*.csv` and `results/rating/gemini_*.csv`; the sample is drawn with seed 2032. Result: the two agree on 86.8% of the numbers (kappa 0.76; 0.85 for stale or not) and 95.5% of the SLO sentences (kappa 0.92). Stale rule against either: precision 90%, recall 36% (Claude) and 37% (Gemini); every SLO verdict of the script matches both. The stale shares are lower bounds.

**Results of the checks.** (5) AWARE seed 2 in the simulator on the development days (3 days x 3 seeds; `results/analysis/aware_budget.csv`): violation rate 4.3% at the published budget, 2.6% at 2 times and 1.2% at 3 times, at 10.1, 10.5 and 16.7 mean copies. The cluster runs keep the published budget. (6) Thinking mode (`results/analysis/thinking_check.csv`): mean 18.0 s (Qwen3.5-27B) and 14.3 s (TelecomGPT-R1) per call, at most 40 s; stale share of the present-tense numbers in the thinking 21.0% and 40.3%, not lower than in the answers.

## 2026-11-06: prompt-layout replay (order and labels separated), more models

**Why.** The history-order test changed two things at once: the order of the intervals (newest first) and an age label on each row ("now", "1 min ago"). It cannot say which of the two removed the stale readings. Both LLMs are also one family (TelecomGPT-R1 is a fine-tune of Qwen3.5-27B). This design is fixed after the main results and before any replay call.

**Design.** Offline, no cluster. Calls of the reasoning-first mode in the main matrix (test2_{tgpt,qwen}_oracl, 5 days x 2 repeats, scored minutes 15-74): 500 (decision, application) pairs per model, 50 per run, drawn with seed 2026. Each observation is rebuilt into the reasoning-first prompt in four layouts: oldest first without labels (the main matrix), oldest first with labels, newest first without labels, newest first with labels (the history-order test). Nothing else in the prompt changes. Each prompt goes through the same two calls as in the runs (tagged reasoning, then the counts; temperature 0, thinking off, same token limits), and the reasoning through the same checks (`tools/faithfulness.py`).
Models: TelecomGPT-R1 and Qwen3.5-27B as in the runs; Gemma 4 26B-A4B (RedHatAI FP8-dynamic, vLLM 0.29.0, reasoning parser gemma4, thinking off) as a model from another family, on every second pair of both samples (500), reported as characterization; Mistral Small 3.2 24B Instruct (2506; FP8 build RedHatAI/Mistral-Small-3.2-24B-Instruct-2506-FP8, vLLM 0.29.0 in Mistral tokenizer mode; this mode takes no chat-template arguments, so its requests omit the thinking switch, which the model does not have) as a fourth model from another family, on the same 500 pairs as Gemma, reported as characterization. Both other models are third-party FP8 builds, like TelecomGPT-R1.

**Measures.** Primary: the stale share and the latest share of the present-tense numbers. Secondary: wrong SLO-exceeded claims; how often the counts differ from the oldest-first layout without labels. Per model and layout, pooled shares with a 95% bootstrap interval that resamples runs (10,000 draws). Order effect: newest minus oldest, averaged over the two label levels; label effect: with minus without, averaged over the two orders; and their interaction. Effects with intervals only; this is a mechanism check, not a test of the controllers.

**Result (2026-11-09).** 2,000 prompts per model (500 pairs in four layouts), no failed call (`results/analysis/layout_replay/`, summary in `results/analysis/layout_replay.csv`). In the original layout, 11.8% to 32.6% of the present-tense numbers are stale in all four models; with labels alone, or with the newest row first alone, 2.1% or less. The latest share rises from 22-45% to 46-81%. The paper reports the stale share per model and layout with its interval. Not reported: wrong SLO-exceeded claims (too few claims, intervals too wide) and changed counts. At temperature 0, the replay in the original layout repeated the logged counts in only 397 of 500 calls for Qwen3.5-27B and 459 of 500 for TelecomGPT-R1, which is close to the size of the layout effect on the counts.

## 2026-11-02: faithfulness checks fixed after a sample check
**Why.** The 30 sampled calls (faithfulness_sample.csv) were read next to the verdicts of the script with an LLM assistant. Number grounding and stale readings agreed (no false stale flags; a few stale values missed, so stale rates are a lower bound). Three checks did not: SLO claims in four-service calls were judged against all services (6 of 6 "wrong within-SLO" flags were correct claims); root causes counted negated or past mentions ("not CPU or memory", "the previous surge") in 6 of 18 calls; the stated direction came from negated or hypothetical phrases ("no need to scale up"), and 5 of 7 verdicts were wrong.
**Change.** (1) An SLO claim is judged on the services its sentence (or list item) names; claims about past intervals ("some intervals exceeded") are not judged. (2) Root cause: the type right after "the root cause is/was", else types outside negated or past clauses. (3) Action consistency per service, from the last sentence about the service that states a plan; sentences weighing an option (would, could, risk, if), naming several services, or echoing the prompt's tag text are skipped. On the 30 calls every verdict now matches the reading; recall is lower (4 real mismatches in multi-service calls stay unjudged).
**Effect.** Stale, latest and unsupported-number rates unchanged; the history-order test changes only in wrong SLO-breach claims (now lower under newest-first for both models; Holm p 0.375 and 1.0).

## 2026-10-28: new-service shift (d1)

**Why.** Part of direction d1 (entry of 2026-10-26): how controllers handle a service none of them has seen. The trained RL agents (AWARE, FIRM, ours) never saw it in the simulator or on the cluster; HPA, KEDA and predictive HPA keep the settings tuned on the four services; the LLMs get one line on it in the system prompt, as an operator would add. AWARE's published claim is fast adaptation to new workloads, and AWARE and FIRM keep learning online during runs.

**Service.** SeBS 411.image-recognition (ResNet-50 on CPU, SeBS's photos; `workloads/sebs/imgrec.yaml`, image cached on every worker), deployed as before 2026-10-01: 0.5 CPU request, 1 CPU limit, 768 Mi, spread over workers. About 130 ms per warm request; a new copy's first request loads the model (about 1.3 s).

**Calibration (today, same rules as the other services).**
- One copy alone (Poisson): P95 289 ms at 3.3 req/s, 447 at 3.8, 748 at 5.1.
- All five apps together, 2 runs at 60 to 90% of one-copy capacity: P95 329 to 428 ms at 3.1 to 3.2 req/s, 826 at 4.4. **Capacity per copy 3.0 req/s** (the value of 2026-09-30 on this testbed).
- SLO base: the drift-reference load (3/3/3/5 copies, every entry at 50% of capacity, even arrivals, 15 min) plus image recognition at 3 copies and 4.5 req/s (`fp_imgrec50_20261028`). No-queue P95 336.8 ms, **SLO 1010 ms** (k = 3). The other entries keep their SLOs. At the same load image recognition raises their P95 (vs `ref6`: TeaStore 87 to 207 ms, hotel 22 to 37, graph-bfs 76 to 107, thumbnailer 35 to 45): the shift costs the existing services too, which the paired comparison measures.
- A first base try on dev_0 (`warm_imgrec`, `fp_imgrec_20261028`) is not used: since the trace rebuild of 2026-10-06, dev_0's warm-up sends thumbnailer up to 190 req/s against 3 copies, so it queued (P95 up to 7.7 s).
- Only 17 of the planned 18 scaled copies fit on the workers (worker 1 has 91% of its CPU requested by fixed pods and copies); one image-recognition copy stayed pending in that try. This held in the main matrix too (pending copies are recorded there).

**Load.** The test traces of the main matrix with their four columns unchanged, plus image recognition from Huawei function 132, chosen by rule: the most variable minute-to-minute load (coefficient of variation) among functions active on every profiled day, functions in use excluded. Same day and window as the other functions (previous day's last 6 h + the day, 24x); its busiest scored minute needs 2.5 copies at capacity (7.5 req/s; mean about 3 req/s). `load_scale` does not apply. The cluster is not resized: the new service competes for the same copies (`tools/traces/add_imgrec.py`, `data/traces/runs_imgrec/`).

**Design.** The 8 controllers of the +25% load test (hpa70, keda, phpa, aware, firm, ours, tgpt_joint, qwen_joint; picks of `results/test_picks_v2.json`), 5 test days x 2 repeats (seed = repeat), 80 runs, all in GPU layout A (no switch: both GPU faults came at switches). A block is one (day, repeat): a drift reference with the 4-service config and image recognition idle at 1 copy (`ref7`), then the 8 runs in a random order (seed 2030). Config: `HARNESS_CONFIG=config/harness_imgrec.yaml`, `SCALING_CONFIG=config/trace_scaling_imgrec.yaml`. Code: 5-entry load generator (image recognition last, so the other entries' requests are identical for the same seed, checked offline); our agent takes the config's capacity for a service it did not train on; the LLM system prompts add one line only when the service is configured (the four-service prompts are byte-identical). `harness/chains/new_service.sh`, about 113 h.

**Measures and tests.** Each run pairs with the main-matrix run of the same controller, day and repeat (same seed; the main matrix ran half its blocks in layout B). (1) Image recognition: violation rate (k = 3) and mean copies, Friedman over the 8 controllers, Wilcoxon of each controller vs hpa70 with Holm, two-level bootstrap intervals (as the main matrix). (2) The four existing services: violation rate and copies, paired difference to the main matrix per controller (Wilcoxon, Holm over the 8). (3) Overall 5-entry violation rate, total copies, reversals per hour, reported. Exploratory: AWARE's and FIRM's online updates over the run.

## 2026-10-26: history-order test (d4b)

**Why.** The faithfulness checks (entry below) found that the models often quote an older interval as the current one, and 79-89% of those numbers come from the oldest interval, the first one listed. If the order of the history causes it, presenting the history newest first with age labels should cut the stale readings, and possibly the reversals, which would also explain why history prompts made LLM controllers churn in our conference paper.

**Design.** One change only: `order=newest` (intervals newest first, labeled "now", "1 min ago", ...; same numbers, same system prompts). Both models in two modes: ORACL-style (writes reasoning; faithfulness measurable) and joint (counts only; tests whether decisions change). 4 controllers x 5 test days = 20 runs, 1 repeat, all in GPU layout A (no layout switch: both GPU faults came at switches). Each run is paired with the main-matrix run in layout A on the same day: repeat 1 on even days, repeat 2 on odd days, with that repeat's load seed. A block is one day: the drift reference, then the 4 runs in a random order (seed 2029). `harness/chains/history_order.sh`, about 27 h.

**Measures and tests.** Faithfulness (ORACL-style): the stale share of present-tense numbers and the wrong SLO-breach claims, with the checks of `tools/faithfulness.py` as fixed on 2026-10-26 (the parser reads either order). Behavior (both modes): reversals per hour, violation rate (k=3) and mean copies. Each measure: the mean paired difference over the 5 days with a bootstrap interval, and a Wilcoxon test per model and mode, Holm-corrected within each measure. Expected: fewer stale readings; fewer reversals. With 5 pairs, the Wilcoxon test cannot pass at 5% after correction (its smallest two-sided p is 0.0625), so the intervals carry the result and the tests are reported for completeness.

## 2026-10-26: paper direction; faithfulness checks of the written reasoning

**Direction.** The journal paper does not repeat the earlier conference paper's claims (a frozen LLM as a drop-in autoscaler; prompt structure over model size). Its two new parts: (d1) robustness to shift (25% more load, done; a burst shift and a service never seen in training or tuning, to be planned and fixed here before their runs) and (d4) whether the LLMs' written reasoning is faithful to what they were shown.

**d4 corpus.** Only two controller settings write reasoning: the ORACL-style mode (per application, tagged stages; `test2_{tgpt,qwen}_oracl_t{d}_r{r}`, 20 runs) and the step-by-step ablation (joint; `ab_{tgpt,qwen}_cot_t{d}`, 10 runs). Joint and per-service modes return counts only. Before fixing the checks, the reasoning of 4 decisions from one run (`test2_qwen_oracl_t0_r1`) was read to learn its form; in 2 of them the model described the oldest of the 6 intervals as the current one.

**d4 checks (fixed before running them on the corpus; `tools/faithfulness.py`).** The input is the exact prompt each call received, so every check compares the text with what the model saw.
1. Number grounding: every number the reasoning gives in ms, %, or req/s is looked up among the numbers in its prompt (system and user), same unit; it matches within 0.5 (values of 1 or more) or 0.05 (smaller values), and for req/s within 1%. A number with no match is unsupported.
2. Temporal grounding: numbers in a sentence that refers to the present (current, currently, now, latest, most recent, at present, this or the last interval) are matched against the per-interval values of their field (ms: P95; req/s: request rate; %: CPU or errors): latest if they match the latest interval, stale if they match only an older one, unsupported otherwise.
3. State claims, against the latest interval: a claim that the SLO is exceeded or violated (P95 above the SLO); a claim that replicas are pending (a pending count above 0 anywhere in the prompt); a claim that latency is within the SLO (P95 at or below it). Sentences with a negation are skipped.
4. Root cause (`<root>`, ORACL-style only), against the latest interval of the call's services: none (P95 within the SLO and errors below 0.1%), load surge (request rate at least 10% above the mean of the earlier intervals), cold start (ready below set or pending in the last 2 intervals), CPU (CPU at 70% or more). Memory and network: the prompt gives no memory or network measure, so such a cause has no evidence in the input.
5. Action consistency: the direction the reasoning settles on (keep, scale up, scale down; from `<root>`, then the first stage) against the decision (counts against the current setting); reasoning with no clear direction is reported as unclear.
Outcome (exploratory): the application's P95 in the next interval, after stale against latest readings. Statistics: rates per model with 95% intervals by a bootstrap over runs. A random sample of 30 decisions with the script's verdicts is checked by an author.

**d4 checks, validation (same day).** The first full run was checked by reading random flagged sentences next to their prompts. Rule errors found and fixed, each in the model's favor: (a) hypothetical or conditional sentences ("would risk violating the SLO") counted as state claims, now skipped; (b) the models echo the `<root>` tag description ("cpu, memory, network, cold start, load surge, or none"), which counted as named causes, now removed before matching; (c) present-tense sentences that also point to older intervals on purpose (previous, first interval, history, spike, peak), ranges ("24-30%") and maxima ("up to 80%") counted as stale readings, now skipped; (d) a number counts as stale only if it is more than 10% (and 2 units) away from every latest value of its field; closer ones count as approximate. Found in the check, added as an exploratory measure: which older interval a stale number comes from.

## 2026-10-25: second GPU fault at a layout switch

**What happened.** At 05:38 UTC `high_load_r2.sh` switched to layout A for test days 1 and 3. TelecomGPT, loading on the GPU on the control VM's socket (08:00.0), hit NVIDIA Xid 32 at 05:39:42 and exited; Qwen3.5, loading on the other GPU, hung in engine start-up (one CPU core busy, 12 MiB on the GPU). The chain stopped at 06:38 (TelecomGPT not ready after 3600 s). Days 0, 2 and 4 (27 runs) were already done; no run was affected. As on 2026-10-13, both faults came at a layout switch, on different GPUs. The cluster idled about 3 h.

**What we did.** Stopped the hung Qwen container. The drift references and the controllers that need no GPU from days 1 and 3 run in each block's order (`harness/chains/gpu_outage_hl.sh`, 2 references and 14 runs). The 4 LLM runs follow after a VM reboot (`high_load_r2.sh` skips finished runs). In the runs made during the outage the GPUs hold no model, so their GPU energy is not comparable with the other runs; the controllers in them do not use the GPU, and the KPIs are not affected.

## 2026-10-23: higher load, second repeat

**Why.** The +25% load check (5 pairs) can only give effect sizes; its main reading (AWARE and the joint LLMs degrade least, HPA, KEDA and the predictive HPA most) is one of the paper's main findings. A second repeat (seed 2, the other GPU layout per day) gives 10 pairs, the same design as the main matrix, so the plan's tests (Friedman, Holm-corrected Wilcoxon against HPA 70%) apply. Same 8 controllers plus the budget-aware variant (`harness/chains/high_load_r2.sh`, 45 runs and 5 drift references, about 61 h). Both repeats are reported; the 5-pair repeat-1 effect sizes stay as reported before. Decided before any of these runs.

## 2026-10-23: broken TeaStore web copy, 5 runs repeated

**What happened.** One TeaStore web copy, created during xb_ours_t2_r2 (2026-10-22 16:04 UTC), returned HTTP 500 on category pages from about 17:00 while its home page and its pod status were fine. In xb_ours_t3_r2 and xb_ours_t4_r2 the run started TeaStore at 1 copy, this one: 80% of TeaStore requests failed in about 5 ms, its CPU stayed low, so the agent's HPA kept it at 1 copy (violation rate 25%). In xb_hl_t2, xb_hl_t3 and xb_hl_t4 it was one of 2-4 copies (TeaStore errors 3%; every earlier run in the study: 0.1% at most). The harness health check looks at the control node only and did not catch it. A scan of every run in the study found no other TeaStore errors; the other error rates are on the serverless functions under too few copies, controller behavior, kept.

**Decision.** The 5 runs are a harness failure (a broken application copy, not a controller decision): kept as failed_*, logged in results/invalid_runs.json, repeated. From now on run.py probes every TeaStore web copy's category page after the reset (replacing a failing copy) and at the end of the run (a failing copy marks the run for an automatic repeat).

## 2026-10-22: budget-aware variant, more runs

**Gate and first test runs.** The gate picked the agent trained from scratch (mean violation rate on the 3 development days 1.80%, pretrained 1.94%). On the 5 test days (repeat 1, seed 1) it ran with a mean violation rate of 1.2% at 10.7 mean copies.

**Repeat 2 and +25% load.** The same checkpoint, greedy, on the 5 test days with seed 2 (pairs with the main matrix's repeat 2) and with every rate x 1.25 at seed 1 (pairs with the +25% load runs), after a drift reference run (`harness/chains/ours_budget_more.sh`). The variant then has the same runs as the main agent except the second GPU layout, which does not apply to a controller without an LLM. The main agent stays in every table and figure: its failure under a shared budget motivates the variant, and it is in the fixed plan. Still reported as exploratory: the variant was designed after the main agent's test runs were seen (2026-10-15 entry), with its design fixed before training and the gate on development days only.

## 2026-10-15: our agent's failure explained, budget-aware variant (exploratory)

**Why our agent fails on test days 1-3 (from its decision logs, test runs so far).** It uses only two of its four HPA targets (30% or 90%). Once TeaStore's load passes a level, it sets TeaStore to 30% for 40-60 of 60 minutes while TeaStore is well within its SLO; TeaStore then asks for up to 12 copies, total demand reaches 25-26 copies against 18, pods are pending 56-94% of the time (HPA 70%: 18-38%), and the other services cannot scale (day 2 repeat 2: thumbnailer 20% and graph-bfs 22% violations at 2.4 and 3.8 copies; HPA 70%: 2% at 3.0 and 3.9). On 1-3 decisions per run it raises a target while the service is over its SLO. Cause: each service's decision optimizes that service's own reward; the state shows cluster CPU and the service's own pending copies, not the shared copy budget, and the reward has no cost for starving other services. The published agent was built for one service. Day 0 (lower load) does not reach the budget and the agent is the best controller there. The main results are unchanged (frozen protocol).

**Budget-aware variant, exploratory.** After all planned runs, one variant of our agent with (1) two shared state features: total copies set / copies available, and the mean SLO preservation over all applications; (2) a shared reward term: the same published weights, with R_SLO the mean over all applications instead of the service's own, and a penalty proportional to copies set beyond the budget. Same network, algorithm (Double DQN), budget, seeds (3, median picked), gate (pretrained vs from scratch on the 3 development days) and fine-tuning as the main agent; then the 5 test days, repeat 1 (seed 1, same GPU layout). Reported apart from the main comparison, labeled exploratory, against the main matrix's repeat-1 runs. Design fixed here, before training.

## 2026-10-13: GPU fault, prompt ablation added

**GPU fault at a layout switch.** At 05:14 UTC the main matrix switched the LLM containers to layout B (block 6: test day 2, repeat 2). TelecomGPT, loading on the GPU on the workers' socket, hit NVIDIA Xid 32 (and Xid 13 on a retry); Qwen3.5, loading on the other GPU, hung inside the driver and could not be killed. The two earlier switches had worked. No run was affected (the chain stops when a model does not come up); the cluster idled about 1 h. Fix: a VM reboot (a GPU reset was refused while a process held the GPU). While waiting, the block's drift reference and its first two runs (AWARE and the predictive HPA, the first two in the block's order, neither an LLM) ran (`harness/chains/gpu_outage.sh`); the block then continues in its order after the reboot, so the planned order is kept. The follow-up chain now starts only after the main matrix has finished.

**Prompt ablation added.** Our earlier LLM-autoscaling papers compared four prompt strategies (zero-shot, history, chain of thought, domain knowledge). The main prompt combines history and domain knowledge, so each ingredient is now also run on its own, on joint mode, both LLMs, the 5 test days (40 runs; `ablate=` in `harness/controllers/llm.py`, `docs/analysis_plan.md`).

## 2026-10-08: analysis plan fixed, test matrix v2

**12 controllers, violation rate and cost co-primary.** Both LLMs run in all three modes (6 LLM controllers), with HPA 70%, KEDA, the predictive HPA, AWARE, FIRM and our agent. At the nominal load nearly every controller meets the SLOs, so the violation rate alone would not separate them; cost (mean ready copies) is co-primary, with the same statistics. SLO protection under pressure is compared in the +25% load runs. The plan is fixed as of today in `docs/analysis_plan.md`.

**Block design.** A block is one (test day, repeat), with a drift reference run first and the 12 controllers in a random order. The repeat sets the load generator seed, so every controller in a block gets identical requests. Each test day has one block in each GPU layout, so the GPU assignment cancels within every day (replaces the earlier split of days 0-2 and half of day 3 in layout A, which was not balanced). About 175 h (`harness/chains/test_matrix_v2.sh`).

## 2026-10-08: LLM pair kept, raw request logs out of git

**LLM pair kept: TelecomGPT-R1-27B and Qwen3.5-27B.** TelecomGPT-R1-27B is a fine-tune of Qwen3.5-27B (same architecture, qwen3_5, 64 layers, hidden size 5120), so the pair compares a telecom model with its own base model at equal size and serving: the difference is the telecom training, not architecture or size. Considered and not taken: AdaptKey-Nemotron-30b (LoRA on Nemotron-3-Nano-30B-A3B, trained on network function configuration and anomaly detection; closest to the task but untested on our serving stack) and OTel-LLM-8.3B/12B-IT (Gemma 3; lighter, while decision time is not a bottleneck at 1-2 s per 60 s). A telecom model paired with a different base would confound telecom training with architecture.

**Raw request logs stay on the testbed.** About 45 MB per run; GitHub gets every run's summary, decisions, timelines, GPU and host logs.

## 2026-10-07: control-node stalls, host memory settings

**Control-node stalls.** The control VM sometimes stops for a few seconds (guest kernel: clock jumps of up to 5.4 s, RT throttling); the API stalls (15 s writes), control-plane pods lose their leases, and the load generator, on the control node, stalls with it. Episodes: Sep 30-Oct 1 (the swap period), Oct 3 (one run), Oct 6 13:17-15:16 UTC (three runs); none for days in between. The recorder's 5 s samples show them (normal jitter under 0.01 s); every run now carries a health check, and a stall of 2 s or more makes it a harness failure, rerun and logged (docs/analysis_plan.md). Two causes removed or reduced (no VM setting changed): an unused apt-installed kubelet service on the control VM that crashed and restarted every 10 s since the VM was cloned (57,000+ times, each starting and removing containers) was disabled (2026-10-07 08:16 UTC); the hypervisor had about 1 GB free per socket with 250,000 memory-compaction stalls since boot, so (2026-10-07 08:20 UTC, runtime settings, reset by a host reboot): vm.watermark_scale_factor 10 -> 200 (about 5 GB kept free by background reclaim), transparent huge pages always -> madvise (QEMU asks for them, so the VMs keep theirs), host file cache dropped once (27 GB free after). The stall rate before and after is compared on the following runs.

## 2026-10-06: burst-free function traces, both LLMs served at once, GPU energy per GPU

**Testbed unchanged.** Everything stays on the GPU server with the current VM settings; the second server is not used for the experiment (its CPUs are about 1.9x slower per core, which would push SLOs out of the edge range, and its disks are too slow for the control plane). Calibration and SLOs of 2026-10-01 stay valid: same services, inputs and VMs.

**Function traces: no timer bursts.** Huawei functions 60 and 183 have timer-triggered bursts (up to 2,164 requests in one second every 5 minutes, against about 125 on average); they set the minute's P95 whatever a 60 s controller does, and the two functions caused about 80% of all violations in every controller's runs. Of the 73 functions present on every development and test day, 21 have none (requests spread evenly over each minute: 3.3% in its first 2 seconds, against 6.1% and 8.4% for 60 and 183; no excess every 5 minutes). Within their busiest hour they are almost flat (minute-to-minute variation 2-12%), so they are replayed like Wikipedia: the day's 24 h after the previous day's last 6 h, at 24x. Chosen: 86 (thumbnailer) and 194 (graph-bfs), the strongest daily cycles (peak 2.3x and 1.9x the mean) and the least alike (minute correlation 0.66); 86 has eight near-identical siblings (88-97), so two of those were not paired. Days re-selected with the same rule and seed (Huawei days now also need the previous day, for the warm-up); Wikipedia and Alibaba days unchanged. Run traces: the busiest second is at most 1.2-3.7x its minute's mean (about 10x before), and the thumbnailer spans 15-283 req/s within a run (about 3x before).

**Load level set from an HPA pilot.** Nearly every controller sat at the copy limit (18) with the old traces. The new load_scale is set so that HPA 70%'s peak demand (copies wanted, pending included) fits the copies available, measured on dev_0 (`harness/chains/load_scale_pilot.sh`).

**Both LLMs served at once, one per GPU.** Each 27B FP8 model (about 31 GB) gets its own L40S (`harness/llm_containers.sh`, identical settings otherwise); no model switching between runs, so the test runs can be in a fully random order. The GPU assignment is counterbalanced across test days (layout A/B).

**GPU energy per GPU.** `gpu.csv` interleaved the GPUs' rows without an index and the analysis integrated them as one series, which gives about the mean of the GPUs. Fixed (index logged, energy per GPU and summed); all runs recomputed (`tools/recompute_gpu_energy.py`). An LLM controller adds 6.8-8.9 Wh per scored hour on its GPU over HPA (about twice the figure from the mean).

## 2026-10-03: our agent's variant check on the cluster

On the 4-service design the simulator picked PPO for our agent (development days: PPO 9.4%, QR-DQN 12.6%, DQN 12.8% violations; HPA 70%: 9.9%), and every variant sat at the copy limit. Online fine-tuning exists for DQN and QR-DQN only; PPO learns from large on-policy batches, and three cluster runs give too few decisions for it. The pretraining gate was not run. Instead PPO and QR-DQN run as trained, greedy, no learning, on the 3 development days, interleaved (`harness/chains/ours_ppo_check.sh`), to see if the simulator's ranking holds on the cluster before the final design. Preliminary: the testbed layout is under review, and calibration, simulator and training are redone after it.

Variants changed: PPO is dropped and Double DQN added, so the variants are DQN (the published agent), Double DQN (van Hasselt et al., 2016) and QR-DQN, all on the same network and budget. Rule: a variant must support the method, simulator pretraining plus online fine-tuning on the cluster, which needs an off-policy learner. PPO fails it (on-policy; with SB3 defaults one update needs at least 8 h of cluster decisions), and AWARE already represents PPO among the baselines. TRPO, also checked, fails it for the same reason. PPO's simulator result above stays on record; the cluster check runs to the end. Our DQN variant is plain DQN, as the published code: "double" in its name is the double LSTM.

Simulator pick among DQN, Double DQN and QR-DQN (development days): DQN 12.8%, QR-DQN 12.6%, Double DQN 15.4% violations, all at 18 copies; DQN picked by the rule (within 3 points, then fewest copies; 17.99 vs 18.00). Gate on the cluster (violations dev_0/dev_1/dev_2): pretrained 30.8/15.0/15.4% (mean 20.4%), from scratch 12.9/5.0/12.5% (mean 10.1%): failed, from scratch better on every day. Cause: the pretrained policy runs the thumbnailer hot (HPA target 90% most of the time) and graph-bfs and TeaStore cold (30%); the simulator underrates the thumbnailer's burst sensitivity (its known optimism on dev_0), and three fine-tuning runs do not undo it. The from-scratch agent ends near 70% on every service, close to HPA. Frozen PPO for reference: 21.7/3.8/15.8% (mean 13.8%). Single runs; preliminary, as above.

## 2026-10-01: edge latency class, fewer scaled services, run length confirmed

**Every application's SLO in the edge range of about 20-200 ms.** The study is about an edge cluster; LLM decision time is reported as a controller cost (time, tokens, GPU energy), not limited, as long as the SLOs hold and a decision fits the 60 s interval. Image recognition (SLO 759 ms) and compression (SLO 2,787 ms) do not fit and are removed. The serverless application keeps at least two functions: SeBS functions with edge-range service times replace them, chosen on measured latency (`results/functions/`).

**Fewer scaled services, the same three applications.** Hotel's frontend, search and rate never limit hotel (0.3-0.4 CPU at 450 req/s against 0.9 for reservation) and run at fixed copies; reservation stays scaled. Reasons, measured on 2026-10-01:
- SLA difficulty does not depend on the number of scaled services, because the traces are scaled so the busiest minute always needs 125% of the copies the controllers can use. Simulated HPA on the 3 dev days, 3 seeds: 9.3% (8 services), 8.1% (without compression), 9.5% (4 services), spread about 2 points (`tools/sim_setups.py`, `results/sim_setups/`).
- Fewer scaling actions: 54 to 39 per hour.
- LLM cost per decision (TelecomGPT, real controller code on 10 recorded pilot observations, `tools/llm_setup_timing.py`): joint 4.16 s and 2,167 prompt tokens (8 services) against 2.34 s and 1,230 (4 services); per-service 1.34 s against 0.99 s; ORACL-style about 15 s in every setup.
- Compression's per-minute P95 rests on 52-75 requests per minute and was mostly noise.

**The 4 scaled services and 2 functions (2026-10-01).** Measured at one copy, no load, the P95 of SeBS candidates (`tools/function_latency.py`, `results/functions/`): thumbnailer on SeBS's 800 px photos (the image-recognition data, 60-240 KB) 12 ms instead of 69 ms on its 0.8-4 MB photos; graph-bfs input "small" (10,000 nodes) 25-29 ms; dynamic-html "small" 3 ms (expected SLO below 20 ms); graph-pagerank "small" 43-48 ms (expected SLO above 200 ms). Chosen: **thumbnailer (800 px photos) and graph-bfs ("small")**, both standard SeBS code and data, of different kinds (image processing with storage reads; in-memory graph computation). Scaled: TeaStore front end, thumbnailer, graph-bfs, hotel reservation. Fixed: hotel frontend, search and rate at 2 copies each (config `fixed`; controllers can use 24 - 6 = 18 copies, and the traces are scaled to that). Each function keeps a Huawei trace: thumbnailer fn 60 (per-second spikes), graph-bfs fn 183 (image recognition's minute surges); compression's fn 190 is dropped. Until the base run and calibration of the new layout, the two functions' SLOs, capacities and simulator parameters are provisional estimates from the one-copy timings (marked PROVISIONAL in `config/trace_scaling.yaml` and `sim/params.json`). The LLM system prompt now states TeaStore's real start time (about 25 s plus warm-up; it said 2 s for every service) and the room for scaled services.

**Run length: 60 scored minutes confirmed on the second testbed** (pilot2b, 4 valid runs, `results/pilot2b/`). Violations cluster in the first scored minutes, as in phase 1; the estimate is within 2 points of its 60-min value only after 25-55 min (phase 1: 15-45). Copies settle within 10-15 min.

## 2026-09-30: second testbed, same size; phase 1 redone in full

**The second testbed keeps 4 workers of 4 vCPUs.** `config/harness.yaml` is unchanged. Same worker vCPUs, memory, CPU pinning and images as phase 1 (configuration fingerprint `post_clone_20260930`). The control node grew to 56 vCPUs; its vCPUs 16-55 are pinned to host threads on the other NUMA node, so none can run on worker cores.

**Phase 1 is redone in full from calibration, not reused.** The performance fingerprint found a harness bug that already affected phase 1: the registry cleaner removed new hotel copies from consul about 1 s after they registered, before the API server showed them Ready, and they never registered again (fixed, `docs/harness.md` item 8). In about half of the phase-1 runs one copy of a hotel service sat idle for the whole run while the others took all its traffic, including 4 of the 14 test runs, the picked predictive-HPA setting, fine-tuning runs of all three RL agents, the gate's scratch runs, the pilot HPA runs and the SLO base run. Which controller it hit was random, so the anchor runs planned for 2026-09-27 are dropped.

**Fixed pods pinned to workers** (`workloads/placement.yaml`, applied by `tools/pin_fixed_pods.py`). Before, the scheduler placed the unscaled pods anew after every restart; after the Sep 30 restart five TeaStore back-end pods landed on one worker and TeaStore's base P95 rose from 37 to 162 ms. The plan balances the CPU they used in the base run and their CPU requests over the 4 workers. The simulator reads the same layout.

**Performance fingerprint of the second testbed** (`fp_pinned_20260930`, fixed pods pinned, after one warm-up base run): 0 errors, violation rate 1.4%. Compared with phase 1 (`fp_pre_clone_20260927`), P95 is 8-16% higher for hotel and the functions and 70% higher for TeaStore (37 to 63 ms), and CPU per request is 4-26% higher for every service. The host is unchanged (performance governor, all cores at 3.9 GHz, nothing else running); the guest kernel moved from 6.8.0-137 to 6.8.0-142 and the control node runs more on the same socket. Not investigated further: phase 2 measures everything again on this testbed, and this run is its SLO base.

**One Qwen model: Qwen3.5-27B; Qwen3.8-27B is dropped.** Qwen3.5-27B is the base TelecomGPT-R1-27B was fine-tuned from, so the pair isolates the effect of the telecom fine-tuning. A newer generation would answer a second, weaker question and cost 5 more test runs. LLM controllers in the test runs: TelecomGPT (joint and per-service), Qwen3.5-27B (joint), and the ORACL-style prompt.

**Host swap off during measurements (2026-10-01, for fairness: workers must not swap).** The host swapped worker memory in every run (17.3% vs 8.7% violations for the same controller and day), and per-VM swappiness 0 did not hold under NUMA node 0 pressure. Host swap is off with an automatic swap-on brake below 8 GiB available memory (`docs/cluster.md`).

**Host swap limit 4096 MiB** (`host_swap_limit_mib`; was 512). See `docs/cluster.md`.

## 2026-09-28: the three remaining controllers (built offline, cluster untouched)

**TelecomGPT per-service** (`llm:port=8001,mode=per-service`): one prompt per service with only its own data, 8 calls in parallel. Checked on recorded observations: 1.2-1.3 s per decision, all answers valid.

**ORACL-style prompt** (`llm:...,mode=oracl`), after Bai et al. (arXiv 2602.05292); their fine-tuned model is not released, so the prompting runs on our frozen models. One agent per application (a subgraph of the call graph), in parallel; the prompt carries deployment descriptions (role, resource type C/M/N, readiness time), the call graph, the cluster and application state in Low/Medium/High bins, and the telemetry. Each agent reasons in ORACL's four tagged stages (`<think>`, `<Fault>`, `<Counterfactual>`, `<root>`; free text, stops at `</root>`, at most 700 tokens) and then gives the replica counts in a second, schema-enforced call that sees its reasoning. A rule-based check rejects scale-in of an application over its SLO (or with errors above 1%). A single JSON answer holding the long reasoning fields failed: under grammar-forced decoding the model filled the answer with whitespace until the token limit (4 of 5 agents, with or without vLLM's whitespace option). Checked: 17-18 s per decision, all valid, reasoning 150-330 tokens.

**Our agent, joint variant** (`rl/ours_joint.py`, `ours_joint:` controller): one network sees all 8 services and sets every HPA target. Branching QR-DQN (QR-DQN won the per-service comparison): the published attention double-LSTM over a window whose steps hold all services' features, one head of 200 quantiles per action per service, each head learning from its own service's reward. Published DQN hyperparameters, the same budget (27,000 joint steps = 216,000 service decisions). One transition per decision, so on the cluster learning starts after 50 decisions (the per-service agent, 8 transitions per decision, after about 7). Written in PyTorch because SB3 has no DQN-family algorithm for one discrete choice per service.

## 2026-09-27 (afternoon): phase 1 closed; the study restarts on a resized cluster

**The cluster gets more workers, and the study restarts there from calibration.** A second configuration answers whether the ranking holds with more room. The 4-worker results stay as phase 1 (`testbed_4w.md`, with the redo list); the test runs on it continue until the resize as a reference. Adding 4 workers of the same size needs 8 physical cores and 64 GB: the host's second CPU node is used by another cluster, and the VMs are already assigned 256 GB against 251 GB of RAM (the likely cause of the swap creep), so the resize depends on freeing that cluster's resources (the lab's decision).

## 2026-09-27: test runs start before all controllers are ready; checking the testbed across the VM cloning

**Test runs start now for the 8 ready controllers:** HPA 70% and 80%, KEDA, predictive HPA, AWARE, FIRM, our agent (QR-DQN, per-service), TelecomGPT (joint), each once on the 5 test days, in one fixed random order (`results/test_order.json`). The cluster is free until the VMs are cloned (2026-09-28); no run starts after 05:00 UTC that day. The other controllers join after the cloning.

**How a change of testbed is detected.** The cloning can change placement on the physical host, pinning, memory or versions, and with them the latency every result depends on. Three checks, before and after:
1. Configuration fingerprint (`tools/cluster_fingerprint.py`, `results/fingerprint/`): Kubernetes and node versions and resources, image digests, where the fixed pods run, host CPU, the VMs' vCPUs, memory and pinning, swap settings. `diff` lists every change.
2. Performance fingerprint: the shared base run (fixed copies, evenly spaced arrivals, 15 min; `fp_<label>`), the measurement our SLOs are built on. Compared per app: no-queue P95 and CPU per request.
3. Anchor test runs: 2-3 test runs repeated after the change (same controller, same test day), compared with the first ones.
Rule: if the no-queue P95 of any app moves by more than 10%, or an anchor run differs by more than the noise of one run (about 3-4 points of violation rate), the test runs made before the change are redone on the new testbed.

## 2026-09-25 (night): main matrix controllers; KEDA and predictive HPA setup

**Controllers: all four families.** Rule-based: static, HPA default and tuned, KEDA, predictive HPA. Literature RL: AWARE and FIRM (DDPG). Our RL agent: pretrained in a simulator and fine-tuned on the cluster, joint and per-service. LLMs: TelecomGPT, Qwen3.5-27B and Qwen3.8-27B (joint), TelecomGPT per-service, and an ORACL-style prompt.

**KEDA scales on the request rate, through its metrics-api scaler.** One ScaledObject per service, target = util x the per-copy SLO capacity (hotel services: per service, in hotel entry req/s). The rate is the one every controller sees: requests sent by the load generator over the last 30 s, served to the KEDA operator by the harness. The KEDA HTTP add-on was not used because its proxy sits in the request path and would change the latency we measure. Tuned parameter: util.

**Predictive HPA: the upstream operator (jthomperoo v0.13.2) with its linear regression model, not Holt-Winters.** Holt-Winters predicts only after it has stored at least one full season. A 75-min run holds less than one: TeaStore's compressed day lasts 60 min, and the function and hotel traces have no repeating pattern. Holt-Winters would behave exactly like plain HPA. The linear model fits the last few HPA replica counts and scales to the larger of the HPA value and the prediction. The operator runs on the control node; the chart's two webhook configurations are left out (they point to a missing service and a misspelled API group, `workloads/phpa/phpa.yaml`). Tuned parameters: look-ahead and history size, with the CPU target taken from the tuned HPA.

**Tuning: 4 settings per baseline, one 75-min run each on dev_2; the same rule picks the tuned setting.** Among settings within 3 points of the lowest SLO violation rate (k = 3), the one with the fewest mean copies. Three points is about the noise of one run (pilot: 95% range of a 60-min rate about 3-4 points). SLO first, cost as the tie-breaker. HPA: CPU target 50/60/70/80%. KEDA: util 0.5/0.6/0.7/0.8. Predictive HPA: look-ahead 60/120 s x history 6/12. `tools/tuning_table.py` applies the rule.

**Literature RL: AWARE and FIRM as published, trained in our simulator.** Both are per-service agents that only train in AWARE's table-driven simulator (copies and arrival rate to CPU utilization, SLO share and latency); FIRM's default schedule is 9,000 episodes of 300 steps, impossible at a 60 s interval on the cluster. We keep their algorithm code (AWARE `testing/` meta-PPO, FIRM DDPG), state (CPU utilization, SLO share, copies, arrival rate; CPU-share terms dropped, horizontal only) and reward (0.3 x CPU utilization + 0.7 x SLO share, penalty for flip-flopping). They train on the same calibration-based simulator as our RL agent, which is built first.

**Online learning during scored runs: as each method publishes.** AWARE keeps its reward-triggered retraining, FIRM its online updates; the logs record when each one learned. Every run starts from the same checkpoint.

**Cluster fine-tuning: 3 runs per RL agent.** After simulator training, every RL agent (AWARE, FIRM, ours) gets the same budget on the cluster: one 75-min run on each development day (dev_0, dev_1, dev_2), learning online, about 4 h per agent. The checkpoint after these runs is the one every test run starts from.

**Tuning results (dev_2, rule above):** HPA 80% (violations 12.7%, 22.9 copies; all four targets within 12.0-14.3%), KEDA util 0.7 (10.3%, 19.8 copies). Predictive HPA (CPU target 80%): look-ahead 120 s, history 6 (9.7%, 24.0 copies; all four settings kept the cluster full).

**Our agent: the stability-aware HPA-target meta-controller from the FGCS work.** As published (`dlstm_agent.py`): DQN (plain SB3 DQN; corrected 2026-10-03, earlier entries said Double DQN) with the attention double-LSTM over an 8-step window, reward 0.55 R_SLO + 0.25 R_CPU + 0.12 R_succ + 0.08 R_stab. Every interval it sets each service's HPA CPU target (30/50/70/90%); native HPA does the scaling. Per-service: one shared network, the 8 services step together on one simulated cluster (`rl/ours_env.py`); the joint variant (one head per service) follows. Adapted: R_SLO thresholds rescaled to each service's SLO and on P95; memory, time of day and the two load knobs of the old experiments dropped from the state. Variants: QR-DQN and PPO on the same network, trained in the simulator with the same budget (216,000 service decisions, as AWARE); the best in the simulator on the development days (`rl/eval_ours.py`) goes to the cluster, with the pretraining gate (pretrained vs from scratch, both fine-tuned 3 runs). Result: in the simulator on the development days QR-DQN 8.1%, DQN 10.4%, PPO 11.3% violations (simulated HPA 70%: 7.9%); QR-DQN picked. Gate on the cluster (violations dev_0/dev_1/dev_2): pretrained 6.0/5.0/11.0% (mean 7.3%), from scratch 28.0/2.7/11.3% (mean 14.0%): passed on the mean; the from-scratch agent loses its first run to exploration and is level after. Both sat at the 24-copy limit almost throughout. The test runs start from the pretrained agent after its 3 fine-tuning runs (`ours_qrdqn_ft`), greedy, no learning.

Porting notes (`rl/aware_agent.py`, `rl/firm_agent.py`, `rl/features.py`):
- AWARE's retraining trigger compares the mean episode reward with 100-120. With rewards of at most 1 per step and 10-step episodes it can never be met, so the published agent never stops learning. We keep that: AWARE learns online throughout.
- FIRM's exploration factor decays by 1e-6 per update with no floor; at the published 2.7M updates it would reach -1.7 and flip the noise. We floor it at 0 (a bug fix, not a design change).
- Simulator training uses each method's published budget on the RL training days (80 days, `data/traces/train`, never on the cluster): AWARE 500 iterations x 5 episodes per service (about 360 simulated runs), FIRM 2.7M transitions (4,500 runs, one agent for all services).
- The observation has latency percentiles, not the share of requests within the SLO that both methods use; it is read off the percentiles (piecewise linear), the same way in the simulator and on the cluster.

## 2026-09-25 (evening): run length confirmed, 60 scored minutes

Pilot: HPA (CPU target 70%) and TelecomGPT (joint), one 75-min run each on development days dev_0 and dev_1, interleaved (`results/runs/pilot_*`). KPIs over the first W scored minutes: `tools/pilot_runlength.py`, output in `results/pilot/`.

| W (min) | HPA dev_0 | HPA dev_1 | TelecomGPT dev_0 | TelecomGPT dev_1 |
|---|---|---|---|---|
| 10 | 4.0% | 14.0% | 20.0% | 20.0% |
| 30 | 5.3% | 6.0% | 16.7% | 8.7% |
| 60 | 7.3% | 5.0% | 15.7% | 5.3% |

Overall violation rate, k = 3. It stays within 2 points of its 60-min value from W = 15, 30, 25 and 45 min.

**Decision: keep 15 min warm-up + 60 min scored.**
- Violations cluster in the first scored minutes, where load is highest (about 70% of each app's peak, against about 60% later). The estimate falls as the window grows, and one run was still falling at 60 min.
- A 30-min window would have changed a conclusion: TelecomGPT worse than HPA on dev_1 (8.7% vs 6.0%), against about equal at 60 min (5.3% vs 5.0%).
- A longer window buys little. The 95% range of a 60-min rate (moving-block bootstrap, 5-min blocks) is about 3-4 points, while TelecomGPT differs by 10 points between the two days. Repeats over test days matter more than minutes.
- Cost settles fast: mean copies within about 0.3 of the final value after 10-15 min.
- The P95 of all requests in the hour does not settle for the functions (image recognition not within 10% before 60 min), because a few overloaded minutes decide it. The per-minute violation rate is the main latency KPI; percentiles are reported next to it.

## 2026-09-25 (afternoon): load level 45%, SLO base measured in the shared cluster

Harness smoke tests (`harness.md`) showed that calibrated per-copy capacities are too optimistic when the whole cluster is busy, and that neighbouring pods alone slow TeaStore and compression by about 2x.
- At 100% of the trace load no allocation works (TeaStore collapses above about 340 req/s).
- At 60%, even 22 of 24 copies miss every SLO.
- At 45%, 22 copies meet hotel, image recognition and thumbnailer; TeaStore (P95 34-61 ms against 39) and compression (1.0-1.7 s against 1.3 s) miss because of interference, not queueing.

**Load level: every trace rate is multiplied by 0.45** (`load_scale` in `config/trace_scaling.yaml`). TeaStore's peak becomes 202 req/s, well below its persistence ceiling of about 300; the combined peak needs about the whole cluster.

**SLO base: the no-queue P95 of each entry measured in the shared cluster** (rule 3 amended). All entries run together at the 45% level with evenly spaced arrivals and 22 copies (per-copy load at most about half of the calibrated capacity), so the base includes the interference of a shared cluster but no queueing. SLO = 3 x that value; k = 2 and 5 still reported. The alone-measured values stay in `calibration.md` for reference.

## 2026-09-25: hotel trace from Alibaba service MS_42222, cleaned

Details and numbers in `traces.md`. Of five candidate services, MS_61753 is unusable (each pod row repeated thousands of times per minute in the published table). MS_42222 is used. One-minute jumps that hit all services at once, with unchanged response time, are treated as a collection artifact and replaced by the local median (above 1.15x the median of the 7 surrounding minutes). The cleaned hotel load is nearly flat within the hour, so the hotel app acts as the steady neighbour next to TeaStore's compressed daily cycle and the functions' bursts. Whole-day downloads for more variety were rejected (about 84 GB per day). With all traces scaled, each service needs at most 9.4 copies, within the cap of 12.

## 2026-09-25: TeaStore peak capped at 450 req/s

TeaStore's unscaled persistence service limits the whole app near 450-500 req/s (1.2 CPU at 3 front-end copies, warmed). Options were to cap TeaStore's trace peak or to scale persistence as a 9th service. Decision: cap at 450 req/s and keep 8 scaled services. TeaStore already brings the slow-start challenge (about 25 s to Ready plus minutes of Java warm-up). The cap lives in `config/trace_scaling.yaml`, together with the SLOs and the per-copy capacities measured with all apps running (interpolated estimates, refined in the pilot).

## 2026-09-24 (night): rules from calibration

Calibration showed that placement, arrival randomness, and CPU limits change results as much as the controller does. Four rules, the same for every method:

1. **Placement: copies of a scaled service spread evenly over the workers** (topology spread, maxSkew 1, `DoNotSchedule`, `nodeTaintsPolicy: Honor`). Two copies on one worker share physical cores through hyperthreads: image recognition at 12 req/s had P95 3.7 s on one worker against 370 ms spread over two. `Honor` keeps the powered-off extra node out of the spread count. Tested: 6 copies land 1/2/1/2 on the 4 workers.
2. **CPU: every scaled service requests 500m and is limited to 1 CPU.** TeaStore's front end had no limit and used 2.6 CPUs. A 1-CPU request would fit only about 11 copies in the cluster.
3. **SLA: k times the no-queue P95 of each user-facing entry**, measured with evenly spaced traffic at low load (no queueing). Under random traffic, even low load already queues, which makes a poor baseline. Main analysis uses k = 3; results are also reported for k = 2 and k = 5 (every request's latency is logged, and changing k is an offline recomputation). SLO entries: TeaStore front end, the three functions, the hotel frontend (end to end).
4. **Trace peaks: scale traces so the busiest moment needs about 120-130% of the 16 worker CPUs**, using the per-copy capacities measured with random arrivals. At the peak a controller must choose which service to favor. If the peak fit easily, every controller would look alike.

Scaled services (8): TeaStore front end; thumbnailer, compression, image recognition; hotel frontend, search, reservation, rate (the four busiest hotel services at 800 req/s).

## 2026-09-24: run length, repeats, RL pretraining

**Run length: 75 min = 15 min warm-up (not scored) + 60 min scored (60 decisions), confirmed by a pilot.**
- Warm-up: the same trace lead-in for every controller, to fill histories (LLM, predictive HPA) and reach steady state.
- Scored window: Wikipedia (TeaStore) replays one full day at 24x (hourly, smooth data: compression adds no artificial spikes). Huawei (functions) and Alibaba (hotel) replay the busiest hour of the test day at 1x (compressing them would turn 1-minute bursts into 2.5-second spikes).
- Pilot: HPA and TelecomGPT, one 75-min run each on two development days (dev_0, dev_1). Pick the shortest scored window after which the KPI estimates (SLO violation rate, P95) stop changing meaningfully. This sets the final length and gives the paper its justification. Result: 60 min kept (2026-09-25 evening entry).

**Repeats: 5, each on a different held-out test day.** Every controller gets the same day for the same repeat index (paired design). This measures robustness across days, not just machine noise. 300 scored decisions per controller.

**RL: pretrain in a simulator, fine-tune on the cluster.** The simulator is built from the calibration curves (measured per-copy capacity and latency percentiles against load, per service), not from a regression on logs. Reason: the July 2026 attempt fitted a regression latency model to logs; it looked good offline (R2 0.93) but drifted in closed loop, and the pretrained agent failed its gate. Validate the simulator against real runs before use. Gate: a sim-pretrained agent must beat one trained from scratch on development days, or it is not used.

Main matrix estimate: 16 controllers x 5 repeats x (75 + 10 min reset) = about 113 h.

## 2026-09-24: multi-service, one shared run

**All workloads run at once on the shared cluster, each driven by its own trace. Every controller manages all of them at every decision step.** This replaces running the three scenarios one by one. Every method goes through the same combined run.

- About 8 scaled services: TeaStore entry service (Wikipedia trace), 3 SeBS functions (3 Huawei traces), 3-4 hotel-reservation services (Alibaba trace). They share the 16 worker vCPU.
- Joint decisions: a controller sees all services and returns replicas for all of them.
- HPA, KEDA, and AWARE run one instance per service (their native form). The LLMs and our RL agent run in two variants: joint (one controller for everything) and per-service (one controller per service, seeing only its own service). This tests whether a global view helps. The per-service variant is also the fallback if joint proves too complex.
- Results are also broken down per workload type (web, serverless, microservices).

**Control interval: 60 s for every method.** An 8-service prompt is long, and thinking can take minutes. Main runs use thinking off. Thinking on is a smaller ablation.

**SLA: one rule for all methods, set per service from calibration.** Proposal: for each service, measure the P95 latency at low load with ample replicas (calibration run). The SLO threshold is k times that value, with the same k for every service (k to be fixed before the main runs, for example 3). Web and microservice SLOs use end-to-end request latency. Function SLOs use per-invocation latency. Every method is scored against the same thresholds.

**Record everything, report KPIs.** Raw metrics at 5-15 s resolution, stored for every run:
- Per service: request rate offered and served, errors, latency (average, P50, P75, P90, P95, P99, from the load generator's per-request timings, plus the full histogram), replicas desired and ready, pod CPU and memory, throttling, pod start time.
- Per node and host: CPU, memory, network, Kepler power, host RAPL power.
- Per controller: every observation and decision (full prompt and reply for LLMs), decision latency, tokens, GPU power and utilization.

KPIs reported per method (overall and per service type):
- SLO violation rate (share of intervals with P95 above the threshold), average latency and P50, P75, P90, P95, P99 (per service and overall), error rate.
- Cost: replica-seconds and vCPU-hours.
- Energy: cluster and host.
- Stability: scaling actions per hour, direction reversals.
- Fairness: Jain index over per-service SLO attainment.
- Controller cost: decision latency, GPU energy per decision.
- Robustness: recovery time after a flash crowd.

## 2026-09-24 (later): three general scenarios as a difficulty ladder

**Telecom scenario dropped.** A 5G core scenario needs protocol-level depth the paper cannot defend. The SD-Core work is in `archive/sdcore/`. This replaces the two-scenario scope below.

**New scope: three ordinary cloud workloads, each adding one challenge.** Same controllers, LLMs, prompts, metrics, and harness everywhere.

| Step | Scenario | Trace | What it adds |
|---|---|---|---|
| 1 | Web shop (TeaStore), one service | Wikipedia (or another public web trace) | Predictable daily pattern. Baseline case, closest to earlier papers |
| 2 | Serverless, 3 SeBS functions | Huawei Cloud 2023, one trace per function | Independent services, sudden bursts, one shared cluster |
| 3 | Microservices, hotel reservation (DeathStarBench), 3-4 stateless services scaled | Alibaba microservices 2022 | Dependent services: the bottleneck moves, and slowdowns spread |

The paper asks where frozen LLMs start and stop being good autoscalers along this ladder.

**Research questions.**
RQ1: frozen LLMs against tuned HPA, KEDA, RL, and AWARE, and on which step they gain or lose ground.
RQ2: does networking fine-tuning (TelecomGPT-R1-27B vs its base Qwen3.5-27B) or a newer generation (Qwen3.8-27B) move that pattern? TelecomGPT stays as a test of whether fine-tuning for an adjacent technical domain transfers to cloud autoscaling.
RQ3: cost per decision (latency, GPU energy) per step.
RQ4: robustness on held-out days and a flash crowd, on every step.

**Hypothesis to register before the main runs.** LLMs do best where reasoning and context matter (step 3, and the daily pattern in step 1). They do worst where reaction speed matters (the bursts in step 2), since a decision takes 5 to 15 s.

**Harness.** One observation format for every controller: a list of services with their metrics. One action format: replicas per service (CPU field kept for future vertical scaling). A scenario is a bundle: manifests, trace, SLOs, service list.

## 2026-09-24

**Scope: one paper, two scenarios, horizontal scaling only.** (Superseded above: the telecom scenario was dropped.)
The first design (4 scenarios, 11 controllers, CPU resize, about 220 h of runs) was too large for one paper.
Kept: a telecom scenario (Open5GS AMF and UPF driven by the Milan Telecom Italia 2013 data) and a non-telecom control (SeBS serverless functions on Huawei Cloud 2023 traces). Both are needed to test whether the telecom model helps on telecom functions specifically.
Moved to future work: CPU resize (vertical scaling), DeathStarBench chain, all scenarios at once, more RL variants, node failure and noisy neighbor, node placement.
The harness must keep these easy to add later: one controller interface, scenario bundles, an action space that already has a CPU field (held fixed for now), and one log schema.

**Telecom scenario gets a time box.**
Open5GS with UERANSIM is the highest risk. Prototype it for 2 to 3 days. If it fails, use a telecom data pipeline driven by the Milan data.

**LLMs: TelecomGPT-R1-27B vs its base Qwen3.5-27B vs the latest Qwen3.8-27B.**
TelecomGPT-R1 is fine-tuned from Qwen3.5-27B. Comparing it with the same base at the same size isolates the effect of telecom training. The earlier choice, Qwen3.5-9B, mixed model size with domain tuning.
Qwen3.8-27B (released 2026-08-13, same architecture) answers whether telecom tuning still pays off against a newer general model.
Caveat to disclose: the FP8 schemes differ. Our TelecomGPT copy uses per-channel weights with per-token dynamic activations (llm-compressor). Qwen's official FP8 builds use 128x128 block weights with dynamic activations.
TelecomGPT-R1-9B (named in the TelecomGPT paper) is not on Hugging Face yet. If it appears, pair it with Qwen3.5-9B for a same-size ablation.

**Literature baselines.**
AWARE (USENIX ATC 2023) is the main published baseline. It has released code, runs on Kubernetes, and will be restricted to horizontal actions (disclosed). Its repo also carries a FIRM (OSDI 2020) DDPG implementation.
ORACL (arXiv 2602.05292) is the closest LLM work. It has no released code or weights and uses a fine-tuned model. Plan: ask the authors for artifacts. Otherwise add an "ORACL-style prompt" on our frozen models, labeled as inspired by ORACL.
The open-source predictive HPA (jthomperoo, Holt-Winters) replaces a home-made predictive baseline.

**Disk: rotate the two Qwen models.**
The 200 GB disk had about 14 GB never allocated; it was claimed (root now 195 GB). Three 27B models (about 31 GB each) still do not fit with a safe margin. TelecomGPT stays on disk. Qwen3.5-27B and Qwen3.8-27B take turns. A swap is a 7 to 8 min download at about 70 MB/s.

**Rules for all LLM runs.**
No silent model swaps. Same thinking setting for every model in a cell. Enforced JSON output and enough tokens (an old bug: truncated reasoning and a parser that took the last number). Frame results as characterization, not superiority.
