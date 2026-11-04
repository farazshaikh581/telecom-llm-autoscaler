"""HTTP wrapper for one SeBS function, served by gunicorn with one sync worker.

Adapted from SeBS dockerfiles/local/python/server.py. Same request and response
format: POST / with the function event as JSON. The function code and its storage
wrapper are SeBS's own, unchanged. One worker means one request at a time per
replica, like a FaaS instance with concurrency 1.
"""
import os
import shutil
import time
import uuid

import bottle

from function import function

app = bottle.Bottle()


@app.get("/alive")
def alive():
    return {"result": "ok"}


def _clean_tmp(before):
    """Delete what this request left in /tmp. SeBS functions write downloads and
    archives there under unique names and never delete them; in a long-lived
    container that fills the node's disk (compression: about 13 MB per request).
    Files that existed before the request (gunicorn's own) are kept. The image
    recognition model stays loaded in memory after its file is removed."""
    for name in set(os.listdir("/tmp")) - before:
        path = os.path.join("/tmp", name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


@app.post("/")
def process_request():
    before = set(os.listdir("/tmp"))
    begin = time.time()
    try:
        ret = function.handler(bottle.request.json)
    finally:
        end = time.time()
        _clean_tmp(before)
    return {
        "begin": begin,
        "end": end,
        "handler_ms": (end - begin) * 1000.0,
        "request_id": str(uuid.uuid4()),
        "result": {"output": ret},
    }
