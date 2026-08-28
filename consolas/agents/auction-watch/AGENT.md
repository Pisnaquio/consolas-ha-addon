# Auction Watch

Agente de oportunidades que corre únicamente dentro del add-on `Consolas` de
Home Assistant. No tiene scheduler ni notificaciones operativas en la Mac.

## Runtime canónico

- Scheduler interno del add-on: `09:15` y `17:10`, `America/Montevideo`.
- Runtime persistente: `/data/auction-watch`.
- API de publicación y cola manual: backend same-origin del add-on.
- Entrega: SMTP mediante opciones privadas del add-on.
- UI: página Oportunidades de Consolas por Ingress.

No ejecutar ni instalar `launchd`, cron, `osascript`, Mail.app ni una segunda
instancia del agente fuera de Home Assistant.

## Responsabilidades

- Descubrir subastas activas de Bavastro y Castells.
- Escanear Remotes, TodoRemates y Prado Subastas mediante los adaptadores
  registrados.
- Mantener incrementalidad, watchlist, historial y outbox en `/data`.
- Publicar el snapshot antes de habilitar el mail.
- Continuar ante una falla parcial de fuente y declararla en el resultado.
- En cada corrida consultar todos los grupos activos descubiertos. Los sets
  `processed_*` y la watchlist son sólo telemetría/contexto y no habilitan
  scans incrementales que omitan grupos.
- Guardar recibos por grupo y declarar `inventoryAuthoritative` sólo con
  discovery completa, cobertura completa y recibos `complete`; un refresh
  parcial conserva el cache previo y no elimina oportunidades omitidas.
- La eliminación y el lifecycle son por grupo: un recibo completo puede
  retirar omitidos de ese grupo, pero nunca de grupos parciales, fallidos u
  omitidos. Una discovery vacía sin evidencia estructural válida es fallo,
  no inventario vacío.
- Mantener lifecycle de oportunidades con primera/última aparición y contar
  nuevas, detectadas, visibles, descartadas y removidas sin alterar el score.

No modificar catálogo, SQLite de colección ni estado editable de usuario. Las
oportunidades automáticas son read-only y los descartes/seguimientos viven en
las tablas específicas del backend.

## Contrato de entrega

Una corrida está completa sólo cuando el `runId` y hash coinciden entre scan,
snapshot publicado y mail. Los retries reutilizan el manifiesto ya creado: no
inician un segundo scan ni reenvían automáticamente un mail ambiguo.

Estados canónicos:

- `scanStatus`: `success | partial | failed`
- `snapshotStatus`: `skipped | published | failed`
- `emailStatus`: `disabled | pending | sent | failed | uncertain`
- `overallStatus`: `completed | degraded | delivery_pending | failed`

Consultar [`docs/AUCTION_WATCH_RELIABILITY.md`](../../docs/AUCTION_WATCH_RELIABILITY.md)
para el contrato completo y [`docs/HA_ADDON_RUNTIME.md`](../../docs/HA_ADDON_RUNTIME.md)
para operación y despliegue.

## Desarrollo y empaquetado

El código puede probarse localmente, pero la prueba operativa final siempre se
hace dentro de HA. El paquete copia únicamente código y recursos base; excluye
`notification.env`, estado, outbox, corridas, logs, medios y cualquier otro
runtime mutable.

No commitear secretos, datos de usuario, snapshots runtime ni resultados de
corridas.
