# syntax=docker/dockerfile:1.7

# --- build stage -------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock

# --- runtime stage -----------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PATH="/opt/venv/bin:$PATH" \
    APP_HOME=/app

# `default-mysql-client` and `openssl` are required by the backup profile,
# which dumps and encrypts the database from this same image.
RUN apt-get update \
    && apt-get install --no-install-recommends -y tini curl openssl default-mysql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir ${APP_HOME} --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR ${APP_HOME}

COPY --chown=app:app alembic.ini pyproject.toml ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app
COPY --chown=app:app docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# The backup volume inherits this ownership when Docker initialises it, so the
# job can write its archives without running as root.
RUN chmod 0555 /usr/local/bin/entrypoint.sh \
    && python -m compileall -q app \
    && mkdir -p /backups \
    && chown -R app:app ${APP_HOME} /backups

USER app

ENV PYTHONPATH=${APP_HOME}

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["bot"]
