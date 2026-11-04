# SeBS functions on Kubernetes

Three functions from SeBS (spcl/serverless-benchmarks, commit b37f475) run as plain Deployments:

| Service | SeBS benchmark | Memory (from SeBS config) |
|---|---|---|
| `thumbnailer` | 210.thumbnailer | 256 Mi |
| `compression` | 311.compression | 256 Mi |
| `imgrec` | 411.image-recognition (ResNet-50, CPU) | 768 Mi |

SeBS has no Kubernetes target (it supports AWS, Azure, GCP, OpenWhisk, and a local Docker mode). We reuse its function code and storage wrapper unchanged, with a small HTTP wrapper (`server.py`, same request and response format as SeBS's local server).

Design:
- One gunicorn sync worker per replica: one request at a time, like a FaaS instance with concurrency 1. `OMP_NUM_THREADS=1` keeps torch at one thread (CPU limit is 1).
- Storage: MinIO on the control node (external storage service, like S3). A sidecar deletes function outputs older than 1 minute every 30 s.
- Input data: spcl/serverless-benchmarks-data at commit 523644d, loaded by `load-data.yaml` with the bucket layout SeBS's `input.py` files use.
- Images: built by `build.sh`, stored in the in-cluster registry (`../infra/registry.yaml`, `localhost:32000`). Nothing is pushed outside the cluster.

## Deploy

```bash
kubectl apply -f ../infra/registry.yaml
TAG=v3 ./build.sh
kubectl create namespace sebs
# create Secret "minio-creds" in namespace sebs with two keys, `user` and `password`, set to random values
kubectl apply -f sebs.yaml
kubectl apply -f load-data.yaml
```

## Problems found and fixed

1. SeBS's storage wrapper calls `list_objects_v2`, which minio-py removed in 7.0. SeBS installs minio unpinned, and compression fails with a 500 on current versions. Fix: pin `minio==6.0.2` (image tag `v2`, and `v3` for the fixes below).
2. The official `minio/minio` server images are no longer on Docker Hub, and the MinIO project is archived (2026-04). We use `bitnamilegacy/minio:2025.7.23-debian-12-r5` (the real MinIO server, frozen). Storage is not a scaled component.
3. Readiness probe (ours): an HTTP probe on `/alive` has to wait for the single worker. Under load it timed out, Kubernetes marked the pod unready, and every request was refused ("connection refused") until the next probe passed. Calibration measured probe flapping instead of queueing. Fix: `tcpSocket` probe (the gunicorn master accepts connections while the worker is busy, and requests queue in order). Results from the HTTP-probe runs are kept in `results/calibration/superseded_http_probe/`.
4. `/tmp` leak (SeBS functions): compression downloads about 10 MB and writes a zip to `/tmp` per request, under unique names, and never deletes them (image recognition does the same with small images). In a long-lived container this filled a worker's 28 GB after about 1,700 compression requests; the node went into DiskPressure and the pod was evicted. Fix (image tag `v3`): `server.py` deletes whatever a request created in `/tmp` after it returns, like a fresh FaaS sandbox. Verified: `/tmp` stays empty, results unchanged. The affected run is kept in `results/calibration/superseded_tmp_leak/`.

## Measured (one call each, 2026-09-24)

| Function | Cold | Warm | Warm breakdown |
|---|---|---|---|
| thumbnailer | 59 ms | 45 ms | 32 ms resize, 8 ms download, 3 ms upload |
| compression | 445 ms | 444 ms | 256 ms zip, 132 ms download (39 files), 53 ms upload |
| imgrec | 1,817 ms | 118 ms | 111 ms inference; the cold call spends 1.3 s loading the model |

Image recognition returns `sports_car` (class 817) for SeBS's test image, as SeBS's own validation expects. A new imgrec replica pays about 1.8 s on its first request (model load). This cold start is part of the workload, not an artifact.
