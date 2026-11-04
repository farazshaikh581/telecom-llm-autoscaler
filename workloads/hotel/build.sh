#!/bin/bash
# Build the hotel-reservation image with the seed-once fix and push it to the in-cluster registry.
# Upstream: DeathStarBench master 6ecb097 (2024-06-27), the source of the published 0.3.5 image.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$HOME/build}
TAG=localhost:32000/hotel-reservation:0.3.5-seedonce
mkdir -p "$WORK" && cd "$WORK"
[ -d DeathStarBench ] || git clone -q --filter=blob:none --sparse https://github.com/delimitrou/DeathStarBench.git
cd DeathStarBench && git sparse-checkout set hotelReservation && git checkout -q 6ecb097
git checkout -q -- hotelReservation/cmd && git apply "$HERE/seed_once.patch"
cd hotelReservation && docker build -q -t "$TAG" . && docker push "$TAG"
