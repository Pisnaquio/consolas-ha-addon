# Checklist Operativo de Auditoría

## Etapa 1: QA funcional y navegación

### Navegación y estructura
- Verificar navegación entre `index.html`, `database.html` y `console.html`.
- Confirmar que no hay enlaces rotos ni estados huérfanos.
- Validar orden visual: Registrados primero, Deseados después.

### Lógica de listas (crítica)
- Registrar si `ownershipType != none` aparece en Registrados.
- Registrar si `loQuiero=true` o `keepInWishlist=true` aparece en Deseados.
- Detectar casos inválidos: registrado con `loQuiero=false` y `keepInWishlist=false` que aún aparece en Deseados.
- Detectar duplicación innecesaria entre listas.

### Persistencia
- Editar campo desde UI, recargar, verificar persistencia.
- Verificar prioridad de datos: persistido > catálogo base > defaults.
- Revisar migraciones legacy sin sobrescribir estado del usuario.

### Filtros y búsqueda
- Validar filtros combinados.
- Detectar filtros que no afectan resultado.
- Detectar resultados incoherentes con estado real.

## Etapa 2: Auditoría de datos y contenido

### Duplicados y naming
- Identificar IDs duplicados por consola y global.
- Identificar nombres iguales con IDs distintos sospechosos.
- Detectar errores de naming (variantes inconsistentes, typos evidentes).

### Coherencia de consola y catálogo
- Verificar juegos en consola correcta.
- Detectar recomendados no curados o sin sentido.
- Detectar placeholders inventados como contenido real.

### Estado editable del usuario
- Revisar coherencia entre `ownershipType`, `loQuiero`, `keepInWishlist`.
- Detectar campos irrelevantes o contradictorios.
- Verificar juegos manuales (`sourceType=manual`) con misma lógica que catálogo.

## Etapa 3: Visual, UX e imágenes

### Covers e imágenes
- Verificar que todas las cards tengan `coverImage` o placeholder válido.
- Detectar imágenes rotas/missing.
- Detectar covers incorrectas o compartidas entre juegos distintos.
- Revisar casos sensibles de versiones gemelas.

### Presentación visual
- Revisar `object-fit: contain` para no recortar covers.
- Detectar cards deformadas/desbalanceadas.
- Detectar problemas de spacing, jerarquía y densidad visual.

### UX de interacción
- Detectar formularios fijos gigantes o intrusivos.
- Revisar que alta manual sea asistida (flujo liviano).
- Detectar filtros toscos y layout vertical excesivo.

## Criterio de descarte de hallazgos
No incluir hallazgos que no cumplan al menos uno:
- evidencia reproducible,
- impacto claro en uso/datos/UX,
- dirección de fix accionable.
