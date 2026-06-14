from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer
from .models import Lineas, LineaRuta, LineasPuntos, PuntosTransbordo, Puntos
from rest_framework_gis.fields import GeometrySerializerMethodField
from django.contrib.gis.geos import LineString

class ListaLineasSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lineas
        fields = ['id', 'nombrelinea']

class ListaLineaRutaSerializer(serializers.ModelSerializer):
    class Meta:
        model = LineaRuta
        fields = ['id', 'idruta','descripcion']

class DetalleLineasSerialzier(serializers.ModelSerializer):
    ruta = ListaLineaRutaSerializer(source='linearuta_set', many=True, read_only=True)
    class Meta:
        model = Lineas
        fields = ['id', 'nombrelinea', 'colorlinea', 'imagenmicro', 'ruta']

class DetalleLineaRutaSerilaizer(GeoFeatureModelSerializer):
    trayectoria = GeometrySerializerMethodField()
    class Meta:
        model = LineaRuta
        geo_field = 'trayectoria'
        fields = ['id', 'distancia', 'tiempo','trayectoria']
    
    #esta funcion obtiene todos los puntos pertenecientes a un ruta y los guarda en LineString
    def get_trayectoria(self, obj):
        segmentos = obj.lineaspuntos_set.all().order_by('orden')
        lista_coordenadas = []
        for segmento in segmentos:
            lista_coordenadas.append(segmento.idpunto.coordenada)
        if len(lista_coordenadas)>=2:
            return LineString(lista_coordenadas)
        return None

class tramoSerializer(serializers.Serializer):
    tipo = serializers.CharField(max_length=50)
    tiempo_minutos = serializers.FloatField()
    distancia_metros = serializers.FloatField(required=False)
    linea = serializers.CharField(max_length=50, required=False)
    color_linea = serializers.CharField(max_length=7, required=False)
    encodedPolyline = serializers.CharField(max_length=50, required=False)
    coordenadas = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField())
    , required=False)

class RutaOptimaSerializer(serializers.Serializer):
    idopcion = serializers.IntegerField()
    tipo_ruta = serializers.CharField(max_length=50)
    tiempo_total = serializers.FloatField()
    distancia_total = serializers.FloatField()
    tramo = tramoSerializer(many=True)