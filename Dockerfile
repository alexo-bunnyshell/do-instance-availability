FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends nginx cron && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY check_availability.py .
COPY entrypoint.sh /entrypoint.sh
COPY nginx.conf /etc/nginx/sites-enabled/default
RUN chmod +x /entrypoint.sh

# Cron job: run checker every 30 minutes
RUN echo "*/30 * * * * cd /app && python3 check_availability.py >> /var/log/checker.log 2>&1" > /etc/cron.d/checker && \
    chmod 0644 /etc/cron.d/checker && \
    crontab /etc/cron.d/checker

RUN mkdir -p /app/data /var/www/html

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
