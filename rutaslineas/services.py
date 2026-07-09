from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.conf import settings
from collections import defaultdict
import heapq
import requests
from .models import Puntos, LineasPuntos, PuntosTransbordo

# ══════════════════════════════════════════════════════════════════════════
# Constantes de configuración
# ══════════════════════════════════════════════════════════════════════════
PENALIZACION_TRASBORDO_DEFAULT = 5.0   # minutos (trasbordos sin registro en BD)
VELOCIDAD_CAMINATA_MPM        = 83.33  # metros por minuto (≈ 5 km/h)
RADIO_USUARIO                 = 500    # metros – radio para buscar paradas del usuario
RADIO_TRASBORDO               = 300    # metros – radio para detectar trasbordos dinámicos
SRID_UTM                      = 32720  # EPSG 32720 – UTM Zona 20S (Santa Cruz de la Sierra)
K_RUTAS_MAX                   = 5      # máximo de opciones de ruta a devolver

# Caché del grafo: se construye una única vez por ciclo de vida del proceso Django
_GRAFO_CACHE: dict | None = None
_META_CACHE:  dict | None = None


# ══════════════════════════════════════════════════════════════════════════
# APIs de Google (sin cambios respecto al código original)
# ══════════════════════════════════════════════════════════════════════════

def sugerencias_google(place: str):
    url    = settings.URL_GOOGLE_PLACES
    fields = "places.id,places.displayName,places.location,places.viewport"
    headers = {
        "X-Goog-Api-Key": settings.API_KEY_GOOGLE,
        "X-Goog-FieldMask": fields,
        "Content-Type": 'application/json',
    }
    body = {
        "textQuery": place,
        "languageCode": "es",
        "locationBias": {
            "circle": {
                "center": {"latitude": -17.783727, "longitude": -63.179835},
                "radius": 2000.0,
            }
        },
    }
    try:
        respuesta = requests.post(url, headers=headers, json=body, timeout=5)
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.exceptions.HTTPError as e:
        print("--------------------------------------------------")
        print("GOOGLE PLACES ERROR RESPONSE:", respuesta.text)
        print("--------------------------------------------------")
        return {"error": "Google rechazo la peticion", "detalle": str(e),
                "status": respuesta.status_code}
    except requests.exceptions.RequestException as e:
        return {"error": "hubo un problema con la red", "detalle": str(e)}


def ruta_pie(punto_o: Point, punto_d: Point):
    url    = settings.URL_GOOGLE_ROUTES
    fields = 'routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline'
    headers = {
        "X-Goog-Api-Key": settings.API_KEY_GOOGLE,
        "X-Goog-FieldMask": fields,
        "Content-Type": "application/json",
    }
    body = {
        "origin": {
            "location": {"latLng": {"latitude": punto_o.y, "longitude": punto_o.x}}
        },
        "destination": {
            "location": {"latLng": {"latitude": punto_d.y, "longitude": punto_d.x}}
        },
        "travelMode": "WALK",
        "languageCode": "es",
    }
    try:
        respuesta = requests.post(url, headers=headers, json=body, timeout=5)
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.exceptions.HTTPError as e:
        return {"error": "Google rechazo la peticion", "detalle": str(e),
                "status": respuesta.status_code}
    except requests.exceptions.RequestException as e:
        return {"error": "hubo un problema con la red", "detalle": str(e)}


# ══════════════════════════════════════════════════════════════════════════
# Utilidades geoespaciales
# ══════════════════════════════════════════════════════════════════════════

def _distancia_m(coord1: Point, coord2: Point) -> float:
    """Distancia euclidiana en metros usando proyección UTM Zona 20S."""
    p1 = coord1.transform(SRID_UTM, clone=True)
    p2 = coord2.transform(SRID_UTM, clone=True)
    return p1.distance(p2)


def _minutos_caminata(dist_m: float) -> float:
    """Convierte distancia en metros a tiempo estimado de caminata en minutos."""
    return dist_m / VELOCIDAD_CAMINATA_MPM


