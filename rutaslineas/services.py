from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db.models import Sum
from django.conf import settings
import requests
from .models import Puntos, LineasPuntos, PuntosTransbordo

def sugerencias_google(place: str):
    url = settings.URL_GOOGLE
    fields = "places.id,places.displayName,places.location,places.viewport"
    headers = {
        "X-Goog-Api-Key": settings.API_KEY_GOOGLE,
        "X-Goog-FieldMask": fields,
        "Content-Type": 'application/json'
    }
    body = {
        "textQuery": place,
        "languageCode": "es",
        "locationRestriction": {
            "circle": {
                "center":{
                    "latitude": -17.783727,
                    "longitude": -63.179835
                },
                "radius": 2000.0
            }
        }
    }
    try:
        respuesta = requests.post(
            url, headers=headers, json=body, timeout=5
        )
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.exceptions.HTTPError as e:
        return {"error": "Google rechazo la peticion", "detalle": str(e), "status": respuesta.status_code}
    except requests.exceptions.RequestException as e:
        return {"error": "hubo un problema con la red", "detalle": str(e)}

def calcular_distancia(punto_o: Point, punto_d: Point):
    punto_o_utm = punto_o.transform(32720, clone=True)
    punto_d_utm = punto_d.transform(32720, clone=True)
    distancia = punto_o_utm.distance(punto_d_utm)
    return distancia

#recibe un punto y un radio, envia la lineaspuntos, linea y el punto que estan dentro del radio del punto
def limpiar_rutas_directas(rutas_encontradas: list):
    rutas_unicas = []
    lineas_procesadas = set()
    for ruta in rutas_encontradas:
        nombre_linea = ruta['tramo'][1]['linea']
        if nombre_linea not in lineas_procesadas:
            lineas_procesadas.add(nombre_linea)
            rutas_unicas.append(ruta)
    for indice, ruta in enumerate(rutas_unicas):
        ruta['idopcion'] = indice + 1
    return rutas_unicas

#devuelve los puntos que estan dentro de del radio del punto origen
def paradas_cercanas(punto: Point, radio: int=200):
    paradas = Puntos.objects.filter(coordenada__distance_lte = (punto, D(m=radio)), stop='S').values_list('pk', flat=True)
    return LineasPuntos.objects.filter(idpunto__in=paradas).select_related('idlinearuta__idlinea', 'idpunto')

#envia una lista de todas las rutas directas
def calcular_rutas_directas(paradas_origen, paradas_destino, punto_o, punto_d, lng_o, lat_o, lng_d, lat_d)-> list:
    rutas_encontradas = []
    for p_origen in paradas_origen:
        for p_destino in paradas_destino:
            #preguntamos si el punto origen y el punto destino pertenecen a la misma ruta y ademas de el orden del punto origen es menor al orden del punto destino
            if (p_origen.idlinearuta == p_destino.idlinearuta) and (p_origen.orden < p_destino.orden):
                distancia_pie_o = calcular_distancia(punto_o, p_origen.idpunto.coordenada)
                distancia_pie_d = calcular_distancia(p_destino.idpunto.coordenada, punto_d)
                #velocidad promedio humana: 5km/h -> 83m/min
                tiempo_pie_o = distancia_pie_o / 83.3
                tiempo_pie_d = distancia_pie_d / 83.3
                
                #calculamos el tiempo total del viaje en bus 
                resultado_suma = LineasPuntos.objects.filter(
                    idlinearuta = p_origen.idlinearuta,
                    orden__gte = p_origen.orden,
                    orden__lt = p_destino.orden
                ).aggregate(total_tiempo = Sum('tiempo'))
                
                tiempo_bus = (resultado_suma['total_tiempo'] or 0) / 60.0
                
                #calculamos la distancia total del viaje en bus
                resultado_suma = LineasPuntos.objects.filter(
                    idlinearuta = p_origen.idlinearuta,
                    orden__gte = p_origen.orden,
                    orden__lt = p_destino.orden
                ).aggregate(total_distancia = Sum('distancia'))
                
                distancia_bus = resultado_suma['total_distancia'] or 0
                
                puntos_recorridos = LineasPuntos.objects.filter(
                    idlinearuta = p_origen.idlinearuta,
                    orden__gte = p_origen.orden,
                    orden__lte = p_destino.orden
                ).order_by('orden').values_list('idpunto__coordenada', flat=True)
                
                coords_bus = [[p.x, p.y] for p in puntos_recorridos]
                
                rutas_encontradas.append({
                    "idopcion": len(rutas_encontradas) + 1,
                    "tipo_ruta": "Directa",
                    #tiempo total en minutos
                    "tiempo_total": round(tiempo_pie_o + tiempo_bus + tiempo_pie_d, 2),
                    #distancia total en metros
                    "distancia_total": round(distancia_pie_o + distancia_bus + distancia_pie_d, 2),
                    "tramo": [
                        {
                            "tipo" : "caminata_origen",
                            "tiempo_minutos" : round(tiempo_pie_o, 2),
                            "distancia_metros" : round(distancia_pie_o, 2),
                            "coordenadas" : [[lng_o, lat_o], [p_origen.idpunto.coordenada.x, p_origen.idpunto.coordenada.y]]
                        },
                        {
                            "tipo": "viaje_bus",
                            "tiempo_minutos" : round(tiempo_bus, 2),
                            "distancia_metros" : round(distancia_bus, 2),
                            "linea" : p_origen.idlinearuta.idlinea.nombrelinea,
                            "color_linea" : p_origen.idlinearuta.idlinea.colorlinea,
                            "coordenadas": coords_bus
                        },
                        {
                            "tipo" : "caminata_destino",
                            "tiempo_minutos" : round(tiempo_pie_d, 2),
                            "distancia_metros" : round(distancia_pie_d, 2),
                            "coordenadas" : [[lng_d, lat_d], [p_destino.idpunto.coordenada.x, p_destino.idpunto.coordenada.y]]
                        }
                    ]
                })
    return rutas_encontradas

#envia una lista de todas las rutas con trasbordo
def calcular_rutas_trasbordo()-> list:
    pass

#funcion principal
def calcular_ruta_optima(lat_o: float, lng_o: float, lat_d: float, lng_d: float) -> list:
    #convertimos nuestras coordenas de origen y destino en un objeto Puntp
    punto_o = Point(lng_o, lat_o, srid=4326)
    punto_d = Point(lng_d,lat_d, srid=4326)
    
    #obtener el query para consultar los puntos cercanos al punto de origen y destino ademas tambien se obtiene la linea a la que pertence
    paradas_origen = paradas_cercanas(punto_o)
    paradas_destino = paradas_cercanas(punto_d)
    
    #obtner las rutas directas que nos llevan del punto origen al destino
    rutas_encontradas = calcular_rutas_directas(paradas_origen, paradas_destino, punto_o, punto_d, lng_o, lat_o, lng_d, lat_d)
    
    #ordenamos las rutas encontradas desde la de mayor tiempo hasta la de menor tiempo
    rutas_encontradas.sort(key=lambda x: x['tiempo_total'])
    #limpianos nuestra lista de rutas, las si hay una linea de micro que tenga mas de una ruta directa
    #nos quedamos con la de menor tiempo y eliminamos las demas rutas directas pertencientas a esa linea
    rutas_encontradas = limpiar_rutas_directas(rutas_encontradas)
        
    return rutas_encontradas