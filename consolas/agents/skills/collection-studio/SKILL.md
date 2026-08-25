# Collection Studio Variant (Local)

Skill local adaptada desde `~/Downloads/hostinger-premium-website/SKILL.md` para este proyecto.

## Objetivo
Crear páginas nuevas de alto impacto visual (HTML/CSS/JS sin build) reutilizando la data existente de la app de consolas/juegos.

## Reglas de adaptación para este repo
1. No reemplazar páginas existentes por defecto.
2. Crear páginas paralelas (`index-*.html`) y assets dedicados (`*.css`, `*.js`).
3. Leer siempre:
   - `web/data/consoles.json`
   - `web/data/console-games.json`
   - `window.DataStore` (overrides, additions, detail edits)
4. Mantener patrón sin módulos (`<script defer>` + IIFE).
5. Añadir cache-busting `?v=YYYYMMDDx`.
6. No romper ni pisar persistencia del usuario.
7. Priorización visual:
   - jerarquía fuerte
   - bloques editoriales
   - gradientes azules
   - microinteracciones sutiles
8. Clasificar extras no-juego (demo/soundtrack/artbook/etc.) fuera de métricas principales.

## Salida esperada
- Nueva página alternativa para explorar colección.
- Dashboard operativo centrado en “lo que tengo”.
- Links de navegación a `console.html?id=...`.