def _google_pie_info(coord_o: Point, coord_d: Point) -> tuple:
    """
    Llama a Google Routes API (modo WALK).
    Retorna (tiempo_minutos, distancia_metros, encodedPolyline).
    Si la llamada falla, usa estimación geométrica con polyline vacío.
    """
    resp = ruta_pie(coord_o, coord_d)
    if isinstance(resp, dict) and resp.get('routes'):
        r     = resp['routes'][0]
        t_min = float(r['duration'].rstrip('s')) / 60.0
        d_m   = float(r['distanceMeters'])
        enc   = r.get('polyline', {}).get('encodedPolyline', '')
        return round(t_min, 2), round(d_m, 2), enc
    # Fallback: estimación geométrica si Google falla
    d_m = _distancia_m(coord_o, coord_d)
    return round(_minutos_caminata(d_m), 2), round(d_m, 2), ''


# ══════════════════════════════════════════════════════════════════════════
# Construcción del grafo de transporte
# ══════════════════════════════════════════════════════════════════════════

def construir_grafo() -> tuple:
    """
    Construye el grafo dirigido del sistema de transporte público.

    Nodo  : tupla (linea_ruta_id, punto_id)

    Tipos de arista:
    'bus'                → tramo entre dos paradas consecutivas de la misma ruta.
                            Peso = tiempo de viaje en bus (minutos).
    'trasbordo'          → cambio de línea en el MISMO punto físico.
                            Origen en tabla PuntosTransbordo.
                            Peso = penalizacionmin de la BD.
    'trasbordo_caminata' → cambio de línea caminando a una parada cercana
                            (dentro de RADIO_TRASBORDO metros).
                            Peso = tiempo de caminata + PENALIZACION_TRASBORDO_DEFAULT
                            (o la penalización de BD si el par está registrado).

    Retorna
    -------
    grafo    : dict  nodo → list[arista]
    metadata : dict  nodo → {'coordenada', 'linea', 'color'}
    """
    grafo    = defaultdict(list)
    metadata = {}

    # ── Carga de datos desde la BD ────────────────────────────────────────
    todos_lp = list(
        LineasPuntos.objects
        .select_related('idlinearuta__idlinea', 'idpunto')
        .order_by('idlinearuta_id', 'orden')
    )
    trasbordos_db = list(
        PuntosTransbordo.objects
        .select_related('idlineaorigen', 'idlineadestino', 'idpunto')
    )

    # ── Índices auxiliares ────────────────────────────────────────────────
    lp_por_linea = defaultdict(list)  # linea_ruta_id → [LineasPuntos]
    lp_por_punto = defaultdict(list)  # punto_id      → [LineasPuntos]  (solo stop='S')

    for lp in todos_lp:
        lp_por_linea[lp.idlinearuta_id].append(lp)
        if lp.idpunto.stop == 'S':
            lp_por_punto[lp.idpunto_id].append(lp)

        # Metadatos de cada nodo del grafo
        node = (lp.idlinearuta_id, lp.idpunto_id)
        metadata[node] = {
            'coordenada': lp.idpunto.coordenada,
            'linea':      lp.idlinearuta.idlinea.nombrelinea,
            'color':      lp.idlinearuta.idlinea.colorlinea,
        }

    # Mapa de penalizaciones predefinidas para consulta O(1)
    # Clave: (punto_id, linea_ruta_origen_id, linea_ruta_destino_id)
    penalizaciones_definidas: dict = {}
    for tr in trasbordos_db:
        key = (tr.idpunto_id, tr.idlineaorigen_id, tr.idlineadestino_id)
        penalizaciones_definidas[key] = float(tr.penalizacionmin)

    # ── Aristas tipo 'bus' ────────────────────────────────────────────────
    # Conectan paradas consecutivas de la misma LineaRuta
    for lr_id, lps in lp_por_linea.items():
        lps_ord = sorted(lps, key=lambda x: x.orden)
        for i in range(len(lps_ord) - 1):
            curr = lps_ord[i]
            nxt  = lps_ord[i + 1]
            grafo[(lr_id, curr.idpunto_id)].append({
                'tipo':             'bus',
                'nodo':             (lr_id, nxt.idpunto_id),
                'peso':             curr.tiempo / 60.0,      # segundos → minutos
                'distancia':        curr.distancia,          # metros
                'linea':            curr.idlinearuta.idlinea.nombrelinea,
                'color':            curr.idlinearuta.idlinea.colorlinea,
                'coordenada_desde': curr.idpunto.coordenada, # coord del nodo origen
            })

    # ── Aristas tipo 'trasbordo' (predefinidas, mismo punto físico) ───────
    for tr in trasbordos_db:
        from_node = (tr.idlineaorigen_id, tr.idpunto_id)
        to_node   = (tr.idlineadestino_id, tr.idpunto_id)
        grafo[from_node].append({
            'tipo':     'trasbordo',
            'nodo':     to_node,
            'peso':     float(tr.penalizacionmin),
            'distancia': 0.0,
        })

    # ── Aristas tipo 'trasbordo_caminata' (dinámicas) ─────────────────────
    # Para cada parada tipo 'S', buscamos otras paradas 'S' en el radio de
    # trasbordo que pertenezcan a una LineaRuta diferente.
    puntos_stop_ids = list(lp_por_punto.keys())
    puntos_map = {p.pk: p for p in Puntos.objects.filter(pk__in=puntos_stop_ids)}

    # Evitamos duplicar aristas ya cubiertas por trasbordos predefinidos
    ya_agregados: set = {
        (lr_o, p_id, lr_d, p_id)
        for (p_id, lr_o, lr_d) in penalizaciones_definidas
    }

    for punto_id, lps_en_punto in lp_por_punto.items():
        punto = puntos_map.get(punto_id)
        if not punto:
            continue

        # Paradas físicamente distintas dentro del radio de trasbordo
        cercanos = Puntos.objects.filter(
            coordenada__distance_lte=(punto.coordenada, D(m=RADIO_TRASBORDO)),
            stop='S',
        ).exclude(pk=punto_id)

        for cercano in cercanos:
            dist_m     = _distancia_m(punto.coordenada, cercano.coordenada)
            t_caminata = _minutos_caminata(dist_m)

            # Crear arista para cada combinación de líneas entre punto y cercano
            for lp_from in lps_en_punto:
                for lp_to in lp_por_punto.get(cercano.pk, []):
                    if lp_from.idlinearuta_id == lp_to.idlinearuta_id:
                        continue  # misma ruta: no es trasbordo
                    if lp_from.idlinearuta.idlinea_id == lp_to.idlinearuta.idlinea_id:
                        continue  # misma línea (ej. ida/vuelta de L001): no tiene sentido trasbordar

                    edge_key = (lp_from.idlinearuta_id, punto_id,
                                lp_to.idlinearuta_id, cercano.pk)
                    if edge_key in ya_agregados:
                        continue
                    ya_agregados.add(edge_key)

                    # Usar penalización de BD si está definida, si no la default (5 min)
                    penalizacion = penalizaciones_definidas.get(
                        (punto_id, lp_from.idlinearuta_id, lp_to.idlinearuta_id),
                        PENALIZACION_TRASBORDO_DEFAULT,
                    )

                    grafo[(lp_from.idlinearuta_id, punto_id)].append({
                        'tipo':        'trasbordo_caminata',
                        'nodo':        (lp_to.idlinearuta_id, cercano.pk),
                        'peso':        t_caminata + penalizacion,
                        'distancia':   dist_m,
                        'coord_desde': punto.coordenada,
                        'coord_hasta': cercano.coordenada,
                    })

    return dict(grafo), metadata


