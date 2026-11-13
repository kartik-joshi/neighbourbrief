# NeighbourBrief TO in a container. Publish the port on this computer only:
#   docker build -t neighbourbrief .
#   docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -v "$PWD/data:/app/data" neighbourbrief
# Then open http://127.0.0.1:8000 . The local model runs outside the container (Ollama on the host).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    LLM_BASE_URL=http://host.docker.internal:11434/v1 \
    ALLOW_CLIENTS=172.16.0.0/12,192.168.65.0/24
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir $(grep -v -E '^(pytest|playwright|openpyxl)' requirements.txt | sed 's/#.*//')
COPY app ./app
COPY config ./config
COPY data/demo_snapshot ./data/demo_snapshot
COPY data/translation_memory ./data/translation_memory
# Only the pre-made demo bulletins (see .dockerignore); a named volume on /app/data/bulletins starts with them.
COPY data/bulletins ./data/bulletins
RUN useradd --create-home nb && mkdir -p data/bulletins data/cache && chown -R nb /app/data
USER nb
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
# No --proxy-headers: it would also replace the peer address with X-Forwarded-For, and the local-only
# check must see the real peer (the tunnel on Docker's bridge). PUBLIC_DEPLOY reads only the https scheme.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
