from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LineasViewSet, RutaLineaViewSet, ListaLineaViewSet, RutaOptimaViewSet

router = DefaultRouter()

router.register(r'lineas', LineasViewSet, basename='lineas')
router.register(r'ruta-linea', RutaLineaViewSet, basename='rutalinea')
router.register(r'linea-proxima', ListaLineaViewSet, basename='lineaproxima')

urlpatterns = [
    path('', include(router.urls)),
    path('ruta-optima/', RutaOptimaViewSet.as_view(), name='rutaoptima'),
]