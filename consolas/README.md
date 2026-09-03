# Consolas para Home Assistant

Add-on único y permanente de Consolas. Sirve la aplicación por Ingress,
persiste la colección en SQLite y ejecuta Auction Watch dentro del mismo
runtime.

## Operación

- Abrir **Consolas** desde la barra lateral de Home Assistant.
- El estado vive en `/data/consolas.sqlite`; fotos propias en `/data/media`.
- Auction Watch vive en `/data/auction-watch`, corre a las 09:15 y 17:10
  (`America/Montevideo`) y puede dispararse desde Oportunidades.
- Las opciones SMTP se configuran en Home Assistant y nunca forman parte del
  paquete ni del repositorio.

No usar una instancia local, puertos LAN, `homeassistant.local`, launchd, cron
ni Mail.app como alternativa de producción.

## Publicación

El código se prepara desde `console-collection` con:

```bash
./scripts/package-ha-addon.sh
```

El paquete publicado debe contener sólo código y recursos base: nunca `/data`,
`web/runtime`, medios manuales, SQLite, secretos, logs, corridas ni snapshots
runtime. Home Assistant actualiza el add-on desde su fuente de Store; esa
actualización conserva `/data`.

El procedimiento canónico y las validaciones están en
`docs/HA_ADDON_RUNTIME.md` del repositorio de ingeniería.
