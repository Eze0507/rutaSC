#!/usr/bin/env bash
# Script de construcción para Render
# NOTA: Antes del primer deploy, habilitar PostGIS manualmente en el dashboard
#       de Render (pestaña Shell de la base de datos):
#           CREATE EXTENSION IF NOT EXISTS postgis;
set -o errexit  # detener si algún comando falla

# 1. Instalar dependencias Python
pip install -r requirements.txt

# 2. Recopilar archivos estáticos
python manage.py collectstatic --no-input

# 3. Ejecutar migraciones
python manage.py migrate
