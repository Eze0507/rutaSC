import csv
import os
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.gis.geos import Point
from rutaslineas.models import Lineas, Puntos, LineaRuta, LineasPuntos, PuntosTransbordo
from rutaslineas.services import invalidar_grafo

class Command(BaseCommand):
    help = 'importar los datos de las rutas de los microbuses desde archivos CSV a PostGIS'
    def handle(self, *args, **options):
        with transaction.atomic():
            self.stdout.write('1. importando lineas...')
            ruta = os.path.join(settings.BASE_DIR, 'csv_imports', 'Lineas.csv')
            with open(ruta, 'r', encoding='utf-8-sig') as archivo:
                lector = csv.DictReader(archivo, delimiter=';')
                for fila in lector:
                    Lineas.objects.create(
                        id = fila['IdLinea'],
                        nombrelinea = fila['NombreLinea'],
                        colorlinea = fila['ColorLinea'],
                        imagenmicro = fila['ImagenMicrobus'],
                    )
            
            self.stdout.write('2. importando puntos...')
            ruta = os.path.join(settings.BASE_DIR, 'csv_imports', 'puntos.csv')
            with open(ruta, 'r', encoding='utf-8-sig') as archivo:
                lector = csv.DictReader(archivo, delimiter=';')
                for fila in lector:
                    punto = Point(float(fila['Longitud']), float(fila['Latitud']))
                    Puntos.objects.create(
                        id = int(fila['IdPunto']),
                        descripcion = fila['Descripcion'],
                        stop = fila['Stop'],
                        coordenada = punto
                    )
            
            self.stdout.write('3. importando LineasRutas...')
            ruta = os.path.join(settings.BASE_DIR, 'csv_imports', 'LineaRuta.csv')
            with open(ruta, 'r', encoding='utf-8-sig') as archivo:
                lector = csv.DictReader(archivo, delimiter=';')
                for fila in lector:
                    LineaRuta.objects.create(
                        id = int(fila['IdLineaRuta']),
                        descripcion = fila['Descripcion'],
                        distancia = float(fila['Distancia']),
                        tiempo = float(fila['Tiempo']),
                        idlinea_id = int(fila['IdLinea']),
                        idruta = int(fila['IdRuta'])
                    )
            
            self.stdout.write('4. importando LineasPuntos...')
            ruta = os.path.join(settings.BASE_DIR, 'csv_imports', 'LineasPuntos.csv')
            puntos_cache = {p.id: p.coordenada for p in Puntos.objects.all()}
            with open(ruta, 'r', encoding='utf-8-sig') as archivo:
                lector = csv.DictReader(archivo, delimiter=';')
                for fila in lector:
                    destino_csv = int(fila['IdPuntoDest'])
                    destino_final = None if destino_csv == 0 else destino_csv
                    
                    distancia_f = 0.0
                    tiempo_f = 0.0
                    
                    if destino_final is not None:
                        coord_origen = puntos_cache[int(fila['IdPunto'])]
                        coord_destino = puntos_cache[destino_final]
                        
                        #32720 -> UTM ZONE 20S: zona de la ciudad de santa cruz
                        origen_utm = coord_origen.transform(32720, clone=True)
                        destino_utm = coord_destino.transform(32720, clone=True)
                        
                        distancia_f = origen_utm.distance(destino_utm)
                        #15km/h = 4.17m/s
                        tiempo_f = distancia_f / 4.17
                        
                    LineasPuntos.objects.create(
                        id = int(fila['IdLineaPunto']),
                        orden = int(fila['Orden']),
                        distancia = round(distancia_f, 2),
                        tiempo = round(tiempo_f, 2),
                        idlinearuta_id = int(fila['IdLineaRuta']),
                        idpunto_id = int(fila['IdPunto']),
                        idpuntodest_id = destino_final
                    )
            
            self.stdout.write('5. importando PuntosTrasbardos...')
            ruta = os.path.join(settings.BASE_DIR, 'csv_imports', 'PuntosTrasbordos.csv')
            with open(ruta, 'r', encoding='utf-8-sig') as archivo:
                lector = csv.DictReader(archivo, delimiter=';')
                for fila in lector:
                    PuntosTransbordo.objects.create(
                        id = int(fila['IdTrasbordo']),
                        idpunto_id = int(fila['IdPunto']),
                        idlineaorigen_id = int(fila['IdLineaOrigen']),
                        idlineadestino_id = int(fila['IdLineaDestino']),
                        penalizacionmin = int(fila['PenalizacionMin'])
                    )
            
            self.stdout.write(self.style.SUCCESS('¡Base da datos poblada!'))
            # Invalidar caché del grafo Dijkstra para reflejar los nuevos datos
            invalidar_grafo()
            self.stdout.write(self.style.SUCCESS('Caché del grafo invalidado.'))
