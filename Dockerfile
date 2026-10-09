FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=prod LABWORK_CONFIG=/runtime/config.json
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home labwork && mkdir /runtime && chown labwork /runtime
COPY manage.py runtime.py ./
COPY release.json ./
COPY labwork ./labwork
COPY records ./records
COPY templates ./templates
COPY packaging/docker/setup.py ./packaging/docker/setup.py
RUN LABWORK_CONFIG= APP_ENV=test RUNTIME_DIR=/tmp/collect python manage.py collectstatic --noinput && rm -rf /tmp/collect
USER 10001
CMD ["python", "runtime.py", "web"]
