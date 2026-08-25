# Consolas Home Assistant add-on

Repositorio de despliegue del único runtime de Consolas en Home Assistant.
Home Assistant lo consume desde el Store; la aplicación se abre por Ingress en
la entrada **Consolas** de la barra lateral.

Este repositorio contiene sólo el paquete saneado: código y recursos base de
catálogo. No contiene estado de colección, fotos propias, oportunidades
runtime, SQLite, secretos, logs ni credenciales. Todo eso permanece en el
volumen privado `/data` del add-on.

No instalar ni mantener una variante local, scheduler de Mac, cron, launchd o
Mail.app para este producto.
