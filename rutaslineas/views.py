from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from .models import Lineas, Puntos, LineaRuta
from .serializers import ListaLineasSerializer, DetalleLineaRutaSerilaizer, DetalleLineasSerialzier, RutaOptimaSerializer
from .services import calcular_ruta_optima, sugerencias_google, K_RUTAS_MAX
# Create your views here.

class LineasViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Lineas.objects.all()
    def get_serializer_class(self):
        if self.action == 'list':
            return ListaLineasSerializer
        elif self.action == 'retrieve':
            return DetalleLineasSerialzier
        return super().get_serializer_class()

class RutaLineaViewSet(viewsets.ReadOnlyModelViewSet):
    #si recibe solo el idlinea le enviamos las coordenadas tanto de la ida como la vuelta
    #si recibe un un idruta el enviamos la coordenadas de esa ruta ya sea vuelta o ida
    def get_queryset(self):
        queryset = LineaRuta.objects.all().prefetch_related('lineaspuntos_set__idpunto')
        linea_id = self.request.query_params.get('idlinea')
        ruta_id = self.request.query_params.get('idruta')
        if linea_id is not None: 
            queryset = queryset.filter(idlinea=linea_id)
        if ruta_id is not None:
            queryset = queryset.filter(idruta=ruta_id)
        
        return queryset
    serializer_class = DetalleLineaRutaSerilaizer

class ListaLineaViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ListaLineasSerializer
    def get_queryset(self):
        queryset = Lineas.objects.all()
        queryset2 = queryset.filter()
        lat_str = self.request.query_params.get('lat')
        lng_str = self.request.query_params.get('lng')
        #el radio esta en metros, si el usuario no envia el radio por defecto usaremos un radio un 500 metros
        radio_str = self.request.query_params.get('radio', '500')
        
        if lat_str and lng_str:
            lat = float(lat_str)
            lng = float(lng_str)
            radio = float(radio_str)
            punto_usuario = Point(lng, lat, srid=4326)
            
            puntos_cercanos_ids = Puntos.objects.filter(
                coordenada__distance_lte=(punto_usuario, D(m=radio))
            ).values_list('pk', flat=True)
            
            queryset= queryset.filter(
                linearuta__lineaspuntos__idpunto__in=puntos_cercanos_ids
            ).distinct()
            
        return queryset

class RutaOptimaViewSet(APIView):
    def get(self, request):
        try:
        #sacamos las coordenadas del query_params y los convertimos en float
            lat_o = float(request.query_params.get('lat_o'))
            lng_o = float(request.query_params.get('lng_o'))
            lat_d = float(request.query_params.get('lat_d'))
            lng_d = float(request.query_params.get('lng_d'))
        except(TypeError, ValueError):
            return Response(
                {"error": "Coordenadas de origen o destino invalidas o imcompletas"},
                status=status.HTTP_400_BAD_REQUEST
            )
        # Cantidad de rutas a calcular: opcional, por defecto K_RUTAS_MAX, máximo 10
        try:
            cantidad = int(request.query_params.get('cantidad', K_RUTAS_MAX))
            cantidad = max(1, min(cantidad, 10))  # limitar entre 1 y 10
        except (TypeError, ValueError):
            cantidad = K_RUTAS_MAX

        rutas = calcular_ruta_optima(lat_o, lng_o, lat_d, lng_d, k=cantidad)
        serializer = RutaOptimaSerializer(instance=rutas, many=True)
        return Response(serializer.data)

class SugerenciasGoogleView(APIView):
    def get(self, request):
        lugar = request.query_params.get('lugar', '').strip()
        #verificamos que la peticion no este vacia
        if not lugar:
            return Response(
                {"error": "Debe escribir un texto para buscar el lugar"}, status=status.HTTP_400_BAD_REQUEST
            )
        resultado = sugerencias_google(lugar)
        if isinstance(resultado, dict) and "error" in resultado:
            return Response(
                {
                    "error": resultado["error"],
                    "detalle": resultado.get("detalle", "")
                },
                status=resultado.get("status", status.HTTP_400_BAD_REQUEST)
            )
        return Response(resultado, status=status.HTTP_200_OK)