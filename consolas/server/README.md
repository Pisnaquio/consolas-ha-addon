# Consolas Server

Backend liviano para servir la web de colección desde Home Assistant y mover la
persistencia editable del navegador al servidor.

## Endpoints iniciales

- `GET /api/health`: estado del servicio, readiness y frescura de Auction Watch.
- `GET /api/readiness` (alias `/api/ready`): `200` cuando DB, estáticos y
  storage de Auction Watch están disponibles; `503` en caso contrario.
- `GET /api/state`: estado normalizado actual.
- `PUT /api/state`: reemplaza el estado normalizado.
- `GET /api/state/export`: export server-side listo para backup.
- `POST /api/state/restore`: restaura estado server-side con confirmación explícita.
- `POST /api/media`: guarda un `dataUrl` base64 en `/data/media`.
- `GET /media/<file>`: sirve media persistida.
- `GET /api/auction-watch`: expone el snapshot visible de auction-watch si existe.
- `POST /api/auction-watch/snapshot`: recibe y publica atómicamente el snapshot
  de la corrida antes de que salga el mail. Requiere el header
  `X-Consolas-Auction-Watch: 1`, el SHA-256 canónico en
  `X-Auction-Watch-Snapshot-Hash` y JSON válido con `runId` + `generatedAt`.
- `GET/POST /api/auction-watch/run-now`: consulta o encola una corrida manual.
- `POST /api/auction-watch/run-now/claim`: permite que el scheduler de la Mac reclame la solicitud pendiente.
- `POST /api/auction-watch/run-now/complete`: registra el resultado final para mostrarlo en la web.
- `GET /api/auction-watch/dismissals`: lista las oportunidades descartadas.
- `POST /api/auction-watch/dismissals`: descarta un lote por `sourceId` + `lotId`.
- `DELETE /api/auction-watch/dismissals?sourceId=...&lotId=...`: restaura un lote.

Los descartes viven en la tabla SQLite `auction_watch_dismissals`, separada de
`app_state`. No son ediciones de la colección: solo filtran la presentación del
snapshot, la UI y los siguientes mails, y pueden revertirse.

El cleanup automático de descartes requiere inventario autoritativo por fuente.
En `publicationLifecycle.sourceHealth`, el formato vigente por fuente es
`{"status":"success","inventoryAuthoritative":true}`. Los estados legacy en
forma de string siguen siendo aceptados, pero no inician ni consumen la gracia
de expiración porque no demuestran que se haya observado el inventario completo.

`auction_watch_dismissals` y `auction_watch_following` son decisiones mutuamente
excluyentes: descartar elimina el seguimiento y volver a seguir restaura el lote
quitándolo de descartados. La migración de SQLite conserva las filas existentes
y agrega de forma no destructiva los campos estructurados de resultado de las
solicitudes manuales (`run_id`, `snapshot_hash`, `snapshot_status`,
`email_status`, `overall_status`).

La publicación devuelve un recibo con el mismo `runId`, hash y `generatedAt`.
El servidor rechaza snapshots anteriores, empates contradictorios y un mismo
`runId` con contenido diferente. Un retry idéntico preserva `acceptedAt`. El
`GET /api/auction-watch` agrega `sync` (`current | stale | unavailable`) y
mantiene `snapshotHash` como alias superior; el hash siempre identifica el
snapshot crudo, antes de aplicar descartes. La frescura por defecto es de 36 h
y puede configurarse con `CONSOLAS_AUCTION_WATCH_STALE_AFTER_SECONDS`.

Las completions manuales usan compare-and-set: sólo una request `running` puede
cerrarse. Una entrega `delivery_pending` puede avanzar más tarde a
`completed`, para el mismo `runId`, cuando la outbox entrega el artefacto sin
repetir el escaneo.
`delivery_pending` también bloquea nuevas requests y puede actualizar su avance
de `snapshotStatus=failed` a `published`. Antes de aceptar `published`, el
servidor verifica que `runId` y hash coincidan con el snapshot crudo y su recibo;
los retries ya persistidos siguen siendo idempotentes aunque exista un snapshot
más nuevo.
Los recibos aceptados se conservan por `runId`, por lo que una completion tardía
no depende de que ese snapshot siga siendo el último publicado.
Mientras una búsqueda manual corre, el scheduler renueva un lease separado de
su hora original de inicio. Una solicitud con el lease vivo no puede habilitar
un segundo scan automático.

Las escrituras de descarte/restauración aceptan únicamente JSON desde la UI con
la cabecera `X-Consolas-Auction-Watch: 1`; `lotUrl` se limita a `http/https`.
La página de confirmación vuelve a buscar el lote canónico antes de habilitar el
botón y no permite ser embebida. El puerto LAN no debe publicarse directamente
en Internet: el backend general todavía no implementa autenticación de usuario.

Ademas, al iniciar o al escribir estado, el server migra automaticamente
referencias legacy `./runtime/media/...` hacia archivos reales en `/data/media`
y reescribe el estado a URLs `./media/...`.

La primera versión conserva el contrato actual de `DataStore`:

```json
{
  "version": 3,
  "user": {
    "overridesById": {},
    "additionsById": {},
    "detailEditsById": {}
  }
}
```

Esto permite una migración segura antes de partir el modelo a tablas
relacionales más finas.

## Backup y restore

### Export

`GET /api/state/export` devuelve un payload portable:

```json
{
  "exportedAt": "2026-07-10T00:00:00Z",
  "app": "consolas",
  "version": 3,
  "source": "server",
  "state": {
    "version": 3,
    "user": {
      "overridesById": {},
      "additionsById": {},
      "detailEditsById": {}
    }
  }
}
```

### Restore

`POST /api/state/restore` exige confirmación explícita:

```json
{
  "confirmReplace": true,
  "source": "manual-backup",
  "state": {
    "version": 3,
    "user": {
      "overridesById": {},
      "additionsById": {},
      "detailEditsById": {}
    }
  }
}
```

Regla:

- no usar restore como bootstrap rutinario
- si el server ya tiene datos reales, restaurar solo desde un backup intencional
