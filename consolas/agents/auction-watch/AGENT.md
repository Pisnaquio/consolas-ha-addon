# auction-watch

Agente autonomo para monitorear remates online de Uruguay una o dos veces por dia.

## Mision
- Ejecutar las busquedas legacy de Bavastro y Castells y los adaptadores registrados de Remotes, TodoRemates y Prado Subastas.
- Guardar cada corrida con timestamp para poder auditar resultados y fallas.
- Mantener un espejo estable de la ultima corrida en `agents/auction-watch/runs/latest/`.
- Generar un resumen consolidado en Markdown para lectura rapida.
- Exportar un snapshot web read-only a `web/runtime/auction-watch.json` para uso local.
- En modo Home Assistant, publicar el snapshot y verificar el ACK antes de entregar el mail.

## Scope obligatorio
- Descubrir subastas activas de Bavastro con `buscador_bavastro.py --active-only`.
- Descubrir remates activos de Castells con `buscador_consolas_castells.py --discover-only`.
- Buscar matches de consolas en Bavastro con `buscador_consolas_bavastro.py`.
- Buscar matches de consolas en Castells con `buscador_consolas_castells.py`.
- Escanear las fuentes adicionales con `scripts/scan_extra_sources.py`.
- Incluir todos los matches activos; no aplicar un limite global al mail.
- Consolidar salidas, logs y estado general de la corrida.

## Guardrails
- No modificar catalogo ni persistencia editable de la app. La integracion web se limita al repositorio y snapshot read-only de oportunidades.
- No inventar remates ni resultados.
- No depender del `python3` global: usar siempre `.venv/bin/python`.
- No pisar los archivos root existentes como fuente principal de la automatizacion.
- Si una fuente falla, registrar la falla y continuar con la otra para evitar corridas vacias innecesarias.
- Preferir APIs publicas, feeds y endpoints HTTP estructurados. Usar HTML de detalle solo para completar campos no expuestos por esas interfaces.
- Nunca guardar ni registrar blobs de estado de sesion, datos de usuarios o pujas de terceros que no formen parte del metadata publico del lote.
- El snapshot web es solo lectura: no debe mezclarse con `DataStore` ni con estado editable del usuario.

## Salidas esperadas por corrida
- `runs/<timestamp>/summary.md`
- `runs/<timestamp>/run.json`
- `runs/<timestamp>/newsletter-preview.html`
- `runs/<timestamp>/logs/*.log`
- `runs/<timestamp>/auctions_bavastro_matches.csv`
- `runs/<timestamp>/consolas_castells_auctions.csv`
- `runs/<timestamp>/consolas_bavastro_matches.csv`
- `runs/<timestamp>/consolas_castells_matches.csv`
- `runs/<timestamp>/consolas_castells_matches_readable.md`
- `runs/<timestamp>/consolas_extra_matches.csv`
- `runs/<timestamp>/extra_sources_status.json`
- `runs/<timestamp>/auction-watch.json`
- `runs/<timestamp>/delivery.json`
- `web/runtime/auction-watch.json` (espejo local ignorado por Git)

## Agregar una fuente nueva
- Implementar un adaptador en `agents/auction-watch/sources/` con `collect(session, timeout)` y, opcionalmente, `enrich_lots(session, lots, timeout)`.
- Devolver `AuctionGroup`, `AuctionLot` y `SourceScanResult` del modelo canonico en `sources/model.py`.
- Registrar una sola entrada `SourceSpec` en `sources/registry.py`.
- `collect` debe usar la interfaz estructurada mas estable disponible; `enrich_lots` recibe unicamente candidatos que ya pasaron el filtro, para evitar visitas HTML innecesarias.
- Una falla de una fuente no bloquea las demas. Una corrida parcial se considera entregada para que el scheduler no reenvie el mismo mail cada cinco minutos.

## Entrada unica para scheduler
- Wrapper shell: `agents/auction-watch/scripts/run_watch.sh`
- Runner Python: `agents/auction-watch/scripts/run_watch.py`

## Notificaciones
- macOS: activas por default al terminar la corrida, via `osascript`, incluso sin configurar mail.
- Mail: por default via `Mail.app` si existe `agents/auction-watch/notification.env` con destinatario.
- `AUCTION_WATCH_APP_BASE_URL` habilita el enlace `Descartar` y la sincronización de decisiones con la API de la app.
- El enlace del mail abre una confirmación: un `GET` nunca descarta por sí solo porque los clientes de correo pueden precargar URLs.
- El snapshot publicado contiene inventario crudo: `dismissals-cache.json` nunca elimina lotes del artefacto que recibe HA.
- `AUCTION_WATCH_PUBLICATION_MODE=ha-required` exige publicación y ACK verificable; si falla, el mail queda pendiente. `local-only` omite HA y no genera enlaces de acción.
- En `ha-required`, el boletín se reconstruye después del ACK usando sólo los matches visibles del `GET /api/auction-watch`; un descarte hecho en HA entre scan y entrega no puede reaparecer en el mail.
- `scanStatus` usa `success|partial|failed`. La publicación registra `snapshotStatus=skipped|published|failed`; el mail registra `emailStatus=disabled|sent|failed|pending|uncertain` y la corrida `overallStatus=completed|degraded|delivery_pending|failed`.
- El transporte se registra como `sending` antes de invocarlo. Un crash, timeout o resultado que podría haber sido aceptado queda `uncertain` y nunca se reenvía automáticamente.
- `sendmail`: alternativa opcional headless. Ahora arma mail multipart con version HTML, pero solo sirve si el sistema local de mail esta realmente configurado para salir.
- SMTP: alternativa opcional con credenciales explicitas. Es la via correcta para boletin HTML real sin depender de sesion grafica.
- Politica por default:
  - banner de macOS: cada corrida
  - mail: solo si hay matches o si la corrida falla
