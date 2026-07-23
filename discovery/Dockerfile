FROM python:3.12-slim
WORKDIR /app
COPY discovery/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collectors /app/collectors
COPY itp_profiles /app/itp_profiles
COPY profiles /app/profiles
COPY telemetry /app/telemetry
COPY analysis /app/analysis
COPY grafana /app/grafana
COPY dashboards /app/dashboards
COPY VERSION SCHEMA_VERSION /app/
COPY discovery/discover.py .
CMD ["python", "/app/discover.py", "run", "--config", "/app/config.yml"]
