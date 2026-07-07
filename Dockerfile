FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar librerías espaciales del sistema (GDAL, PROJ)
RUN apt-get update && apt-get install -y \
    binutils \
    libproj-dev \
    gdal-bin \
    python3-gdal \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de tu proyecto
COPY . .

# Exponer el puerto para Render
EXPOSE 10000

# Dar permisos por seguridad y ejecutar el script
RUN chmod +x start.sh
CMD ["./start.sh"]