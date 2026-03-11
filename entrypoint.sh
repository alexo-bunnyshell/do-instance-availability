#!/bin/sh
set -e

# Run the checker once on startup
cd /app
python3 check_availability.py

# Copy generated dashboard to nginx root
cp /app/dashboard.html /var/www/html/index.html

# Set up cron to also copy after each run
cat > /etc/cron.d/checker <<'EOF'
*/30 * * * * cd /app && python3 check_availability.py && cp /app/dashboard.html /var/www/html/index.html >> /var/log/checker.log 2>&1
EOF
chmod 0644 /etc/cron.d/checker
crontab /etc/cron.d/checker

# Start cron
cron

# Start nginx in foreground
nginx -g 'daemon off;'
