# Scenarios: workloads and traces

Availability checked 2026-09-24.

## Workloads

| Step | Workload | Source | Status | Work needed |
|---|---|---|---|---|
| 1 | TeaStore (web shop, several services, we scale one entry tier) | DescartesResearch/TeaStore | Stable (last push 2025-01), widely used in autoscaling papers, Kubernetes manifests included | Deploy, pick the scaled service, set SLO |
| 2 | SeBS functions: thumbnailer, image recognition, compression | spcl/serverless-benchmarks | Active (2026-09) | Done 2026-09-24: wrapped as HTTP containers, deployed and verified. See `workloads/sebs/README.md` |
| 3 | DeathStarBench hotel reservation | delimitrou/DeathStarBench | Stable (last push 2024-07), Kubernetes manifests included | Deploy, pick 3-4 stateless services to scale |

## Deployment notes and capacity (2026-09-24)

Manifests: upstream files passed through `tools/prep_manifests.py` (pods pinned to workers, PVCs replaced by emptyDir, namespace set).
- TeaStore: `examples/kubernetes/teastore-clusterip.yaml` at commit d9274f5. Images are untagged (`latest`); pin by digest before the main runs.
- Hotel reservation: `hotelReservation/kubernetes/` at DeathStarBench commit 6ecb097.

Upstream problems found and fixed:
1. Hotel manifests start services as `./frontend` etc. The published image (`0.3.5`, identical to `latest`) installs binaries in `/go/bin` on PATH. All 8 services crash-looped. Fix in `prep_manifests.py`: start by name, pin tag `0.3.5`.
2. The hotel user service stores usernames as `fmt.Sprintf("Cornell_%x", suffix)`, which hex-encodes the number string (user 1 is `Cornell_31`). DeathStarBench's own wrk2 script logs in as `Cornell_1`, and every login and reservation fails. Our load generator must use the stored form. Passwords are unchanged (the number repeated 10 times).

3. TeaStore defines no readiness probe. A new front-end copy refuses connections for about 4 s and answers its first request only after about 24 s (app deployment, page compilation), while Kubernetes marked it Ready after 2 s and routed traffic to it. Fix in `prep_manifests.py`: HTTP readiness probe on the front end (lenient: 5 s timeout, 3 failures). A new copy is now Ready after 24-26 s.
4. TeaStore publishes only `latest`, and pods pulled it again on every new copy (`imagePullPolicy: Always`). Fix: all seven images pinned by digest, `IfNotPresent`.
5. TeaStore's registry loses its state when it restarts, and services do not register again. Restarting every TeaStore service at once left an empty registry and a front end that hung. Rule: restart in order (registry, db, persistence, then auth, image, recommender, front end) and warm up before scoring. Note: the registry names services `tools.descartes.teastore.<name>`.

Both apps verified working: TeaStore home and category pages return 200. Hotel search, recommendations, login, and reservation return 200 with correct bodies.

Idle footprint:

| App | Pods | CPU | Memory |
|---|---|---|---|
| TeaStore | 7 | 132m | 3.7 GiB (6 Java services, about 0.5-0.65 GiB each) |
| Hotel reservation | 19 | 36m | 0.4 GiB |

Workers after both: 57-188m CPU and 2.2-3.8 GiB memory used each, out of 4 CPU and 15.5 GiB. Disk: 1.4-1.8 GB of images per worker, about 24 GB still free. Total worker capacity: 16 CPU, 62 GiB.

Estimates for the rest: SeBS functions need 256 MB (thumbnailer, compression) and 768 MB (image recognition) per replica, plus one MinIO instance. Image recognition runs ResNet on CPU and will be the heaviest per request.

Open design point: TeaStore sets no CPU requests, and the hotel app sets 100m on every container. HPA needs CPU requests on every scaled service. Set one consistent request and limit per scaled service, from the calibration run, and use the same values for every method.

Next capacity step: load calibration. Find the requests per second one replica of each scaled service sustains within its SLO. That sets the trace scale factors and the maximum replicas.

## Traces

| Step | Trace | Granularity | Size and access | License |
|---|---|---|---|---|
| 1 | Wikimedia pageviews (dumps.wikimedia.org/other/pageviews) | Hourly | About 47 MB per hour, public | CC0 |
| 2 | Huawei Cloud 2023 serverless (sir-lab/data-release) | Per second and per minute, per function, 235 days | Zip archives, extract selectively | CC BY 4.0 |
| 3 | Alibaba microservices 2022 (alibaba/clusterdata) | 60 s call rates per service (min-max normalized), 13 days | About 2 TB total, about 10 GB per hour compressed. Download selectively and keep only our services | See repo |
| Stress | WorldCup98 (Internet Traffic Archive) | Per request | Public | See archive |

Notes:
- The classic Wikibench per-request Wikipedia trace is no longer downloadable (site empty, trace directories 404). Wikimedia pageviews replace it.
- WorldCup98 contains real flash crowds (match days). The flash-crowd test can replay one instead of a synthetic spike.
- Hourly pageviews are coarse against a 30 s control interval. Replay uses time compression (for example one day in one hour) with interpolation. The compression factor is a design choice to record.
- Alibaba call rates are normalized to 0-1. They need a scale factor to become requests per second.
