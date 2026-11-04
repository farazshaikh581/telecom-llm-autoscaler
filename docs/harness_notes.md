# Harness notes

Checked 2026-09-24 (night). Facts the experiment harness must respect.

## Clocks

Time sync (`systemd-timesyncd`) is installed and switched off on every node. Measured against the physical host (NTP-synced): the control node is about 274 s ahead, and the workers are 7.5 to 8.2 s behind the control node (up to 0.7 s apart from each other).
- Effect: metrics stamped on different nodes do not line up. Prometheus keeps cAdvisor's own timestamps (`honorTimestamps`), which come from the worker clocks.
- Fixed 2026-09-25: NTP (systemd-timesyncd) turned on for all five nodes with a short-lived privileged pod per node (`nsenter` into the host, `timedatectl set-ntp true`), control node first, then the workers. Clocks stepped back about 275 s once; every node stayed Ready. Verified: control node within 0.01 s of the host, workers within 0.02 s of the control node. NTP stays on.
- The harness times everything that matters (scale-up, decisions, request latency) on the control node's clock.

## Metric freshness

| Source | Resolution | Note |
|---|---|---|
| metrics-server (what HPA uses) | 15 s | pod CPU and memory |
| Prometheus (cAdvisor, kubelet, node exporter, kube-state-metrics) | 30 s scrape | a rate needs two scrapes: 60-90 s old, too slow for a 60 s controller |
| Kepler | 15 s | not used for energy (see below) |
| Load generator (k6) | per request | rate, errors, latency percentiles, live |

Rule: every controller gets the same observation feed. Request rate and latency percentiles come live from the load generator; CPU comes from metrics-server (15 s), the same freshness HPA has. Prometheus keeps recording for the analysis.

## Energy

Kepler inside the VMs is known to be wrong (August 2026 calibration: a constant 147 W per VM from its trained model). Energy comes from:
- Host RAPL, sampled on the physical host by `host_power_probe.sh` (from the August calibration: counter-wrap handling, per-VM CPU time from libvirt). With the CPU pinning, package 0 (NUMA node 0) holds our whole cluster, and package 1 holds the other cluster. Package 0 is therefore our cluster's CPU power plus the host's own share. At idle: package 0 about 94 W, package 1 about 82 W.
- GPU power from `nvidia-smi`, sampled every second on the control node (84 W idle with a model loaded, 350 W limit).
- Per-VM split (control node vs workers) from each VM's share of CPU time.

## Scale-up

Time from a scale command until the new copy is Ready (control-node clock, image cached, 3 repeats each), and its warm-up:

| Service | Ready after | First request on the new copy | Warm after |
|---|---|---|---|
| Thumbnailer | 1.1-1.8 s | 57 ms (normal) | at once |
| Compression | 0.8-2.1 s | 440-460 ms (normal) | at once |
| Image recognition | 0.8-1.1 s | 1.5-1.9 s (model load) | 2nd request (about 120 ms) |
| TeaStore front end | 24-26 s | 2.7-2.9 s | minutes (Java JIT: 170 ms, then 40 ms by the 15th request, 9-13 ms warm) |
| Hotel frontend, search, reservation, rate | about 2 s | 7-8 ms (frontend; the others speak gRPC) | at once |

Scaling up TeaStore costs about 25 s plus minutes of warm-up; a controller that plans ahead gains from it. Data: `results/calibration/scaleup_*`.

## Interference

All five entries loaded together raise P95 against each alone at the same rates (Poisson, 30/60/80% of one-copy capacity): TeaStore about 2x at the highest step (12 to 24 ms), image recognition 2.1x (227 to 488 ms), hotel +60%, thumbnailer and compression about +16%. Trace scaling uses capacities measured together. Data: `results/calibration/together_*`.

## LLM decision time

One joint decision for all 8 scaled services (prompt about 1,400 tokens: 5 intervals of history per service, SLOs, cluster budget; answer forced to a JSON schema with one replica count per service). Temperature 0, 5 scenarios. Script: `tools/llm_decision_timing.py`; data: `results/llm_timing/`.

| Model | Thinking off | Thinking on | Replicas at the cluster-peak scenario (soft budget 24) |
|---|---|---|---|
| Qwen3.5-27B | 3.4-4.4 s, 79 output tokens | 197-221 s, 4,967-5,553 output tokens | 28 off, 32 on |
| TelecomGPT-R1-27B | 3.8-4.7 s, 79 output tokens | 9-25 s, 199-528 output tokens | 28 off, 24 on |

- Thinking off: both fit easily in the 60 s interval (under 5 s) and gave identical decisions in all 5 scenarios. All answers were valid.
- Thinking on: Qwen3.5-27B does not fit a 60 s interval and uses about 7,000 of the 8,192 context tokens. TelecomGPT fits. The thinking ablation needs a fair setup for Qwen (longer interval or a thinking-token cap). One repeat per scenario only: an anecdote, not a result.
