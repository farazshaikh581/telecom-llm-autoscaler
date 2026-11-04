#!/bin/bash
# Build the SeBS function images from SeBS at a pinned commit and push them to the in-cluster
# registry (localhost:32000). Run on the control node, never while a harness run is live (heavy disk I/O).
# ONLY="dynamic-html graph-bfs" builds just those.
set -euo pipefail

SEBS_COMMIT=b37f475          # spcl/serverless-benchmarks, 2026-09-02
TAG=${TAG:-v1}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

git clone -q --filter=blob:none --no-checkout https://github.com/spcl/serverless-benchmarks.git "$WORK/sebs"
git -C "$WORK/sebs" checkout -q "$SEBS_COMMIT" -- benchmarks/wrappers/local/python benchmarks/200.multimedia/210.thumbnailer \
  benchmarks/300.utilities/311.compression benchmarks/400.inference/411.image-recognition \
  benchmarks/100.webapps/110.dynamic-html benchmarks/500.scientific/501.graph-pagerank benchmarks/500.scientific/503.graph-bfs

build() {  # name, sebs benchmark path, extra build args
  local name=$1 src=$2; shift 2
  [ -n "${ONLY:-}" ] && ! [[ " $ONLY " == *" $name "* ]] && return 0
  local ctx="$WORK/ctx-$name"
  mkdir -p "$ctx/function"
  cp "$HERE/Dockerfile" "$HERE/server.py" "$ctx/"
  cp "$WORK/sebs/benchmarks/$src/python/function.py" "$ctx/function/"
  cp "$WORK/sebs/benchmarks/wrappers/local/python/storage.py" "$ctx/function/"
  [ -f "$WORK/sebs/benchmarks/$src/python/imagenet_class_index.json" ] && \
    cp "$WORK/sebs/benchmarks/$src/python/imagenet_class_index.json" "$ctx/function/"
  [ -d "$WORK/sebs/benchmarks/$src/python/templates" ] && cp -r "$WORK/sebs/benchmarks/$src/python/templates" "$ctx/function/"
  touch "$ctx/function/__init__.py"
  docker build -q -t "localhost:32000/sebs-$name:$TAG" "$@" "$ctx"
  docker push -q "localhost:32000/sebs-$name:$TAG"
}

build thumbnailer 200.multimedia/210.thumbnailer
build compression 300.utilities/311.compression
build imgrec      400.inference/411.image-recognition --build-arg TORCH=1
# Edge-range functions (2026-10-01). SeBS asks for jinja2>=2.10.3 unpinned; pinned here for repeatable builds.
build dynamic-html   100.webapps/110.dynamic-html    --build-arg PIP_EXTRA="jinja2==3.1.4"
build graph-bfs      500.scientific/503.graph-bfs    --build-arg PIP_EXTRA="igraph==0.11.4"
build graph-pagerank 500.scientific/501.graph-pagerank --build-arg PIP_EXTRA="igraph==0.11.4"
