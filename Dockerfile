FROM python:3.12.16-slim-bookworm

# git clones the target app at its tag; the generator libraries are what
# app-deploy-service's review docs promise generators (docs/app-manifest-review.md).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /processor
COPY requirements.txt /processor/requirements.txt
RUN pip install --no-cache-dir -r /processor/requirements.txt

COPY processor/ /processor/processor/
ENV PYTHONPATH="/processor" \
    PYTHONUNBUFFERED=1 \
    GIT_TERMINAL_PROMPT=0

ENTRYPOINT ["python3", "-m", "processor.main"]