def obtener_grafo() -> tuple:
    """
    Retorna (grafo, metadata) desde caché.
    Si no existe la caché, construye el grafo leyendo la BD (operación costosa
    que solo ocurre la primera vez por proceso).
    """
    global _GRAFO_CACHE, _META_CACHE
    if _GRAFO_CACHE is None:
        _GRAFO_CACHE, _META_CACHE = construir_grafo()
    return _GRAFO_CACHE, _META_CACHE


def invalidar_grafo():
    """
    Invalida la caché del grafo para forzar su reconstrucción en la próxima petición.
    Llamar después de cambios en la BD (ej: import_csv).
    """
    global _GRAFO_CACHE, _META_CACHE
    _GRAFO_CACHE = None
    _META_CACHE  = None


# ══════════════════════════════════════════════════════════════════════════
# Algoritmo de Dijkstra multi-fuente / multi-destino
# ══════════════════════════════════════════════════════════════════════════

def dijkstra_transito(grafo: dict, fuentes: list, destinos: set) -> tuple:
    """
    Dijkstra multi-fuente, multi-destino sobre el grafo de transporte.

    Recibe múltiples nodos de inicio (paradas cercanas al origen del usuario)
    con sus costos iniciales de caminata, y busca todos los nodos destino
    (paradas cercanas al destino del usuario), encontrando el camino de
    menor costo total para cada uno.

    Parámetros
    ----------
    grafo    : lista de adyacencia  nodo → [arista, ...]
    fuentes  : list of (costo_inicial, nodo, arista_inicio)
    destinos : set de nodos objetivo

    Retorna
    -------
    resultados : list[(costo_total, nodo_destino)] ordenados por costo ascendente
    previo     : dict  nodo → (nodo_anterior, arista) para reconstruir caminos
    """
    heap    = []  # min-heap: (costo, tiebreaker_int, nodo)
    tie     = 0
    costos  = {}  # nodo → menor costo conocido hasta el momento
    previo  = {}  # nodo → (nodo_anterior, arista_que_llegó_aquí)

    # Inicializar heap con todos los nodos de origen
    for costo_ini, nodo, arista_ini in fuentes:
        if nodo not in costos or costo_ini < costos[nodo]:
            costos[nodo] = costo_ini
            previo[nodo] = (None, arista_ini)
            heapq.heappush(heap, (costo_ini, tie, nodo))
            tie += 1

    resultados  = []
    encontrados = set()

    while heap:
        costo, _, nodo = heapq.heappop(heap)

        # Entrada obsoleta en el heap (lazy deletion)
        if costo > costos.get(nodo, float('inf')):
            continue

        # ¿Llegamos a un nodo destino?
        if nodo in destinos and nodo not in encontrados:
            encontrados.add(nodo)
            resultados.append((costo, nodo))
            # No hacemos break: seguimos para encontrar los demás destinos

        # Explorar vecinos
        for arista in grafo.get(nodo, []):
            vecino      = arista['nodo']
            nuevo_costo = costo + arista['peso']
            if nuevo_costo < costos.get(vecino, float('inf')):
                costos[vecino] = nuevo_costo
                previo[vecino] = (nodo, arista)
                heapq.heappush(heap, (nuevo_costo, tie, vecino))
                tie += 1

    return resultados, previo


