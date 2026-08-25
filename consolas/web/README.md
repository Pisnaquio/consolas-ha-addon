# Catálogo de Consolas

Web personal para gestionar colección y wishlist de consolas. Hoy puede correr:

- local/browser-only para desarrollo
- como add-on de Home Assistant con persistencia server-side

## Estructura

- `web/index.html`: estructura de la página.
- `web/database.html`: base general de consolas (cronológica + agrupada por generación).
- `web/console.html`: vista de detalle por consola.
- `web/styles.css`: diseño visual y responsive.
- `web/app.js`: lógica de render, filtros y buscador.
- `web/database.js`: filtros de marca/generación/tipo + agrupación colapsable.
- `web/console.js`: lógica de detalle (accesorios, juegos, oportunidades, precios).
- `web/data/consoles.json`: fuente única de datos para consolas.
- `web/data/consoles-base.json`: base global de consolas (sin duplicar revisiones).
- `web/assets/photos/`: carpeta para futuras fotos.
- `server/app.py`: backend same-origin + SQLite para Home Assistant.
- `ha-addon/consolas/`: add-on autocontenido de Home Assistant.

## Cómo abrirla

### Desarrollo local

Opción recomendada (evita problemas de `fetch` local):

```bash
cd web
python3 -m http.server 8080
```

Luego abrir `http://localhost:8080`.

### Home Assistant

Ver flujo operativo en:

- `server/README.md`
- `ha-addon/consolas/README.md`
- `docs/HA_ADDON_RUNTIME.md`

## Base general de consolas

- URL: `http://localhost:8080/database.html`
- Base única integrada: hogar + portátiles conviven en el mismo dataset cronológico.
- Vista 1: cronológica completa (año ascendente).
- Vista 2: agrupada por generación (expandible/colapsable).
- Filtros combinables: marca + generación + tipo + buscador.
- Navegación por marca mediante chips rápidos.
- Las generaciones y filtros son vistas de visualización, no listas separadas.
- Estado personal integrado: desde la base general podés marcar `tengo/quiero` y se refleja en el catálogo personal.

### Sincronización de estado personal

- La base general usa un mapeo entre consola base y tu `consoles.json`.
- Con backend activo, los cambios viajan a `./api/state` y quedan persistidos en SQLite server-side.
- Sin backend, la app cae a persistencia local del navegador.

### Estructura de cada consola base

```json
{
  "id": "sony-playstation-2",
  "nombre": "Sony PlayStation 2",
  "marca": "Sony",
  "generacion": "Generacion 6",
  "anioLanzamiento": 2000,
  "tipo": "hogar",
  "notas": "Una de las consolas más vendidas de la historia.",
  "revisiones": ["Fat", "Slim"],
  "tracking": {
    "tengo": null,
    "quiero": null,
    "completa": null,
    "funcionando": null,
    "accesorios": [],
    "juegos": [],
    "precioPriceChart": null,
    "precioGameStop": null,
    "precioEbaySold": null,
    "precioCIB": null,
    "precioObjetivoCompra": null
  }
}
```

## Cómo agregar una consola nueva

Catálogo base versionado:

1. Abrí `web/data/consoles.json`.
2. Copiá un objeto existente dentro de `consolas`.
3. Completá/ajustá estos campos:

```json
{
  "id": "slug-unico",
  "nombre": "Nombre de consola",
  "fabricante": "Nintendo|Sony|Sega|...",
  "generacion": "5ta",
  "anioLanzamiento": 1996,
  "tengo": false,
  "estado": "Buscando",
  "funcionando": null,
  "accesorios": [],
  "juegos": [],
  "notas": "",
  "precioPriceChart": null,
  "precioGameStop": null,
  "precioEbaySold": null,
  "precioCIB": null,
  "precioObjetivoCompra": null,
  "precioPagado": null,
  "fotos": [],
  "categoria": "wishlist"
}
```

4. Guardá y recargá el navegador.

Importante:

- esto agrega o edita catálogo base
- no es el flujo correcto para estado personal del usuario cuando la app corre en HA
- el estado personal debe entrar por UI y persistirse en `/api/state`

## Campos sugeridos

- `categoria`: `coleccion` o `wishlist`.
- `funcionando`: `true`, `false` o `null` (si no aplica / no se sabe).
- `precioPagado`: opcional (`null` si no lo querés cargar).
- `fotos`: array de rutas (ejemplo: `["assets/photos/ps1-front.jpg"]`).
- `fotosPropias`: array para priorizar fotos tuyas (si existe, se muestra antes que `fotos`).
- `precioPriceChart`: referencia general de mercado desde PriceCharting.
- `precioEbaySold`: referencia de mercado real por ventas concretadas.
- `precioGameStop`: referencia retail alta / techo razonable.
- `precioCIB`: referencia para consola completa en caja.
- `precioObjetivoCompra`: precio oportunidad ideal (agresivo, pero realista).
- `priceChartingUrl`: link de referencia externa (opcional).
- `oportunidades`: array de oportunidades (remates/subastas) por consola.

