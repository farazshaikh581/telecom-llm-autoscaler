# One image per SeBS function. Build context is prepared by build.sh.
FROM python:3.11-slim

# Common: storage client, HTTP server, image library (SeBS pins pillow 10.3.0).
# minio 6.0.2: SeBS's storage wrapper calls list_objects_v2, which minio-py removed in 7.0.
# SeBS installs minio unpinned, so its own local mode breaks on current versions.
RUN pip install --no-cache-dir "minio==6.0.2" "bottle==0.13.*" "gunicorn==23.*" "pillow==10.3.0"

# Function-specific packages (image recognition: CPU torch as pinned by SeBS for Python 3.11).
ARG TORCH=""
RUN if [ -n "$TORCH" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.0.0 torchvision==0.15.1 \
      && pip install --no-cache-dir numpy==1.24.0; \
    fi

# Other function-specific packages, as pinned by SeBS for Python 3.11 (e.g. "jinja2==3.1.4", "igraph==0.11.4").
ARG PIP_EXTRA=""
RUN if [ -n "$PIP_EXTRA" ]; then pip install --no-cache-dir $PIP_EXTRA; fi

WORKDIR /function
COPY server.py /function/server.py
COPY function/ /function/function/
RUN pip freeze > /function/pip-freeze.txt

# One request at a time per replica; torch uses one thread (pod CPU limit is 1).
ENV PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
EXPOSE 8080
CMD ["gunicorn", "-w", "1", "-k", "sync", "-b", "0.0.0.0:8080", "--timeout", "120", "server:app"]