def reconstruir_camino(previo: dict, nodo_final) -> list:
    """
    Reconstruye la secuencia [(nodo, arista)] desde el origen hasta nodo_final
    usando el diccionario 'previo' generado por Dijkstra.

    El primer elemento tiene arista de tipo 'inicio'.
    Cada arista en posición i conecta nodo[i-1] → nodo[i].
    """
    camino = []
    nodo   = nodo_final
    while nodo is not None:
        padre, arista = previo[nodo]
        camino.append((nodo, arista))
        nodo = padre
    camino.reverse()
    return camino


# ══════════════════════════════════════════════════════════════════════════
# Formateo de la ruta encontrada → estructura de respuesta del API
# ══════════════════════════════════════════════════════════════════════════

def formatear_ruta(
    camino: list,
    metadata: dict,
    punto_o: Point,
    punto_d: Point,
    id_opcion: int,
) -> dict:
    """
    Convierte el camino reconstruido de Dijkstra en la estructura de respuesta
    del API, agrupando aristas consecutivas del mismo tipo/línea en tramos.

    Tramos generados:
    caminata_origen       → caminata del usuario a la primera parada de bus
    viaje_bus             → recorrido en micro (segmentos agrupados por línea)
    espera_trasbordo      → penalización en el mismo punto físico
    caminata_trasbordo    → caminata entre paradas de distinta línea
    caminata_destino      → caminata de la última parada al usuario
    """
    tramos          = []
    tiempo_total    = 0.0
    distancia_total = 0.0
    tipo_ruta       = 'Directa'

    # Nodo/arista de inicio y coordenadas extremas
    nodo_inicial, _ = camino[0]
    coord_primera_parada = metadata.get(nodo_inicial, {}).get('coordenada')
    coord_ultima_parada  = metadata.get(camino[-1][0], {}).get('coordenada')

    # ── Agrupar aristas en bloques homogéneos ─────────────────────────────
    # Bloque bus: aristas consecutivas del mismo tipo 'bus' Y misma línea
    # Resto: cada arista de trasbordo forma su propio bloque
    bloques        = []
    bloque_clave   = None   # ('bus', nombre_linea) | 'trasbordo' | 'trasbordo_caminata'
    bloque_aristas = []

    for _, arista in camino[1:]:  # omitir el primer elemento (arista 'inicio')
        tipo = arista['tipo']

        if tipo == 'bus':
            clave = ('bus', arista['linea'])
            if clave == bloque_clave:
                bloque_aristas.append(arista)
            else:
                if bloque_aristas:
                    bloques.append((bloque_clave, bloque_aristas))
                bloque_clave   = clave
                bloque_aristas = [arista]
        else:
            if bloque_aristas:
                bloques.append((bloque_clave, bloque_aristas))
            bloque_clave   = tipo
            bloque_aristas = [arista]

    if bloque_aristas:
        bloques.append((bloque_clave, bloque_aristas))

    # ── Procesar cada bloque y construir tramos ───────────────────────────
    for bloque_clave, aristas in bloques:

        # ── Tramo en bus ──────────────────────────────────────────────────
        if isinstance(bloque_clave, tuple) and bloque_clave[0] == 'bus':
            linea_nombre  = aristas[0]['linea']
            color         = aristas[0]['color']
            tiempo_bus    = sum(a['peso'] for a in aristas)
            distancia_bus = sum(a.get('distancia', 0.0) for a in aristas)

            # Coordenadas del recorrido:
            # - 'coordenada_desde' de cada arista → coord del nodo origen de esa arista
            # - Más la coord del nodo final (destino de la última arista)
            coords = [
                [a['coordenada_desde'].x, a['coordenada_desde'].y]
                for a in aristas
            ]
            coord_nodo_final = metadata.get(aristas[-1]['nodo'], {}).get('coordenada')
            if coord_nodo_final:
                coords.append([coord_nodo_final.x, coord_nodo_final.y])

            tramos.append({
                'tipo':             'viaje_bus',
                'tiempo_minutos':   round(tiempo_bus, 2),
                'distancia_metros': round(distancia_bus, 2),
                'linea':            linea_nombre,
                'color_linea':      color,
                'coordenadas':      coords,
            })
            tiempo_total    += tiempo_bus
            distancia_total += distancia_bus

        # ── Trasbordo en el mismo punto (solo penalización de tiempo) ──────
        elif bloque_clave == 'trasbordo':
            tipo_ruta    = 'Con trasbordo'
            penalizacion = sum(a['peso'] for a in aristas)
            tramos.append({
                'tipo':             'espera_trasbordo',
                'tiempo_minutos':   round(penalizacion, 2),
                'distancia_metros': 0.0,
            })
            tiempo_total += penalizacion

        # ── Trasbordo caminando a parada cercana de otra línea ─────────────
        elif bloque_clave == 'trasbordo_caminata':
            tipo_ruta   = 'Con trasbordo'
            arista      = aristas[0]
            coord_desde = arista.get('coord_desde')
            coord_hasta = arista.get('coord_hasta')

            if coord_desde and coord_hasta:
                t_tr, d_tr, enc_tr = _google_pie_info(coord_desde, coord_hasta)
            else:
                d_tr   = arista.get('distancia', 0.0)
                t_tr   = round(_minutos_caminata(d_tr), 2)
                enc_tr = ''

            tramos.append({
                'tipo':             'caminata_trasbordo',
                'tiempo_minutos':   t_tr,
                'distancia_metros': d_tr,
                'encodedPolyline':  enc_tr,
            })
            tiempo_total    += t_tr
            distancia_total += d_tr

    # ── Caminata inicial: usuario → primera parada de bus ─────────────────
    if coord_primera_parada:
        t_o, d_o, enc_o = _google_pie_info(punto_o, coord_primera_parada)
        tramos.insert(0, {
            'tipo':             'caminata_origen',
            'tiempo_minutos':   t_o,
            'distancia_metros': d_o,
            'encodedPolyline':  enc_o,
        })
        tiempo_total    += t_o
        distancia_total += d_o

    # ── Caminata final: última parada de bus → usuario ────────────────────
    if coord_ultima_parada:
        t_d, d_d, enc_d = _google_pie_info(coord_ultima_parada, punto_d)
        tramos.append({
            'tipo':             'caminata_destino',
            'tiempo_minutos':   t_d,
            'distancia_metros': d_d,
            'encodedPolyline':  enc_d,
        })
        tiempo_total    += t_d
        distancia_total += d_d

    return {
        'idopcion':        id_opcion,
        'tipo_ruta':       tipo_ruta,
        'tiempo_total':    round(tiempo_total, 2),
        'distancia_total': round(distancia_total, 2),
        'tramo':           tramos,
    }


