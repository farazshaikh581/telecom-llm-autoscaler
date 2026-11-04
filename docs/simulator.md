# Simulator

Used to pretrain the RL agents (ours, AWARE, FIRM) before cluster fine-tuning. It replays the same per-second traces as the harness and returns the same observation (`harness/observe.py`). Code: `sim/`. Parameters: `sim/params.json`, built by `sim/params.py`.

## Model

| Part | Model | Source |
|---|---|---|
| Functions (thumbnailer, compression, image recognition) | Every request simulated. Random Ready copy (as kube-proxy), one request at a time per copy (FIFO). | Service time: no-queue latency in the shared cluster (`slo_shared_base_even`, minutes 5-15) |
| TeaStore | Per second: no-queue latency x (1 + a rho^b / (1 - rho)) + fluid backlog wait. Front-end copies plus the shared persistence service. | Curve fitted on the alone Poisson sweeps with 2 and 3 copies |
| Hotel | Same curve on its bottleneck service (reservation first); service capacities in the ratio of their CPU per request | Poisson sweeps with 2 and 3 copies of each service |
| TeaStore warm-up | A request served by a copy that turned Ready `age` seconds ago takes 800 x exp(-age / 40) ms longer (Java JIT) | **Fitted on the pilot scale-ups** (grid search); the only values not from calibration |
| Workers | 4 workers; copies spread per service (topology rule), fixed pods where they run. Per second, each worker's CPU demand = sum of CPU per request x load; above 2.6 CPUs (2 cores with hyperthreads) its pods slow down in proportion. | CPU per request per pod: Prometheus, shared base run |
| Cluster | At most 24 copies placed, the rest Pending; Ready after the measured start time (TeaStore 25 s, others 2 s); 30 s timeout | `config/harness.yaml`, `results/calibration/scaleup_*` |

## Validation

**Open loop** (`sim/validate.py`): replay a real run's arrivals and Ready copies (with their workers), compare the per-minute P95 and SLO violations over the scored hour. Pilot runs (dev_0, dev_1), 240 minutes per entry:

| Entry | Violations, real | Violations, sim | Minutes that agree | Sim / real P95 (median) |
|---|---|---|---|---|
| TeaStore | 13.3% | 9.6% | 94.6% | 1.01 |
| Thumbnailer | 12.5% | 16.2% | 96.2% | 1.22 |
| Compression | 7.5% | 12.1% | 92.1% | 1.24 |
| Image recognition | 8.3% | 9.2% | 98.3% | 1.03 |
| Hotel | 0% | 0% | 100% | 0.90 |

Per run, real vs sim violation rate: HPA dev_0 7.3 vs 6.3%, HPA dev_1 5.0 vs 5.0%, TelecomGPT dev_0 15.7 vs 18.7%, TelecomGPT dev_1 5.3 vs 7.7%. The worker capacity (2.6 CPUs) matters little: at 3.2 and above, contention never binds and the results are unchanged; at 2.0 the sim is far too pessimistic.

Without the warm-up term the sim had no TeaStore violations at all: they happen in the 20-40 s after a new copy turns Ready, so controllers that add copies often pay more.

**Closed loop** (`sim/validate_closed.py`): the Kubernetes HPA algorithm (`sim/sim_controllers.py`) drives the sim on the same trace and start sizes, 3 seeds.

| Run | Violations, real | Violations, sim (3 seeds) | Mean copies, real | Mean copies, sim |
|---|---|---|---|---|
| HPA 70%, dev_0 | 7.3% | 8.6% (8.3-8.7) | 23.6 | 23.4 |
| HPA 70%, dev_1 | 5.0% | 5.0% (4.3-5.3) | 23.1 | 22.7 |

Weak test: HPA sits at the 24-copy limit.

**Held-out check** (runs not used for any fitting: dev_2 HPA and KEDA tuning runs, Qwen3.5 on dev_0; `sim/validation_heldout.csv`, `sim/validation_closed_heldout.csv`).

