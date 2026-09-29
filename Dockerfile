# callscout as a service, for n8n's HTTP Request node to call over the
# network. Includes headless Chromium so the browser-fallback fetch path
# (JS-rendered funder portals) works in production, not just locally.
#
# Build:  docker build -t callscout .
# Run:    docker run -p 8000:8000 -e CALLSCOUT_API_KEY=changeme callscout
FROM python:3.11-slim

# Playwright's own installer pulls the OS packages Chromium needs
# (--with-deps below), so no manual apt-get list to maintain here.
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Install callscout itself plus the browser extra, then Chromium + its
# system deps. Split into two RUN layers so `pip install` and the (slower,
# larger) browser install cache independently during rebuilds.
RUN pip install --break-system-packages -e ".[browser]"
RUN playwright install --with-deps chromium

# Not baked in by default: ANTHROPIC_API_KEY (LLM fallback) and
# CALLSCOUT_API_KEY (auth) are set at deploy time as environment variables,
# never committed to the image.

EXPOSE 8000

CMD ["uvicorn", "callscout.api:app", "--host", "0.0.0.0", "--port", "8000"]
