# Hotel reservation (DeathStarBench) fixes

The published image `deathstarbench/hotel-reservation:0.3.5` inserts its test data into MongoDB every time a service copy starts (`InsertMany` with no check in `cmd/*/db.go`). In an autoscaling study every scale-up duplicates the data: after a day, 240 hotels instead of 80 and 4,640 capacity records instead of 80, and reservation needed over 10x more CPU per request.

`seed_once.patch` inserts the data only when the collection is empty. `build.sh` builds `localhost:32000/hotel-reservation:0.3.5-seedonce` from the same upstream commit as 0.3.5 and pushes it to the in-cluster registry. `tools/prep_manifests.py` points every hotel service to it.

Deploying the fix on a running app: scale the hotel services to 1 copy, drop the six hotel databases, switch the image (each service seeds once), restart the memcached caches, then scale back up. Other hotel fixes (registry cleaner, per-run booking reset, memcached and Go settings) are described in `docs/harness.md`.
