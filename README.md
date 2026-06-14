#sistema de informacion geografico
clonar repositorio
git clone [repository url]

entorno virtual
crear entorno virtual
python -m venv env

activar En Windows
.\env\Scripts\activate

activar En macOS/Linux
source env/bin/activate

instalar dependencias
pip install -r requirements.txt

Configura el proyecto:
Crea un archivo .env en la raíz del proyecto y rellénalo siguiendo el archivo .env.example.

generar SECRET_KEY
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

#nota:
antes de migrar, primero se debe instalar la extension de postgis usando el instalador de 'Stack builder' en muestro motor de base de datos,
luego creamos la base de datos y antes de pasar las tablas ejecutamos este comando para instalar postgis en la base de datos que se usara:
CREATE EXTENSION IF NOT EXISTS postgis;
verificamos si instalo:
SELECT postgis_full_version();
y procedemos con la migracion

migraciones para la base de datos:
python manage.py migrate

poblar la base de datos con los archivos CSV:
python manage.py import_csv