## Próxima mejora simple (opcional)

Agregar una segunda tabla JSON para "oportunidades" (remates/subastas) y mostrarla como lista en una sección dedicada.

## Portadas de juegos (obligatorio)

Regla actual del proyecto:

- Ningún juego puede quedar sin imagen.
- Cada juego debe tener `coverImage` (o fallback automático).

### Modelo de datos de juego

```json
{
  "id": "pokemon-emerald",
  "nombre": "Pokemon Emerald",
  "franquicia": "Pokemon",
  "genero": "RPG",
  "prioridad": "alta",
  "loQuiero": true,
  "loTengo": false,
  "ownershipType": "none",
  "keepInWishlist": true,
  "condicion": "",
  "notas": "",
  "coverImage": "./assets/game-covers/gba/pokemon-emerald.png",
  "coverUrl": "./assets/game-covers/gba/pokemon-emerald.png",
  "imageSource": "steamgriddb",
  "imageStatus": "found",
  "imageSearchName": "Pokemon Emerald",
  "priceRange": {
    "low": 140,
    "mid": 180,
    "high": 220,
    "notes": ""
  }
}
```

Notas de gestión:

- `ownershipType`: `none | physical | digital | both`.
- `keepInWishlist`: si ya lo tenés, define si sigue visible en deseados.
- `standby`: pausa temporal para sacarlo de “Lo quiero” sin perderlo del catálogo.
- `prioridad`: editable desde UI (`alta`, `media-alta`, `media`, `baja`).

### Cadena automática de fuentes de portada

El script `web/scripts/fetch-game-covers.mjs` intenta esta prioridad:

1. SteamGridDB
2. IGDB
3. TheGamesDB
4. Bing Image Search (fallback)

Si no encuentra portada válida, aplica fallback consistente:

- `./assets/photos/game-placeholder.svg`

### Variables de entorno soportadas

- `SGDB_API_KEY`
- `IGDB_CLIENT_ID`
- `IGDB_CLIENT_SECRET`
- `TGDB_API_KEY` o `THEGAMESDB_API_KEY`
- `BING_IMAGE_API_KEY`
- `BING_IMAGE_ENDPOINT` (opcional)

### Uso del script

Todas las consolas:

```bash
node web/scripts/fetch-game-covers.mjs
```

Solo una consola:

```bash
node web/scripts/fetch-game-covers.mjs --console switch
```

Forzar recálculo de portadas existentes:

```bash
node web/scripts/fetch-game-covers.mjs --force
```

### Validación obligatoria

Para garantizar que ningún juego quede sin imagen:

```bash
node web/scripts/validate-game-covers.mjs
```

La validación falla si:

- falta `coverImage`
- falta `imageSource`, `imageStatus` o `imageSearchName`
- `imageStatus=placeholder` no apunta al fallback
- la ruta local de imagen no existe

## Persistencia centralizada (fuente de verdad)

La app usa esta jerarquía:

1. si hay backend same-origin: `GET/PUT ./api/state`
2. SQLite server-side en Home Assistant (`/data/consolas.sqlite`)
3. `IndexedDB` (`consolas-app-db` / store `app_state` / key `root`)
4. shadow/fallback en `localStorage["consolas.appState.v2"]`
5. bootstrap privado opcional en `web/runtime/user-bootstrap.json`
6. catálogo base versionado en `web/data/*.json`

Archivo central:

- `web/data-store.js`

Estructura normalizada:

```json
{
  "version": 3,
  "user": {
    "overridesById": {},
    "additionsById": {},
    "detailEditsById": {}
  },
  "meta": {
    "migratedLegacy": true,
    "storageBackend": "server",
    "updatedAt": "2026-07-10T00:00:00.000Z"
  }
}
```

Prioridad de datos al render:

1. Estado persistido del usuario
2. Catálogo base
3. Defaults vacíos

Notas importantes:

- `localStorage` ya no es la fuente primaria.
- si `storageBackend=server`, el server manda
- `web/runtime/` está reservado para material privado/local y no entra al repo.
- Si un navegador arranca sin estado y existe `web/runtime/user-bootstrap.json`, la app puede importarlo una sola vez como puente de migración local.
- no reimportar `web/runtime/user-bootstrap.json` sobre una instancia HA con datos reales salvo restauración intencional.
- Diferentes navegadores/perfiles no comparten `IndexedDB` ni `localStorage` mientras no exista backend o cuando la app cayó a fallback local.
