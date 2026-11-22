# syntax=docker/dockerfile:1

# --- build: produce the wheel -------------------------------------------------------------------
FROM python:3.13-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY examples ./examples
RUN python -m build --wheel --outdir /dist

# --- runtime: HTTP mode, non-root ---------------------------------------------------------------
FROM python:3.13-slim
LABEL org.opencontainers.image.title="brand-kit-mcp" \
      org.opencontainers.image.description="MCP server that serves brand identity and exports DTCG design tokens" \
      org.opencontainers.image.source="https://github.com/Alejandro-Cuautli/brand-kit-mcp" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BRAND_KIT_DIR=/brands

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin brandkit
COPY --from=build /dist/*.whl /tmp/
RUN pip install /tmp/*.whl && rm -f /tmp/*.whl

# Example brands; mount your own read-only over /brands.
COPY examples/brands /brands

USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

ENTRYPOINT ["brand-kit-mcp"]
CMD ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
