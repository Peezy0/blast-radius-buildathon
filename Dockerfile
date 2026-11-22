# Exact image supplied by the official starter; Linux/amd64 in evaluation.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY selector.py /app/selector.py
COPY src /app/src
COPY data/map.json.gz /app/data/map.json.gz
# The slim image ships without bytecode: precompile the standard library and
# our modules so each run does not recompile them (CPU is scored).
RUN python -m compileall -q -j 0 /usr/local/lib/python3.13 /app/src /app/selector.py >/dev/null 2>&1 || true
USER 65534:65534
CMD ["python", "/app/selector.py", "--map", "/app/data/map.json.gz"]
