# Trace preparation

How the per-second target rates of every run are built (prepared 2026-09-25). Sources and licenses are in `scenarios.md`. Raw archives stay outside the repo; only derived shapes are kept in `data/traces/`.

## Pipeline

| Step | Script | Output |
|---|---|---|
| Wikipedia hourly views (en, user agents, 2025-07-01 to 09-30) | `tools/traces/fetch_wikipedia.py` | `wikipedia_en_hourly.csv` |
| Huawei 2023 per-second requests: profile functions | `tools/traces/profile_huawei.py` | `huawei_profile.csv` |
| Alibaba 2022 cluster CPU per minute (finds each day's busiest hour) | `tools/traces/alibaba_cluster_load.py` | `alibaba_cluster_cpu_minute.csv` |
| Choose dev, test, and training days (seed 2026) | `tools/traces/select_days.py` | `days.json` |
| Alibaba call rate per minute, 75 min per day, 5 candidate services | `tools/traces/alibaba_mcr_extract.py` | `alibaba_mcr/day_N.csv` |
| Hotel shape per second (service MS_42222, cleaned) | `tools/traces/alibaba_hotel_series.py` | `alibaba_hotel_second.csv` |
| Scale and write every run | `tools/traces/build_traces.py` | `runs/<split>_<n>.csv.gz`, `runs/index.json` |

## Days

Clean days only. 3 development and 5 test days are sampled with a fixed seed; the rest form the RL training pool.

| Trace | Excluded | Dev | Test | Training pool |
|---|---|---|---|---|
| Wikipedia | 2 (peak/trough above 2.2 or total 25% off the median) | 07-16, 08-10, 09-04 | 07-14, 07-29, 09-05, 09-17, 09-23 | 82 days |
| Huawei (fns 60, 183, 190) | 52 (a function absent, or its total outside 0.5-2x its 7-day median; fn 190 ends after day 163) | 015, 049, 131 | 013, 037, 132, 151, 157 | 81 days |
| Alibaba | 3 (days 0, 1, 5: missing minutes) | 3, 8, 12 | 4, 6, 7, 10, 11 | 2 days (2, 9) |

Run `test_n` pairs the n-th test day of each trace. The Alibaba training pool is small (2 days); RL training relies on the simulator and the other two traces for variety. This is a limitation to state.

## Shapes

Each shape is normalized so its busiest 60-s window in the scored hour equals 1.

- **TeaStore (Wikipedia):** the previous day's last 6 h as warm-up, then the test day's 24 h, at 150 s per trace hour. Linear interpolation between hour midpoints. One day's cycle is compressed into the scored hour.
- **Functions (Huawei):** the day's busiest hour (all three functions combined), preceded by its 15 minutes, at real time. Per-second data, so the bursts are real.
- **Hotel (Alibaba):** the day's busiest hour by cluster CPU, preceded by 15 minutes, at real time. Per-minute data, interpolated between minute midpoints.

## Alibaba: what we found

1. **One candidate is unusable.** MS_61753 repeats each pod's row 16,384 or 65,536 times per minute (same value; a join error in the published table). Its per-minute sum follows the row count. MS_42222 has one row per pod and minute (1,852 pods) and is used.
2. **One-minute jumps are an artifact.** On 8 of 10 days, all five services jump 1.2-3.7x in the same single minute and fall back the next. In the jump minute of day 3, about a quarter of the pods report almost exactly 4x their rate, the others and the response time (about 189 ms) do not change. A real burst would raise response time. Rule: a minute above 1.15x the median of the 7 minutes around it is replaced by that median (normal change is under 2% per minute). 1-2 minutes are replaced per day, none on days 7 and 11.
3. **The hotel load is nearly flat within the hour.** After cleaning: coefficient of variation 1-3%, busiest minute 1.04-1.09x the mean, a slow 8% decline over the hour. The hotel app is the steady neighbour of the mix. More variety would need whole days (about 84 GB each), which is not worth it.

The mix therefore has three distinct patterns: a compressed daily cycle (TeaStore), per-second bursts (functions), and a steady load (hotel).

## Scaling

**Update 2026-09-25 (afternoon):** every rate below is multiplied by `load_scale` = 0.45 in the final traces (`decisions.md`); the table shows values before that step. Peak rates after it: TeaStore 202 req/s in every run, hotel 741-910 req/s, thumbnailer 36-45, image recognition 10-13, compression 3.5-4.3 req/s.

Rule 4 in `decisions.md`, implemented in `build_traces.py`. The unit is a copy (0.5 CPU requested). Each app needs the same K copies at its own busiest minute (TeaStore capped at 450 req/s, which is 3.2 copies). K is solved per run so the combined busiest minute needs 30 copies, 125% of the 24 that fit on the workers.

| Run | K | Busiest minute, most copies for one service | CPU at combined peak (16 on workers) | Combined copies, scored hour: mean / median / min | Time above 24 copies |
|---|---|---|---|---|---|
| dev_0 | 8.0 | 8.0 | 19.3 | 18.9 / 18.0 / 16.7 | 2% |
| dev_1 | 8.2 | 8.2 | 19.7 | 20.5 / 19.9 / 18.5 | 3% |
| dev_2 | 9.4 | 9.4 | 20.5 | 20.0 / 18.9 / 17.5 | 6% |
| test_0 | 8.2 | 8.2 | 19.9 | 19.5 / 18.6 / 17.1 | 3% |
| test_1 | 7.7 | 7.7 | 18.9 | 17.9 / 17.0 / 15.6 | 2% |
| test_2 | 9.4 | 9.4 | 20.5 | 20.1 / 19.0 / 17.5 | 6% |
| test_3 | 9.4 | 9.4 | 20.5 | 20.2 / 19.0 / 17.5 | 7% |
| test_4 | 9.2 | 9.2 | 20.4 | 19.6 / 18.6 / 17.0 | 5% |

Hotel copies at the busiest minute (test_2): frontend 1.9, search 1.5, reservation 4.5, rate 1.5.

- Every service needs at most 9.4 copies, below the 12 per service the controllers may set.
- CPU at the combined peak is 118-128% of the worker CPUs (rule 4: about 120-130%).
- The copy counts are the minimum at SLO capacity. A controller that keeps headroom asks for more, and meets the 24-copy limit for longer than the 2-7% of the hour shown here.