- Log de notificaciones por corrida: `runs/<timestamp>/logs/notifications.log`

## Instalacion sugerida
- Una vez por dia: `agents/auction-watch/scripts/install_launch_agent.sh daily`
- Dos veces por dia: `agents/auction-watch/scripts/install_launch_agent.sh twice`
- Desinstalar scheduler: `agents/auction-watch/scripts/install_launch_agent.sh uninstall`
- El LaunchAgent mantiene un loop supervisado por macOS y chequea por default cada minuto si quedo pendiente una ventana. Si el proceso cae, `launchd` lo reinicia.
- Una ventana solo se marca cumplida por el `runId` que terminó su entrega. Una falla de publicación o transporte se reintenta desde el manifiesto existente, sin volver a escanear, con backoff.
- El scheduler reconstruye ventanas cumplidas desde la outbox y persiste los ACK manuales pendientes antes del POST. Mientras falte ese ACK no reclama ni inicia otro scan.
- Ventanas recomendadas hoy:
  - `daily`: 17:10
  - `twice`: 09:15 y 17:10

## Instalacion headless sugerida
- Una vez por dia sin depender de sesion grafica: `agents/auction-watch/scripts/install_cron_schedule.sh daily`
- Dos veces por dia sin depender de sesion grafica: `agents/auction-watch/scripts/install_cron_schedule.sh twice`
- Desinstalar cron headless: `agents/auction-watch/scripts/install_cron_schedule.sh uninstall`
- Para mail headless real, usar `AUCTION_WATCH_EMAIL_METHOD=smtp`. `Mail.app` depende de GUI y no es la opcion correcta para cron.
- El cron headless corre un chequeo liviano cada 5 minutos y recupera corridas perdidas cuando la Mac vuelve a estar despierta.

## Notas operativas
- `buscador_bavastro.py` tiene dos usos distintos:
  - historico/manual: barrido por query `electronica` para construir dataset historico
  - diario/agent: remates activos publicados, sin limitar a `electronica`
- Keywords, scoring y flags compartidos viven en `auction_search_config.py`.
- El parseo legacy de Bavastro y Castells es incremental. Los adaptadores registrados consultan su inventario activo en cada corrida.
- El estado incremental vive en `agents/auction-watch/state.json`.
- El cache de matches legacy activos también vive en `state.json`; no depende de conservar el historial de `runs/`.
- La cola durable de entrega vive en `agents/auction-watch/delivery-outbox.json`. Solo guarda referencias y estado de retry; el contenido preparado está en `runs/<runId>/delivery.json`. Ninguno persiste usuario ni contraseña SMTP.
- Para reintentar manualmente una entrega sin escanear: `.venv/bin/python agents/auction-watch/scripts/run_watch.py --deliver-run <runId>`.
- Si el resultado del mail quedó ambiguo, el único reintento permitido es explícito: agregar `--force-uncertain-email-retry`; puede duplicar un mensaje ya aceptado.
- El cache operativo de descartes vive en `agents/auction-watch/dismissals-cache.json`; sólo sirve para la proyección local degradada. La fuente de verdad y el filtrado de la publicación siguen en la tabla separada del servidor.
- Cuando cambia el perfil de busqueda, incrementar `STATE_SCHEMA_VERSION` en el runner para reprocesar una vez todos los grupos legacy activos y no dejar afuera coincidencias incorporadas por keywords nuevas.
- Los lotes que quieras seguir de cerca viven en `agents/auction-watch/watchlist.json`.
- Para administrarlos sin tocar JSON a mano:
  - ver destacados actuales: `python3 agents/auction-watch/scripts/manage_watchlist.py list`
  - ver candidatos activos de la ultima corrida: `python3 agents/auction-watch/scripts/manage_watchlist.py active`
  - promover un lote activo a destacado: `python3 agents/auction-watch/scripts/manage_watchlist.py promote --source castells --lot-id 559500 --priority 1 --notes "Seguir de cerca"`
  - sacar un lote del destacado: `python3 agents/auction-watch/scripts/manage_watchlist.py remove --lot-id 559500`
  - si hay varios lotes en watchlist, el destacado principal se elige por `priority` y despues por cierre mas cercano
- El runner archiva cada ejecucion y rota corridas viejas.
- Si Bavastro no detecta remates activos publicados, el paso de matches de Bavastro se saltea sin caer en el fallback historico.
- El resumen consolidado vive en cada corrida y tambien en `runs/latest/summary.md`.
- Cada corrida tambien deja `newsletter-preview.html` para validar visualmente el boletin antes o despues de ajustar el transporte de mail.
- Template de configuracion de mail: `agents/auction-watch/notification.env.example`
