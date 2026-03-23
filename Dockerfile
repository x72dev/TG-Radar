FROM python:3.11-slim

LABEL maintainer="TG-Radar"
LABEL description="TG-Radar - Telegram Keyword Monitor"

# Install git (needed for plugin updates)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/
COPY config.example.json config.schema.json ./

# Copy plugin repo if present
COPY plugins-external/ plugins-external/

# Create runtime directories
RUN mkdir -p runtime/logs/plugins runtime/sessions runtime/backups configs

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["run"]