# ══════════════════════════════════════════════════════════════════════════
# Función principal – punto de entrada del API
# ══════════════════════════════════════════════════════════════════════════

def calcular_ruta_optima(lat_o: float, lng_o: float,
                        lat_d: float, lng_d: float,
                        k: int = K_RUTAS_MAX) -> list:
    """
    Calcula las mejores rutas (directas y con trasbordo) entre dos coordenadas
    usando Dijkstra sobre el grafo de transporte público.

    Flujo:
    1. Obtener (o construir) el grafo cacheado.
    2. Buscar paradas de tipo 'S' dentro de RADIO_USUARIO del origen y destino.
    3. Ejecutar Dijkstra multi-fuente / multi-destino:
        - Fuentes  = paradas cercanas al origen (costo inicial = caminata estimada)
        - Destinos = paradas cercanas al destino del usuario
    4. Reconstruir los caminos encontrados y formatearlos.
    5. Deduplicar por combinación de líneas usadas y devolver las K mejores.

    Retorna
    -------
    Lista de rutas ordenadas por tiempo_total ascendente.
    Lista vacía si no hay rutas disponibles.
    """
    punto_o = Point(lng_o, lat_o, srid=4326)
    punto_d = Point(lng_d, lat_d, srid=4326)

    grafo, metadata = obtener_grafo()

    # ── Paradas cercanas al origen del usuario ────────────────────────────
    lps_origen = list(
        LineasPuntos.objects.filter(
            idpunto__coordenada__distance_lte=(punto_o, D(m=RADIO_USUARIO)),
            idpunto__stop='S',
        ).select_related('idpunto', 'idlinearuta__idlinea')
    )

    # ── Paradas cercanas al destino del usuario ───────────────────────────
    lps_destino = list(
        LineasPuntos.objects.filter(
            idpunto__coordenada__distance_lte=(punto_d, D(m=RADIO_USUARIO)),
            idpunto__stop='S',
        ).select_related('idpunto', 'idlinearuta__idlinea')
    )

    if not lps_origen or not lps_destino:
        return []

    # ── Fuentes para Dijkstra ─────────────────────────────────────────────
    # Costo inicial = tiempo de caminata estimado desde el usuario hasta la parada.
    # (Google API se llama luego, al formatear, para el costo real con polyline.)
    fuentes = []
    for lp in lps_origen:
        nodo          = (lp.idlinearuta_id, lp.idpunto_id)
        dist_m        = _distancia_m(punto_o, lp.idpunto.coordenada)
        costo_inicial = _minutos_caminata(dist_m)
        fuentes.append((
            costo_inicial,
            nodo,
            {'tipo': 'inicio', 'coordenada': lp.idpunto.coordenada},
        ))

    # ── Nodos destino para Dijkstra ───────────────────────────────────────
    nodos_destino = {
        (lp.idlinearuta_id, lp.idpunto_id) for lp in lps_destino
    }

    # ── Ejecutar Dijkstra ─────────────────────────────────────────────────
    resultados, previo = dijkstra_transito(grafo, fuentes, nodos_destino)

    if not resultados:
        return []

    # ── Reconstruir caminos y formatear rutas ─────────────────────────────
    # Los resultados ya vienen ordenados por costo ascendente (Dijkstra).
    # Deduplicamos por combinación de líneas: si dos caminos usan exactamente
    # las mismas líneas, nos quedamos solo con el de menor tiempo (el primero).
    rutas_formateadas    = []
    combinaciones_usadas = set()
    id_opcion            = 1

    for costo_total, nodo_final in resultados:
        if id_opcion > k:
            break

        camino = reconstruir_camino(previo, nodo_final)

        # Identificar la combinación de líneas usadas en este camino
        lineas = tuple(sorted({
            a['linea']
            for _, a in camino
            if a.get('tipo') == 'bus' and a.get('linea')
        }))

        if lineas in combinaciones_usadas:
            continue   # ya tenemos una ruta con esa misma combinación de líneas
        combinaciones_usadas.add(lineas)

        ruta = formatear_ruta(camino, metadata, punto_o, punto_d, id_opcion)
        rutas_formateadas.append(ruta)
        id_opcion += 1

    return rutas_formateadas