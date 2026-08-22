# The paid export path.  manifold3d is a native C++ extension, so this cannot
# be a Worker -- that is the whole reason a container exists here.
FROM python:3.12-slim

# trimesh loads and exports STL without them, but numpy/scipy wheels expect a
# libstdc++ newer than slim ships, and manifold3d links against it directly.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first so the ~53MB of wheels stay cached when only the generator
# changes -- which is most of the time.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY kumiko_lamp.py ./
COPY sakura_v2_tile.py ./
COPY container/server.py ./container/server.py

# Nothing here writes outside a per-request temp dir, so it never needs root.
RUN useradd --create-home --uid 10001 kumiko
USER kumiko

ENV PORT=8080 \
    MAX_CONCURRENT=2 \
    BUILD_TIMEOUT_S=60 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "container/server.py"]
