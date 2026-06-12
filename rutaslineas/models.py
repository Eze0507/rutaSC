from django.core.validators import MinValueValidator, MaxValueValidator
from django.contrib.gis.db import models

# Create your models here.
class Lineas(models.Model):
    nombrelinea = models.CharField(max_length=4, unique=True)
    colorlinea = models.CharField(max_length=7, null=True)
    imagenmicro = models.CharField(max_length=100, null=True)
    fechacreacion = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.nombrelinea}"
    class Meta:
        db_table = "lineas"
        verbose_name = "Linea"
        verbose_name_plural = "lineas"
        ordering = ['nombrelinea']

class Puntos(models.Model):
    coordenada = models.PointField(srid=4326)
    descripcion = models.CharField(max_length=6)
    stop = models.CharField(max_length=1)
    
    def __str__(self):
        return f"{self.descripcion}"
    
    class Meta:
        db_table = "puntos"
        verbose_name = "punto"
        verbose_name_plural = "puntos"
        ordering = ['id']

class LineaRuta(models.Model):
    idlinea = models.ForeignKey(Lineas, on_delete=models.CASCADE)
    idruta = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(2)])
    descripcion = models.CharField(max_length=17)
    #distancia en kilometros
    distancia = models.FloatField(default=0.0)
    #tiempo en horas
    tiempo = models.FloatField(default=0.0)
    
    def __str__(self):
        return f"{self.descripcion}"
    
    class Meta:
        db_table = "lineasrutas"
        verbose_name = "linearuta"
        verbose_name = "lineasrutas"
        ordering = ['idlinea', 'idruta']
    
class LineasPuntos(models.Model):
    idlinearuta = models.ForeignKey(LineaRuta, on_delete=models.CASCADE)
    idpunto = models.ForeignKey(Puntos, on_delete=models.CASCADE, related_name='punto_origen')
    idpuntodest = models.ForeignKey(Puntos, on_delete=models.SET_NULL, null=True, blank =True, related_name='punto_destino')
    orden = models.IntegerField()
    #distancia en metros
    distancia = models.FloatField(default=0.0)
    #tiempo en segundos
    tiempo = models.FloatField(default=0.0)
    
    def __str__(self):
        return f"puntos {self.idpunto}, {self.idpuntodest} de la ruta {self.idlinearuta}"
    
    class Meta:
        db_table = "lineaspuntos"
        verbose_name = "lineapunto"
        verbose_name_plural = "lineaspuntos"
        ordering = ['idlinearuta', 'orden']

class PuntosTransbordo(models.Model):
    idpunto = models.ForeignKey(Puntos, on_delete=models.CASCADE)
    idlineaorigen = models.ForeignKey(LineaRuta, on_delete=models.CASCADE, related_name='trasbordo_origen')
    idlineadestino = models.ForeignKey(LineaRuta, on_delete=models.SET_NULL, null=True, related_name='trasbordo_destino')
    penalizacionmin = models.IntegerField()
    
    def __str__(self):
        return f"trasbordo en el punto {self.idpunto} de la ruta {self.idlineaorigen} a la ruta {self.idlineadestino} "
    
    class Meta:
        db_table = "puntostrasbordos"
        verbose_name = "puntotrasbordo"
        verbose_name_plural = "puntostrasbordos"
        ordering = ['idpunto']
