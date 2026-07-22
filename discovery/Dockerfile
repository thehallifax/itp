FROM python:3.12-slim
WORKDIR /app
COPY discovery/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collectors /app/collectors
COPY telemetry /app/telemetry
COPY grafana /app/grafana
COPY dashboards /app/dashboards
COPY VERSION SCHEMA_VERSION /app/
COPY discovery/discover.py .
CMD ["python", "/app/discover.py", "run", "--config", "/app/config.yml"]
