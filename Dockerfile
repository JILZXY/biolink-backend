# ==============================================================================
# BioLink Backend - Imagen de Produccion (API REST + Outbox Worker)
# Nota: Python 3.14-slim como entorno objetivo reproducible.
# ==============================================================================

FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias del sistema minimas para diagnostico y usuario no privilegiado
RUN groupadd --system biolink && useradd --system --gid biolink biolink

COPY requirements.lock pyproject.toml ./
RUN pip install --no-deps -r requirements.lock

COPY src/ ./src/
COPY database/ ./database/

RUN pip install --no-deps --no-build-isolation -e . && chown -R biolink:biolink /app

USER biolink

EXPOSE 8000

CMD ["uvicorn", "biolink_backend.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