Open loop, per run (real vs sim violation rate): HPA 50/60/70/80% on dev_2 12.0/14.3/12.3/12.7 vs 11.7/13.0/12.0/12.3%; KEDA util 0.5/0.6/0.7/0.8 11.7/11.0/10.3/15.0 vs 9.0/9.7/8.7/12.7%; Qwen3.5 dev_0 9.3 vs 12.7%. Per entry, minutes that agree: 92-98%.

| Entry | Violations, real | Violations, sim | Minutes that agree |
|---|---|---|---|
| TeaStore | 11.5% | 3.5% | 91.7% |
| Thumbnailer | 22.0% | 25.4% | 95.9% |
| Compression | 12.2% | 15.6% | 94.8% |
| Image recognition | 12.6% | 12.0% | 97.2% |
| Hotel | 2.0% | 0% | 98.0% |

Closed loop, HPA on dev_2 (real vs sim, 3 seeds): 50% 12.0 vs 10.1%, 60% 14.3 vs 11.7%, 70% 12.3 vs 10.2%, 80% 12.7 vs 11.1%; mean copies within 0.3. The sim is about 2 points optimistic; it does not separate the four targets, and neither do the real runs (all within the 3-4 point noise of one run).

Verdict: the sim reproduces per-run violation rates within about 3 points on new days and controllers, the size of one run's noise. TeaStore is the weak part: on new runs the sim misses most of its violations (3.5 vs 11.5%), so agents trained in it will under-provision TeaStore; the cluster fine-tuning runs are meant to correct that.

## Known gaps

- Functions are slightly pessimistic (P95 about 1.2x).
- TeaStore has occasional multi-second minutes with no scale event (for example HPA dev_0 minute 65, 2.9 s); the sim does not produce them.
- The warm-up term is fitted, not calibrated, and transfers only partly to new days; a scale-up-under-load calibration would replace it.

## 4-service design (2026-10-02)

Fast functions (thumbnailer on 800 px photos, about 10 ms; graph-bfs "small", about 25 ms) spend much of their
shared-cluster latency outside the copy. Their model splits it: busy time = one-copy handler time x k holds the
copy; the rest of the base-run latency is overhead that does not (`sim/params.py`). k was first fitted on the
together runs (smooth Poisson load: 1.0 and 1.4), which left the simulator too optimistic on the pilot; it is now
fitted on the pilot's real, bursty arrivals (`sim/fit_busy_pilot.py`, dev_0 runs): thumbnailer 1.5, graph-bfs 1.8.
TeaStore warm-up refitted on the pilot: j0 1500 ms, tau 60 s (13 scale-up minutes).

| Check | Real | Simulated |
|---|---|---|
| Open loop, held out (dev_1 pilot, dev_2 tuning), thumbnailer violations | 20.0% | 19.6% |
| Open loop, held out, graph-bfs violations | 14.6% | 16.3% |
| Open loop, all 6 runs, per run | 7.1-20.0% | 6.7-15.8% (within 0.4-5.8 points) |
| Closed loop HPA, dev_1 / dev_2 t60 / dev_2 t70 | 6.7 / 11.7 / 12.5% | 5.8 / 11.9 / 11.0% |
| Closed loop HPA, dev_0 | 20.0% | 11.5% |
| Copies (closed loop) | 16.9-18.0 | 16.0-18.0 |

Known gap: TeaStore's tail. Real TeaStore violations 6.9-8.8% against 1.4-3.3% simulated: multi-second spikes
(likely Java pauses) that the smooth curve model cannot produce; the same gap was seen in phase 1 (3.5 vs 11.5%).
dev_0 is the hardest day (thumbnailer 48% real). The simulator serves RL pretraining; the gate on the cluster
(pretrained against from-scratch agents) decides whether that pretraining helps.
