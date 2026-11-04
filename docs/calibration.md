# Calibration results

Measured 2026-09-24 on the final setup: 1-CPU limit and 500m request per scaled service, copies spread over workers, SeBS images v3. Load from k6 on the control node. Every request is logged; tables and raw data are in `results/calibration/`.

## Per user-facing entry (one copy of each scaled service)

| Entry | No-queue P95 (evenly spaced, low load) | SLO at k = 3 | Capacity within SLO, random (Poisson) arrivals | Breaks at | Bottleneck at the knee |
|---|---|---|---|---|---|
| TeaStore front end | 13 ms (25-50 req/s) | 39 ms | about 150 req/s (P95 13 ms) | 200 req/s (P95 175 ms) | front end, 1.0 CPU |
| Thumbnailer | 66 ms | 200 ms | about 12.5 req/s (P95 180 ms) | 15 req/s (P95 306 ms) | function, 0.9 CPU |
| Compression | 439 ms | 1.3 s | about 1.1 req/s (P95 1.0 s at 1 req/s) | 1.5 req/s (P95 2.6 s) | function, 0.8 CPU |
| Image recognition | 133 ms | 400 ms | about 4.5 req/s (P95 347 ms at 4 req/s) | 6 req/s (P95 557 ms) | function, 1.0 CPU |
| Hotel frontend (end to end) | 9 ms (100-200 req/s) | 27 ms | about 500 req/s (P95 11 ms) | 600 req/s (P95 137 ms) | reservation, 0.9 CPU |

"No-queue P95" is the SLA base (rule 3 in `decisions.md`). Capacities with random arrivals are the per-copy figures used to scale the traces (rule 4). For the hotel app, capacity is for one copy each of frontend, search, reservation, and rate; reservation saturates first.

## What calibration changed along the way

| Finding | Effect | Action |
|---|---|---|
| HTTP readiness probe blocked by the single busy worker | Kubernetes refused all traffic under load | TCP probe (SeBS README, problem 3) |
| SeBS functions never delete `/tmp` files | Compression filled a worker disk, 39 evictions | Wrapper cleans `/tmp` per request (problem 4) |
| Two copies on one worker share cores through hyperthreads | Image recognition P95 3.7 s vs 370 ms spread | Spread rule for every scaled service |
| Random arrivals queue far earlier than evenly spaced ones | Compression P95 at 1 req/s: 1,040 vs 439 ms | Capacities measured with Poisson arrivals |
| TeaStore sets no CPU limits | Front end used 2.6 CPUs | 1-CPU limit on every scaled service |
| Failed requests in latency percentiles | P50 looked too low under overload | Latency from successful requests only |

Storage (MinIO) never exceeded 0.33 CPU in any run and is not a bottleneck.

## Spot check after the host swap fix (2026-09-25)

The calibration ran while the host had 14 GiB of old swap (`cluster.md`). Three Poisson baselines were repeated at one copy after the fix (`results/calibration/spotcheck_20260925/`). P95 in ms. For image recognition the rate shown is what was actually sent (then vs now), since random arrivals vary around the nominal rate; the others are nominal rates.

| Entry | Rate | P95 then | P95 now |
|---|---|---|---|
| Hotel | 100 / 300 / 500 req/s | 9.0 / 9.5 / 10.7 | 9.5 / 9.6 / 11.4 |
| TeaStore | 50 / 100 / 150 req/s | 12.4 / 12.6 / 13.4 | 14.0 / 15.0 / 16.5 |
| Image recognition | 3.98 vs 4.40 req/s | 347 | 302 |
| Image recognition | 6.38 vs 6.03 req/s | 557 | 431 |
| Image recognition | 8.12 vs 7.88 req/s | 1,952 | 1,857 |

Result: no change that matters. Hotel is within 1 ms. Image recognition matches at equal sent rates. TeaStore is 2-3 ms slower at P95 (its tail at P99 is lower: 17-21 vs 18-41 ms); it had been idle all night, and its Java code warms up over minutes. All stay well inside their SLOs (27 ms, 39 ms, 400 ms). The calibration numbers stand.

Note for the analysis: with random arrivals the sent rate of a 60 s step can differ from the nominal rate by 10% or more (7 req/s nominal sent 7.88 req/s here). Near a knee this moves the tail a lot. Comparisons use the rate actually sent.

## SLO base in the shared cluster (2026-09-25)

The SLO base is now measured in the setting it applies to (decision of 2026-09-25, afternoon). All five entries run together at the 45% load level with evenly spaced arrivals and 22 copies (3 each for TeaStore and the functions; hotel 2/2/4/2), so per-copy load stays at or below about half the calibrated capacity. P95 over minutes 5-15, after the ramp (`results/runs/slo_shared_base_even/`).

| Entry | Load (req/s) | P95 alone (ms) | P95 shared (ms) | Ratio | SLO, k = 3 (ms) |
|---|---|---|---|---|---|
| TeaStore | 188 | 13 | 47.9 | 3.7 | 144 |
| Hotel | 744 | 9 | 16.5 | 1.8 | 50 |
| Thumbnailer | 12.2 | 66 | 111 | 1.7 | 333 |
| Image recognition | 3.1 | 133 | 210 | 1.6 | 629 |
| Compression | 1.1 | 439 | 846 | 1.9 | 2,536 |

Neighbouring pods slow every entry by 1.6-1.9x. TeaStore's 3.7x also includes its unscaled persistence service, shared by every front-end copy. Per-minute P95 was steady (compression had occasional 1.0-1.3 s minutes).
