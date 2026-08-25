# Consolas

Add-on local de Home Assistant para servir la app de colección de consolas con
persistencia server-side.

## Qué incluye esta etapa

- Web estática actual servida desde el add-on.
- API local same-origin.
- SQLite en `/data/consolas.sqlite`.
- Media storage en `/data/media`.
- Ingress en Home Assistant.
- Puerto LAN opcional `8788`.

## Instalación / actualización local

Desde el repo:

```bash
./scripts/release-ha-addon-checklist.sh
./scripts/package-ha-addon.sh
```

Copiar `dist/ha-addon/consolas/` al directorio de add-ons locales de Home
Assistant, por ejemplo:

```bash
rsync -av --delete dist/ha-addon/consolas/ "$ADDON_TARGET/"
```

Luego en Home Assistant:

1. Settings > Add-ons > Add-on Store.
2. Menu > Check for updates o reload local add-ons.
3. Instalar o reconstruir `Consolas`.
4. Start.
5. Abrir por Ingress o por `http://HA-IP:8788`.

## Alcance operativo esperado

Para este proyecto, el flujo de publicación queda así:

1. El dev de `consolas` actualiza la app/add-on.
2. Empaqueta con `./scripts/package-ha-addon.sh`.
3. Copia al directorio local de add-ons configurado para tu instalación.
4. En HA se hace `rebuild/restart` del add-on `Consolas`.
5. Desde el hilo operativo solo se valida que la UI cargue en HA y que `GET /api/health` responda.

## Persistencia

La app usa `./api/state` si el backend está disponible. En desarrollo local sin
backend mantiene el comportamiento anterior con IndexedDB.

Si el backend arranca vacío y el navegador actual tiene datos locales, la app
siembra el servidor una sola vez con ese estado. Si el servidor ya tiene datos,
el servidor gana para evitar que un navegador viejo pise la fuente compartida.
