#!/bin/bash
set -e

# Apply database migrations. chatbot overrides migrate to hold a database lock,
# so instances that start together take turns instead of racing.
echo "Applying database migrations..."
python manage.py migrate --noinput

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Load your bots (and ensure models exist)
echo "Loading bots..."
python manage.py load_bots

# Create superuser on first deploy if DJANGO_SUPERUSER_PASSWORD is set
if [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "Creating superuser..."
  python manage.py createsuperuser --noinput \
    --username "${DJANGO_SUPERUSER_USERNAME:-admin}" \
    --email "${DJANGO_SUPERUSER_EMAIL:-admin@example.com}" || true
fi

echo "Starting Gunicorn..."
# Finally, run the command passed in the Dockerfile CMD or via `docker run`
exec "$@"
