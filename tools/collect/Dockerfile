# syntax=docker/dockerfile:1
# Collection-only image (NOT the submission image). Same base digest as the
# official labelling environment so the same sqlglot tests run and skip.
# Build: docker build --secret id=ca,src=<CA bundle or empty file> -t br-collect .
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY requirements.lock /tmp/requirements.lock
RUN --mount=type=secret,id=ca,target=/run/ca.crt \
    if [ -s /run/ca.crt ]; then export PIP_CERT=/run/ca.crt; fi; \
    pip install --no-cache-dir -r /tmp/requirements.lock coverage==7.13.1
ENV PYTHONDONTWRITEBYTECODE=1 SKIP_INTEGRATION=0 COVERAGE_CORE=sysmon
