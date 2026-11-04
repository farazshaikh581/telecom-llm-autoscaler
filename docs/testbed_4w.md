# Phase 1: the 4-worker testbed (closed 2026-09-27)

Everything below was measured on the first testbed: 4 workers of 4 vCPUs (2 cores with hyperthreads each), room for 24 copies of 0.5 CPU, control node with the LLM GPU. The cluster is resized next (more workers, decided 2026-09-27) and the study restarts there from calibration. These results stay as the record of this testbed; none of them is reused on the new one without being measured again.

Testbed identity: configuration fingerprint `results/fingerprint/pre_clone_20260927.json` (`tools/cluster_fingerprint.py`), performance fingerprint run `results/runs/fp_pre_clone_20260927` (fixed copies, evenly spaced arrivals, 15 min).

## Results of this phase

| Step | Result | Where |
|---|---|---|
| Calibration, one copy alone | per-copy capacity and no-queue P95 per entry | `calibration.md` |
| SLO base, shared cluster | no-queue P95 TeaStore 47.9, hotel 16.5, thumbnailer 111, image recognition 210, compression 846 ms; SLO = 3x | `calibration.md`, `config/trace_scaling.yaml` |
| Load level | 0.45 of the scaled traces | `decisions.md` (2026-09-25) |
| Trace scaling | busiest minute needs 125% of 24 copies | `tools/traces/build_traces.py` |
| Run length | 15 min warm-up + 60 min scored (pilot) | `decisions.md` (2026-09-25 evening) |
| Tuning (dev_2) | HPA 80%, KEDA util 0.7, predictive HPA look-ahead 120 s, history 6 | `decisions.md` |
| Simulator | per-run violation rate within about 3 points on held-out runs; TeaStore optimistic | `simulator.md` |
| AWARE, FIRM | simulator training, 3 cluster fine-tuning runs each | `decisions.md`, `results/runs/finetune_*` |
| Our agent | QR-DQN picked in the simulator; gate passed (pretrained 7.3% vs from scratch 14.0%) | `decisions.md` |
| Test runs | 8 controllers x 5 test days in a fixed random order, until 2026-09-28 05:00 UTC (partial) | `results/test_order.json`, `results/runs/test_*` |

## What carries over (methods, not numbers)

Harness, controllers, load generator, recorder and analysis; trace days and shapes; SLO rule (3 x shared no-queue P95); run-length rule and pilot method; tuning budget and pick rule; RL budgets, gate and fine-tuning budget; drift checks (`decisions.md` 2026-09-27).

## What is redone on the resized cluster, in order

Update 2026-09-30: the second testbed keeps the same size (4 workers of 4 vCPUs), so step 2 changes nothing. Every step is redone anyway, because a registry-cleaner bug left a hotel copy idle in about half of the phase-1 runs (`docs/decisions.md`, 2026-09-30). Step 1 is done: configuration fingerprint `post_clone_20260930`, fixed pods pinned (`workloads/placement.yaml`), performance fingerprint `fp_pinned_20260930`. Since 2026-10-01 the design has 4 scaled services (`docs/decisions.md`); everything from calibration on is redone for that design on the new VM layout. On the new layout: apply `workloads/sebs/sebs.yaml` (graph-bfs in, compression and image recognition out), pin the fixed pods again, base run, then calibration with the new functions.

1. Fingerprints: configuration, and a first performance fingerprint.
2. Set the new size in `config/harness.yaml`, the one place it lives since 2026-09-28: `workers`, `worker_vcpus`, `copies_available` (measure how many 0.5-CPU copies fit), `max_replicas`. The trace builder, the LLM prompt, the RL state scaling and start sizes, and the harness preflight read it from there (checked: identical output for the 4-worker values; an 8-worker setting propagates to all of them). `sim/params.json` takes the number of workers from the cluster when it is rebuilt (step 8).
3. Calibration: per-copy capacity and latency, alone and with all apps running; check the unscaled services at the new load (TeaStore persistence limits the app near 450-500 req/s; the hotel databases and caches were never loaded this high).
4. SLO base in the shared cluster (evenly spaced arrivals).
5. Load level and trace scaling to the new room; rebuild the run and training traces.
6. Harness smoke tests (static, HPA, one LLM).
7. Run length: confirm with a short pilot (method unchanged).
8. Simulator: rebuild parameters from the new calibration, open- and closed-loop checks.
9. Baseline tuning on dev_2 (HPA, KEDA, predictive HPA; 4 settings each).
10. RL: simulator training (AWARE, FIRM, our agent's variants), variant pick, 3 fine-tuning runs each, gate.
11. Test runs: every controller on the 5 test days; the controllers not built in phase 1 (TelecomGPT per-service, ORACL-style prompt, our agent's joint variant) join here.

Cluster time, roughly: steps 3-7 about 1.5 days, step 9 about 16 h, step 10 about 1 day, step 11 about 4 days for 14 controllers.
