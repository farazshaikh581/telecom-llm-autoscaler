# Experiment harness

Built 2026-09-25. Code in `harness/`, settings in `config/harness.yaml`.

## Parts

| Part | File | What it does |
|---|---|---|
| Load generator | `harness/loadgen/` (Go) | Replays a run's per-second rates for the 5 entries as Poisson arrivals, from a fixed seed (every controller gets the same requests). One gzip CSV line per request. Live stats at `/stats?window=N`. Closes idle connections every second so new copies get traffic. Ramps from 20% to 100% of the trace over the first 5 min. |
| Orchestrator | `harness/run.py` | Preflight, reset, start, 60 s decision loop, stop, analysis. |
| Recorder | `harness/recorder.py` | Load stats, replicas and pod states every 5 s; metrics-server CPU and memory every 15 s; GPU power every 1 s; host RAPL every 5 s. |
| Observation | `harness/observe.py` | The one observation every controller gets: per entry rate, errors, latency percentiles (last 60 s); per service replicas, pending pods, CPU. Never the trace's planned rate. |
| Controllers | `harness/controllers/` | Common interface (`decide(obs)` returns replicas per service). Done: static, native HPA, LLM (joint or per service, JSON schema, temperature 0). To add: KEDA, predictive HPA, RL, AWARE. |
| Registry cleaner | `harness/registry_cleaner.py` | Removes dead hotel instances from consul every 5 s (see below). |
| Analysis | `harness/analyze_run.py` | KPIs (`summary.json`) and a per-minute timeline (`timeline.csv`). |
| Model switch | `harness/llm_serve.sh` | Serves one vLLM container at a time. |

The host's ssh address and the VM-to-role map are read from a local folder outside the repo.

## Load generator against k6

Same steps as the calibration spot check, one entry alone, one copy (`results/harness/loadgen_validation/`). P95 in ms, k6 values in brackets.

| Entry | Low | Middle | High |
|---|---|---|---|
| Hotel, 100 / 300 / 500 req/s | 9.2 (9.5) | 9.7 (9.6) | 12.0 (11.4) |
| TeaStore, 50 / 100 / 150 req/s | 13.9 (14.0) | 15.5 (15.0) | 18.2 (16.5) |
| Image recognition, 4 / 6 / 7 req/s | 318 (302-347) | 531 (431-557) | 718 (799) |

P99 at hotel's knee (500 req/s) varies 26-41 ms run to run with identical arrivals, with or without connection recycling.

## What the smoke tests found and fixed

Static controller, trace dev_0, 5-15 min each (`results/runs/smoke_*`).

1. **Cold start at full load.** Every app starts near its peak on fresh copies; TeaStore's new Java copies fail their readiness checks and drop out one after another. Fix: 5-min ramp at the start of the (unscored) warm-up.
2. **Hotel sample data inserted on every start.** Each hotel service inserts its test data into MongoDB whenever a copy starts, with no check (upstream code, unchanged since 2024). After a day of scaling: 240 hotels instead of 80, 4,640 capacity records instead of 80. Every scale-up made hotel slower (reservation needed over 10x more CPU per request). Fix: `workloads/hotel/seed_once.patch` (insert only into an empty collection), image `hotel-reservation:0.3.5-seedonce` in the in-cluster registry. The harness preflight refuses to run if the data is duplicated.
3. **Hotel registry keeps dead copies.** Services register in consul on start and never deregister, with no health checks: 58 reservation entries for 4 live copies. Callers kept sending requests to dead addresses. Fix: the registry cleaner.
4. **Bookings pile up.** 0.5% of hotel requests make a booking, and bookings are never removed (29,040 in one 15-min test). Fix: every run starts from the seed bookings, with the booking cache restarted. Restarting the cache left reservation holding dead pooled connections; it panicked on a broken pipe about 60 s later (twice on Sep 25, both inside the warm-up). Reservation is now restarted right after the cache, before t = 0. The frontend is restarted too since 2026-09-28: in test_phpa_t3 its gRPC resolver stayed stuck on reservation copies that were gone (log: "bad resolver state"), and every search request (60% of hotel traffic) failed with HTTP 500 for the whole run; that run is invalid and is repeated.
5. **Hotel crashes on a slow cache.** The rate and reservation services panic on any memcached error. Under CPU throttling they miss the 2 s deadline and crash-loop. Fixes: `GOMAXPROCS=1` (matching the 1-CPU limit) and the app's own `MEMC_TIMEOUT=10`; memcached connection limit 4,096 (was 1,024).
6. **Analysis bug.** Failed requests counted as infinitely slow made interval percentiles undefined, so failing minutes never counted as violations. Fixed with a finite stand-in (1e9 ms).
7. **Host swap.** See `cluster.md` (per-VM cgroup swappiness).
8. **Registry cleaner removed new copies (found 2026-09-30).** A new hotel copy registers in consul about 1 s after it starts, often before the API server shows it Ready. The cleaner treated every address without a Ready pod as dead, removed the new entry, and the copy never registered again: one copy then took all the traffic of its service (base run on the second testbed: 1 of 4 reservation copies at its 1-CPU limit, hotel P95 9.2 s). The same race hit about half of the phase-1 runs. Fix: a copy that is not terminating counts as live whether Ready or not; an address held by no pod is removed only after 30 s; a terminating copy is still removed at once.

## Load level (open)

Calibrated per-copy capacities are too optimistic when the whole cluster is busy (both hyperthreads of every core in use), and TeaStore's unscaled persistence service limits it near 300 req/s when the other apps run.

| Test | Copies | Result |
|---|---|---|
| 100% load, sized from calibration | 23 | TeaStore collapses above ~340 req/s; everything over SLO |
| 60% load, sized from calibration | 14 | No errors, stable; all entries 2-10x over SLO |
| 60% load, headroom | 22 | Hotel 29-35 ms (SLO 27), thumbnailer 220-620 ms (200), TeaStore 150-1,700 ms (39) |
| 45% load, headroom | 22 | running |
