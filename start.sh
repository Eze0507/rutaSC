#!/usr/bin/env bash
# start.sh
set -o errexit

# 1. Recopilar archivos estáticos
python manage.py collectstatic --no-input

# 2. Ejecutar migraciones
python manage.py migrate

# 3. Arrancar Gunicorn en el puerto que Render asignará (10000)
gunicorn rutasc.wsgi:application --bind 0.0.0.0:10000
