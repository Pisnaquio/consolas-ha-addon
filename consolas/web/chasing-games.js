(() => {
  /**
   * Collection Radar · Búsquedas.
   *
   * Evolución de la página de Chasing Games: las búsquedas ahora son perfiles
   * persistentes con criterios estructurados y lifecycle explícito. Todo el
   * estado sale de `RadarRepository`; esta página sólo decide cómo mostrarlo.
   *
   * Los títulos y descripciones que llegan de un marketplace son datos externos:
   * se escapan siempre y nunca se interpretan como instrucciones.
   */
  const repository = window.RadarRepository;
  const root = document.getElementById("chasingGamesRoot");

  const STATUS_FILTERS = [
    { id: "all", label: "Todas" },
    { id: "active", label: "Activas" },
    { id: "draft", label: "Propuestas" },
    { id: "paused", label: "Pausadas" },
    { id: "archived", label: "Archivadas" }
  ];

  const CONDITION_OPTIONS = ["any", "new", "used", "refurbished"];
  const COMPLETENESS_OPTIONS = ["any", "loose", "boxed", "cib", "sealed"];
  const REQUIREMENT_OPTIONS = ["any", "preferred", "required"];
  const TYPE_OPTIONS = ["chase", "console", "lot", "upgrade", "discovery", "master"];
  const ENTITY_TYPE_OPTIONS = ["", "console", "game", "accessory"];
  const ENTITY_TYPE_LABELS = {
    "": "Sin vincular",
    console: "Una consola",
    game: "Un juego",
    accessory: "Un accesorio",
  };
  const PRIORITY_OPTIONS = ["alta", "media-alta", "media", "baja"];

  /**
   * Cómo se ordenan las búsquedas. "Como las trae el radar" es el orden del
   * servidor —estado y fecha—, y es el default porque es el único que no impone
   * un criterio propio.
   */
  const SORT_OPTIONS = [
    { id: "default", label: "Como las trae el radar" },
    { id: "name", label: "Nombre (A-Z)" },
    { id: "priority", label: "Prioridad" },
    { id: "results", label: "Más resultados primero" },
    { id: "checked", label: "Buscadas hace más tiempo" },
  ];

  /**
   * Cómo se ordenan las publicaciones DENTRO de una búsqueda. Es distinto de
   * `SORT_OPTIONS`, que ordena las búsquedas entre sí.
   *
   * El orden del servidor es por score: la recomendación del radar, mejor
   * primero. Sirve para decidir rápido y no sirve para comparar: con once
   * publicaciones del mismo juego a precios distintos, lo que se quiere es
   * verlas juntas y baratas primero.
   *
   * «Juego y precio» ordena por título y desempata por precio, así las copias
   * del mismo juego quedan pegadas y la más barata arriba. No se infiere qué
   * juego es: se agrupa por lo que el título dice, que es el único dato que hay.
   */
  const RESULT_SORT_OPTIONS = [
    { id: "default", label: "Como los trae el radar" },
    { id: "title-price", label: "Juego y precio" },
    { id: "price-asc", label: "Precio: más barato primero" },
    { id: "price-desc", label: "Precio: más caro primero" },
  ];

  const priceOf = (result) => {
    const value = Number(result?.priceAmount);
    return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
  };

  /** El título sin ruido de plataforma, año ni condición: lo que queda nombra al juego. */
  const titleKey = (result) =>
    String(result?.title || "")
      .toLowerCase()
      .replace(/\b(sony|playstation portable|playstation|psp|ps[1-5]|nintendo|sega|xbox)\b/g, " ")
      .replace(/\b(19|20)\d{2}\b/g, " ")
      .replace(/\b(cib|complete|sealed|new|tested|rare|w\/|with|manual|game|only|version)\b/g, " ")
      .replace(/[^a-z0-9 ]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  function sortResults(results, mode) {
    const list = [...(results || [])];
    if (mode === "price-asc") return list.sort((a, b) => priceOf(a) - priceOf(b));
    if (mode === "price-desc") return list.sort((a, b) => priceOf(b) - priceOf(a));
    if (mode === "title-price") {
      return list.sort((a, b) => {
        const byTitle = titleKey(a).localeCompare(titleKey(b), "es");
        return byTitle !== 0 ? byTitle : priceOf(a) - priceOf(b);
      });
    }
    return list;
  }

  /**
   * Preferencias de la vista, no estado de colección: viven en el navegador y
   * nunca tocan `/api/state`. Con ochenta búsquedas apiladas, cómo las mirás es
   * tan tuyo como qué buscás — y tiene que sobrevivir a recargar la página.
   */
  const PREFS_KEY = "consolas.radar.searchView";

  function loadPrefs() {
    try {
      const raw = JSON.parse(window.localStorage?.getItem(PREFS_KEY) || "{}");
      return {
        sortBy: SORT_OPTIONS.some((o) => o.id === raw.sortBy) ? raw.sortBy : "default",
        resultSortBy: RESULT_SORT_OPTIONS.some((o) => o.id === raw.resultSortBy)
          ? raw.resultSortBy
          : "title-price",
        // Plegado por defecto: el resumen se lee igual y el detalle deja de
        // empujar las búsquedas fuera de la primera pantalla.
        coverageOpen: raw.coverageOpen === true,
        expanded: Array.isArray(raw.expanded) ? new Set(raw.expanded) : new Set(),
      };
    } catch {
      return { sortBy: "default", resultSortBy: "title-price", coverageOpen: false, expanded: new Set() };
    }
  }

  function savePrefs() {
    try {
      window.localStorage?.setItem(
        PREFS_KEY,
        JSON.stringify({ sortBy, resultSortBy, coverageOpen, expanded: [...expanded] })
      );
    } catch {
      // Sin localStorage la vista funciona igual; sólo no recuerda la elección.
    }
  }

  let statusFilter = "all";
  let editingId = "";
  let prefillName = "";
  let prefillPlatform = "";
  let manualEntryId = "";
  let dismissId = "";
  let lotCalcId = "";
  let lotCalcPieceCount = 3;
  let lotCalcResult = null;
  // La previa es de esta sesión y de una sola búsqueda por vez: no se guardó
  // nada en el servidor, así que tampoco se conserva acá.
  let previewId = "";
  let previewData = null;
  let creating = false;
  const prefs = loadPrefs();
  let sortBy = prefs.sortBy;
  let resultSortBy = prefs.resultSortBy;
  let coverageOpen = prefs.coverageOpen;
  const expanded = prefs.expanded;
  let feedback = "";
  let feedbackTone = "info";
  let busy = false;

  const escapeHtml = (value = "") =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  const dateLabel = (value) =>
    value
      ? new Intl.DateTimeFormat("es-UY", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
      : "todavía no buscado";

  function setFeedback(message, tone = "info") {
    feedback = message || "";
    feedbackTone = tone;
  }

  async function reload() {
    await Promise.all([
      repository.load(),
      repository.loadRuns().catch(() => null),
      repository.loadCoverage().catch(() => null), // complementario: sin esto el inventario igual sirve
    ]);
    render();
  }

  /** Envuelve una acción de escritura: feedback, bloqueo y recarga consistentes. */
  async function perform(pendingMessage, action, successMessage, onSuccess) {
    if (busy) return false;
    busy = true;
    setFeedback(pendingMessage);
    render();
    try {
      await action();
      busy = false;
      setFeedback(successMessage, "success");
      onSuccess?.();
      await reload();
      return true;
    } catch (error) {
      busy = false;
      setFeedback(error.message, "error");
      render();
      return false;
    }
  }

  function optionList(values, selected, labelFor) {
    return values
      .map(
        (value) =>
          `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(
            labelFor(value)
          )}</option>`
      )
      .join("");
  }

  function sourceCheckboxes(selected = []) {
    return repository
      .getSources()
      .map((source) => {
        const checked = selected.includes(source.id) ? " checked" : "";
        const note = source.executable
          ? ""
          : `<span class="radar-source-note">${escapeHtml(source.unavailableReason || "Todavía no ejecutable.")}</span>`;
        return `<label class="radar-source-option${source.executable ? "" : " is-blocked"}">
          <input type="checkbox" name="sources" value="${escapeHtml(source.id)}"${checked} />
          <span>${escapeHtml(source.label)}</span>
          ${note}
        </label>`;
      })
      .join("");
  }

  /** Ninguna marcada equivale a todas: una búsqueda nueva corre en los tres slots. */
  function slotCheckboxes(selected = []) {
    return repository
      .getSlots()
      .map((slot) => {
        const checked = !selected.length || selected.includes(slot.slotKey) ? " checked" : "";
        return `<label class="radar-source-option">
          <input type="checkbox" name="slots" value="${escapeHtml(slot.slotKey)}"${checked} />
          <span>${escapeHtml(slot.label)}</span>
        </label>`;
      })
      .join("");
  }

  /** Un único formulario para alta y edición: los criterios no se bifurcan. */
  function searchForm(item, { formId, submitLabel, cancelAction }) {
    const criteria = item.criteria || {};
    const sources = item.sources || ["ebay-us"];
    return `<form class="radar-form" id="${escapeHtml(formId)}">
      <div class="radar-form-grid">
        <label>Nombre
          <input name="name" required maxlength="200" value="${escapeHtml(item.name || "")}"
                 placeholder="Ej: PlayStation 2 lista para usar" />
        </label>
        <label>Plataforma
          <input name="platform" maxlength="100" value="${escapeHtml(item.platform || "")}" placeholder="Ej: PS2" />
        </label>
        <label>Tipo
          <select name="searchType">${optionList(TYPE_OPTIONS, item.searchType || "chase", (value) =>
            repository.getTypeLabel(value)
          )}</select>
        </label>
        <label>Prioridad
          <select name="priority">${optionList(PRIORITY_OPTIONS, item.priority || "media", (value) =>
            repository.getPriorityLabel(value)
          )}</select>
        </label>
      </div>
      <details class="radar-advanced">
        <summary>Criterios de búsqueda</summary>
        <div class="radar-form-grid">
          <label>Términos requeridos
            <input name="includeTerms" maxlength="400" value="${escapeHtml((criteria.includeTerms || []).join(", "))}"
                   placeholder="tested, OEM controller" />
          </label>
          <label>Sinónimos aceptados
            <input name="anyTerms" maxlength="400" value="${escapeHtml((criteria.anyTerms || []).join(", "))}"
                   placeholder="slim, fat" />
          </label>
          <label>Términos excluidos
            <input name="excludeTerms" maxlength="400" value="${escapeHtml((criteria.excludeTerms || []).join(", "))}"
                   placeholder="parts, repair, as-is" />
          </label>
          <label>Región
            <input name="region" maxlength="60" value="${escapeHtml(criteria.region || "")}" placeholder="NTSC-U/C" />
          </label>
          <label>Qué persigue
            <select name="entityType">${optionList(ENTITY_TYPE_OPTIONS, item.entityType || "", (value) =>
              ENTITY_TYPE_LABELS[value]
            )}</select>
          </label>
          <label>Id en el catálogo
            <input name="entityId" maxlength="120" value="${escapeHtml(item.entityId || "")}" placeholder="ps2, aladdin" />
            <small class="radar-field-hint">El id del catálogo, no el nombre. Con esto aparece «Registrar compra».</small>
          </label>
          <label>Consola de ese juego
            <input name="entityConsoleId" maxlength="120" value="${escapeHtml(item.entityConsoleId || "")}" placeholder="snes" />
            <small class="radar-field-hint">Sólo para juegos y accesorios: el mismo juego existe en varias plataformas.</small>
          </label>
          <label>Condición
            <select name="condition">${optionList(
              CONDITION_OPTIONS,
              criteria.condition || "any",
              (value) => repository.CONDITION_LABELS[value]
            )}</select>
          </label>
          <label>Completitud
            <select name="completeness">${optionList(
              COMPLETENESS_OPTIONS,
              criteria.completeness || "any",
              (value) => repository.COMPLETENESS_LABELS[value]
            )}</select>
          </label>
          <label>Probado
            <select name="tested">${optionList(
              REQUIREMENT_OPTIONS,
              criteria.tested || "any",
              (value) => repository.REQUIREMENT_LABELS[value]
            )}</select>
          </label>
          <label>Piezas originales
            <select name="originalParts">${optionList(
              REQUIREMENT_OPTIONS,
              criteria.originalParts || "any",
              (value) => repository.REQUIREMENT_LABELS[value]
            )}</select>
          </label>
          <label>Precio máximo del artículo
            <input name="maxItemPrice" type="number" min="0" step="1"
                   value="${escapeHtml(criteria.maxItemPrice ?? "")}" placeholder="300" />
          </label>
          <label>Precio objetivo
            <input name="targetItemPrice" type="number" min="0" step="1"
                   value="${escapeHtml(criteria.targetItemPrice ?? "")}" placeholder="80" />
            <small class="radar-field-hint">No filtra: es el precio de publicación al que comprás sin pensarlo. Cuando algo lo cruza, te avisa en el momento.</small>
          </label>
          <label>Mínimo de piezas del lote
            <input name="minLotSize" type="number" min="1" step="1"
                   value="${escapeHtml(criteria.minLotSize ?? "")}" placeholder="6" />
            <small class="radar-field-hint">Descarta lo que declare menos piezas. Un lote que no las cuenta no se descarta: queda marcado como sin verificar.</small>
          </label>
        </div>
        <div class="radar-form-checks">
          <label><input type="checkbox" name="returnsRequired"${criteria.returnsRequired ? " checked" : ""} /> Exigir devolución</label>
          <label><input type="checkbox" name="freeShippingOnly"${criteria.freeShippingOnly ? " checked" : ""} /> Sólo envío gratis</label>
        </div>
        <fieldset class="radar-sources">
          <legend>Fuentes</legend>
          ${sourceCheckboxes(sources)}
        </fieldset>
        <fieldset class="radar-sources">
          <legend>Horarios</legend>
          ${slotCheckboxes(item.slots || [])}
        </fieldset>
        <label class="radar-notes">Consultas extra
          <textarea name="queries" rows="4"
                    placeholder="God of War Chains of Olympus PSP&#10;Patapon PSP">${escapeHtml(
                      (criteria.queries || []).join("\n")
                    )}</textarea>
          <small class="radar-field-hint">Una por línea. Cada una se busca por separado, además de la consulta principal: así una sola búsqueda persigue una lista de títulos. Los criterios y los términos excluidos se aplican igual a todas.</small>
        </label>
        <label class="radar-notes">Notas
          <textarea name="notes" maxlength="600" rows="2"
                    placeholder="Qué verificar antes de comprar">${escapeHtml(item.notes || "")}</textarea>
        </label>
      </details>
      <div class="card-actions radar-form-actions">
        <button class="btn-link btn-primary" type="submit"${busy ? " disabled" : ""}>${escapeHtml(submitLabel)}</button>
        <button class="btn-link" type="button" data-cancel="${escapeHtml(cancelAction)}">Cancelar</button>
      </div>
    </form>`;
  }

  /**
   * Carga manual de ShopGoodwill (PRD §12.3, slice 10): el owner ya vio la
   * publicación — por Personal Shopper o abriéndola — y trae los datos acá.
   * Nunca un crawler. Se evalúa contra los mismos criterios que un resultado
   * automático; si no encaja, se explica por qué en vez de guardarla igual.
   */
  function manualListingForm(item) {
    if (manualEntryId !== item.id) return "";
    const id = escapeHtml(item.id);
    return `<form class="radar-form" id="radarManual-${id}">
      <p class="muted">Pegá lo que trajo la alerta de Personal Shopper o lo que viste al abrir la publicación. Queda marcada "verificación pendiente" hasta que confirmes Add to Cart en el sitio real.</p>
      <label>Título<input type="text" name="title" maxlength="300" required /></label>
      <label>Link de la publicación<input type="url" name="listingUrl" placeholder="https://shopgoodwill.com/item/…" required /></label>
      <div class="radar-form-grid">
        <label>Precio (USD)<input type="number" name="priceAmount" min="0" step="0.01" required /></label>
        <label>Envío (USD)<input type="number" name="shippingAmount" min="0" step="0.01" /></label>
      </div>
      <label>Condición<input type="text" name="conditionLabel" maxlength="200" placeholder="Used, tested…" /></label>
      <div class="card-actions radar-form-actions">
        <button class="btn-link btn-primary" type="submit"${busy ? " disabled" : ""}>Agregar</button>
        <button class="btn-link" type="button" data-cancel-manual="1">Cancelar</button>
      </div>
    </form>`;
  }

  function readForm(form) {
    const data = new FormData(form);
    const text = (field) => String(data.get(field) || "").trim();
    const number = (field) => {
      const raw = text(field);
      return raw === "" ? null : Number(raw);
    };
    const sources = data.getAll("sources").map(String);
    const slots = data.getAll("slots").map(String);
    return {
      slots,
      name: text("name"),
      platform: text("platform"),
      entityType: text("entityType"),
      entityId: text("entityId"),
      entityConsoleId: text("entityConsoleId"),
      searchType: text("searchType") || "chase",
      priority: text("priority") || "media",
      notes: text("notes"),
      sources: sources.length ? sources : ["ebay-us"],
      criteria: {
        // Una por línea: una consulta lleva espacios y comas propias, así que
        // separarlas por coma partiría "Patapon 2, the War of the Lions".
        queries: String(data.get("queries") || "").split("\n"),
        includeTerms: text("includeTerms"),
        anyTerms: text("anyTerms"),
        excludeTerms: text("excludeTerms"),
        region: text("region"),
        condition: text("condition") || "any",
        completeness: text("completeness") || "any",
        tested: text("tested") || "any",
        originalParts: text("originalParts") || "any",
        returnsRequired: data.get("returnsRequired") !== null,
        freeShippingOnly: data.get("freeShippingOnly") !== null,
        maxItemPrice: number("maxItemPrice"),
        targetItemPrice: number("targetItemPrice"),
        minLotSize: number("minLotSize")
      }
    };
  }

  function resultCard(result) {
    const meta = [result.conditionLabel, result.shippingLabel, result.locationLabel, result.sellerLabel]
      .filter(Boolean)
      .map((item) => `<span>${escapeHtml(item)}</span>`)
      .join("");
    // Por qué sirve y qué falta verificar: nunca un número opaco.
    const reasons = (result.reasons || [])
      .map((reason) => `<li>${escapeHtml(reason)}</li>`)
      .join("");
    const unverified = (result.unverified || [])
      .map((item) => `<li class="is-unverified">${escapeHtml(item)}</li>`)
      .join("");
    const total = repository.formatTotal(result);
    const benchmark = repository.describeBenchmark(result.valuation || {});
    const imported = repository.formatImported(result);
    // La celda de imagen siempre existe: sin ella la grilla de la card colapsa.
    return `<article class="chase-result">
      ${
        result.imageUrl
          ? `<img src="${escapeHtml(result.imageUrl)}" alt="" loading="lazy" />`
          : `<span class="chase-result-placeholder" aria-hidden="true"></span>`
      }
      <div>
        <p class="eyebrow">${escapeHtml(result.listingType || result.sourceLabel || repository.getSourceLabel(result.sourceId))}${
          result.confidence ? ` · ${repository.formatConfidence(result.confidence)}` : ""
        }</p>
        ${
          result.band
            ? `<p class="chase-result-band is-${escapeHtml(result.band)}">${escapeHtml(
                repository.getBandLabel(result.band)
              )}${Number.isFinite(Number(result.score)) ? ` · ${Number(result.score)}/100` : ""}${
                benchmark ? ` <span>${escapeHtml(benchmark)}</span>` : ""
              }</p>`
            : ""
        }
        <h3>${escapeHtml(result.title)}</h3>
        ${
          result.requiresVerification
            ? `<p class="radar-verification-pending">Verificación pendiente: confirmá Add to Cart en el sitio antes de decidir.</p>`
            : ""
        }
        <div class="chase-result-meta">${meta || "<span>Detalles a confirmar</span>"}</div>
        ${reasons || unverified ? `<ul class="chase-result-why">${reasons}${unverified}</ul>` : ""}
      </div>
      <div class="chase-result-price">
        <strong>${escapeHtml(result.priceLabel || "Ver precio")}</strong>
        ${total ? `<span class="chase-result-total">${escapeHtml(total)}</span>` : ""}
        ${imported ? `<span class="chase-result-total is-imported">${escapeHtml(imported)}</span>` : ""}
        <a class="btn-link" href="${escapeHtml(result.listingUrl)}" target="_blank" rel="noreferrer noopener">Ver publicación</a>
        ${
          result.requiresVerification
            ? `<button class="btn-link" type="button" data-verify="${escapeHtml(result.id)}">Marcar verificada</button>`
            : ""
        }
        <button class="btn-link" type="button" data-lot-calc="${escapeHtml(result.id)}">Valorar como lote</button>
        <button class="btn-link chase-delete" type="button" data-dismiss="${escapeHtml(result.id)}">Descartar</button>
      </div>
    </article>${dismissForm(result)}${lotCalculator(result)}`;
  }

  /**
   * Descartar es para lo que no era: una caja suelta, una región equivocada,
   * algo que ya tenés. La decisión se guarda contra la publicación, no contra
   * el match, así que no vuelve a aparecer aunque la búsqueda se ejecute de
   * nuevo. El motivo no es burocracia: es lo que después dice si conviene
   * ajustar los términos excluidos de esa búsqueda.
   */
  function dismissForm(result) {
    if (dismissId !== result.id) return "";
    const id = escapeHtml(result.id);
    return `<form class="radar-dismiss-form" id="radarDismiss-${id}">
      <label for="dismissReason-${id}">Motivo</label>
      <select id="dismissReason-${id}" name="reason">
        ${repository.DISMISS_REASONS.map(
          (reason) => `<option value="${escapeHtml(reason.id)}">${escapeHtml(reason.label)}</option>`
        ).join("")}
      </select>
      <input name="note" maxlength="400" placeholder="Nota opcional" />
      <button class="btn-link btn-primary" type="submit"${busy ? " disabled" : ""}>Descartar</button>
      <button class="btn-link" type="button" data-cancel-dismiss="1">Cancelar</button>
    </form>`;
  }

  /**
   * Valuación de lotes (PRD §10.5): valor conservador, valor útil para vos,
   * costo por pieza útil, descuento. Las piezas las escribís vos — el radar
   * no lee fotos ni parsea "PS2 + 8 juegos" de un título (eso es
   * reconocimiento asistido, PRD P2, sin construir). Nada de esto se guarda:
   * es una calculadora de bolsillo para decidir, no un registro.
   */
  function lotPieceRow(index) {
    return `<fieldset class="radar-lot-piece">
      <legend>Pieza ${index + 1}</legend>
      <label>Nombre<input type="text" name="pieceName${index}" maxlength="200" /></label>
      <label>Valor comparable (USD)<input type="number" name="pieceValue${index}" min="0" step="0.01" /></label>
      <label>Factor de condición (0–1)<input type="number" name="pieceCondition${index}" min="0" max="1" step="0.05" value="1" /></label>
      <div class="radar-form-checks">
        <label><input type="checkbox" name="pieceWanted${index}" checked /> La quiero</label>
        <label><input type="checkbox" name="pieceOwned${index}" /> Ya la tengo</label>
        <label><input type="checkbox" name="pieceDuplicate${index}" /> Duplicada en el lote</label>
      </div>
    </fieldset>`;
  }

  function lotResultSummary(result) {
    if (!result) return "";
    const line = (label, value) =>
      value == null ? "" : `<div><strong>${escapeHtml(repository.formatAmount(value, "USD"))}</strong><span>${escapeHtml(label)}</span></div>`;
    const discountLine =
      result.discount == null
        ? ""
        : `<div><strong>${Math.round(result.discount * 100)}%</strong><span>descuento sobre el valor conservador</span></div>`;
    const caveats = (result.caveats || []).map((c) => `<li>${escapeHtml(c)}</li>`).join("");
    return `<div class="radar-lot-result">
      <div class="radar-lot-result-grid">
        ${line("valor conservador", result.conservativeValue)}
        ${line("valor útil para vos", result.usefulValue)}
        ${line("costo por pieza útil", result.costPerUsefulPiece)}
        ${discountLine}
      </div>
      ${caveats ? `<ul class="radar-lot-caveats">${caveats}</ul>` : ""}
    </div>`;
  }

  function lotCalculator(result) {
    if (lotCalcId !== result.id) return "";
    const id = escapeHtml(result.id);
    const pieces = Array.from({ length: lotCalcPieceCount }, (_, index) => lotPieceRow(index)).join("");
    return `<div class="detail-block radar-lot-calc">
      <p class="muted">Cuánto vale de verdad este lote, pieza por pieza. Nada se guarda: es para decidir ahora, no un registro.</p>
      <form id="radarLot-${id}" data-lot-form="${id}">
        <label>Costo total del lote (USD)
          <input type="number" name="totalCost" min="0" step="0.01" value="${result.totalAmount != null ? result.totalAmount : ""}" />
        </label>
        ${pieces}
        <div class="card-actions radar-form-actions">
          <button class="btn-link" type="button" data-lot-add-piece="1">+ Agregar pieza</button>
          <button class="btn-link btn-primary" type="submit"${busy ? " disabled" : ""}>Calcular</button>
          <button class="btn-link" type="button" data-lot-cancel="1">Cerrar</button>
        </div>
      </form>
      ${lotResultSummary(lotCalcResult)}
    </div>`;
  }

  function searchActions(item) {
    const id = escapeHtml(item.id);
    const actions = [];
    if (item.status === "active") {
      actions.push(
        `<button class="btn-link btn-primary" type="button" data-run="${id}"${
          repository.canRun(item) ? "" : " disabled"
        }>Buscar ahora</button>`
      );
      actions.push(`<button class="btn-link" type="button" data-status="${id}" data-next="paused">Pausar</button>`);
    }
    if (item.status === "draft") {
      actions.push(
        `<button class="btn-link btn-primary" type="button" data-status="${id}" data-next="active">Activar</button>`
      );
    }
    if (item.status === "paused") {
      actions.push(
        `<button class="btn-link btn-primary" type="button" data-status="${id}" data-next="active">Reanudar</button>`
      );
    }
    if (item.status === "archived") {
      actions.push(`<button class="btn-link" type="button" data-status="${id}" data-next="active">Reactivar</button>`);
    }
    // Previsualizar corre la búsqueda y no guarda nada. Es la única forma de ver
    // qué traería una búsqueda que todavía no está activa sin activarla.
    actions.push(
      `<button class="btn-link" type="button" data-preview="${id}"${
        repository.canPreview(item) ? "" : " disabled"
      }>Previsualizar</button>`
    );
    actions.push(`<button class="btn-link" type="button" data-edit="${id}">Editar</button>`);
    actions.push(`<button class="btn-link" type="button" data-duplicate="${id}">Duplicar</button>`);
    if (item.status !== "archived") {
      // ShopGoodwill llega por Personal Shopper/alerta guardada, nunca por scan
      // propio — el owner ya vio la publicación y trae los datos a mano (PRD §12.3).
      actions.push(`<button class="btn-link" type="button" data-manual-entry="${id}">Agregar de ShopGoodwill</button>`);
    }
    if (item.status !== "archived") {
      actions.push(`<button class="btn-link" type="button" data-status="${id}" data-next="archived">Archivar</button>`);
    }
    actions.push(`<button class="btn-link chase-delete" type="button" data-delete="${id}">Eliminar</button>`);
    return `<div class="card-actions chase-actions">${actions.join("")}</div>`;
  }

  /** Qué pasó en la corrida, en una línea: encontradas, descartadas y cobertura. */
  function runSummary(result = {}) {
    const found = Number(result.results) || 0;
    const rejected = Number(result.rejected) || 0;
    const parts = [found === 1 ? "1 publicación que encaja" : `${found} publicaciones que encajan`];
    if (rejected) parts.push(rejected === 1 ? "1 descartada por criterio" : `${rejected} descartadas por criterio`);
    if (result.authoritative === false) parts.push("cobertura incompleta: se conservó lo anterior");
    return `${parts.join(" · ")}.`;
  }

  /** Franja del scheduler: qué corrió hoy, qué viene y una corrida manual. */
  /**
   * Qué parte de la colección el radar no está mirando.
   *
   * El Master propone de a puñados —los cupos existen para no generar
   * cuatrocientas búsquedas—, así que sin este panel el hueco es invisible: se
   * ven las búsquedas que hay, nunca las que faltan.
   */
  function coveragePanel() {
    const coverage = repository.getCoverage();
    if (!coverage) return "";
    const sinBusqueda = coverage.ownedWithoutSearch || [];
    const conHuecos = (coverage.consoles || [])
      .filter((c) => c.explicitUncovered > 0)
      .sort((a, b) => b.explicitUncovered - a.explicitUncovered);
    if (!sinBusqueda.length && !conHuecos.length) return "";

    const nombre = (id) => (coverage.consoles || []).find((c) => c.id === id)?.name || id;
    // El resumen —cuántos huecos hay— es lo único que se lee de un vistazo y
    // queda siempre visible. El detalle, que son ocho consolas con sus
    // ejemplos, ocupaba media pantalla arriba de las búsquedas y empujaba
    // fuera de vista lo que uno viene a mirar.
    return `<section class="detail-block radar-coverage${coverageOpen ? "" : " is-collapsed"}">
      <button class="radar-coverage-head" type="button" data-toggle-coverage="1" aria-expanded="${coverageOpen}">
        <span>
          <span class="eyebrow">Cobertura</span>
          <span class="radar-coverage-title">Qué no está mirando el radar</span>
          <span class="muted">${coverage.explicitUncovered} de los ${coverage.explicitWanted} juegos que marcaste «lo quiero» no tienen ninguna búsqueda que los persiga${
            coverage.uncoveredGames > coverage.explicitUncovered
              ? `, más ${coverage.uncoveredGames - coverage.explicitUncovered} recomendaciones conservadas`
              : ""
          }.</span>
        </span>
        <span class="radar-coverage-caret" aria-hidden="true">${coverageOpen ? "▾" : "▸"}</span>
      </button>
      ${!coverageOpen ? "" : `${
        sinBusqueda.length
          ? `<p class="radar-coverage-consoles">Consolas tuyas sin ninguna búsqueda: ${sinBusqueda
              .map((id) => escapeHtml(nombre(id)))
              .join(" · ")}</p>`
          : ""
      }
      ${
        conHuecos.length
          ? `<ul class="radar-coverage-list">${conHuecos
              .slice(0, 8)
              .map(
                (c) => `<li>
                  <strong>${escapeHtml(c.name)}</strong>
                  <span>${c.explicitUncovered} sin cubrir</span>
                  ${c.examples.length ? `<em>${c.examples.map((n) => escapeHtml(n)).join(", ")}…</em>` : ""}
                </li>`
              )
              .join("")}</ul>`
          : ""
      }
      <p class="muted">El Master propone de a pocas por vez, a propósito. Pedile que proponga otra tanda, o creá la búsqueda a mano.</p>`}
    </section>`;
  }

  function schedulePanel() {
    const slots = repository.getSlots();
    if (!slots.length) return "";
    const current = repository.getCurrentRun();
    const next = repository.getNextSlot();
    const lastRun = repository.getRuns()[0];
    const chips = slots
      .map(
        (slot) =>
          `<span class="radar-slot is-${escapeHtml(slot.state)}" title="${escapeHtml(slot.detail || "")}">
            ${escapeHtml(slot.label)} <em>${escapeHtml(repository.getSlotStateLabel(slot.state))}</em>
          </span>`
      )
      .join("");
    const lastLine = lastRun
      ? `Última corrida ${escapeHtml(repository.getRunStatusLabel(lastRun.status))}: ${lastRun.searchesOk}/${
          lastRun.searchesTotal
        } búsquedas · ${lastRun.listingsMatched} encajan · ${lastRun.listingsRejected} descartadas`
      : "Todavía no corrió ninguna búsqueda programada.";
    return `<section class="detail-block radar-schedule">
      <div>
        <p class="eyebrow">Horarios</p>
        <h2>Tres revisiones por día</h2>
        <div class="radar-slots">${chips}</div>
        <p class="muted">${
          next ? `Próxima: ${escapeHtml(repository.formatSlotTime(next.at))} (${escapeHtml(next.label)}).` : ""
        } ${escapeHtml(lastLine)}</p>
      </div>
      <div class="card-actions">
        <button class="btn-link btn-primary" type="button" data-run-all="1"${current || busy ? " disabled" : ""}>
          ${current ? "Buscando…" : "Buscar en todas"}
        </button>
      </div>
    </section>`;
  }

  /** El vacío explica el estado real; nunca ofrece una acción que la card no tiene. */
  function emptyResultsMessage(item) {
    if (item.status === "draft") return "Es una propuesta: no busca nada hasta que la actives.";
    if (item.status === "paused") return "Pausada: conserva sus criterios y su historial, pero no vuelve a buscar.";
    if (item.status === "archived") return "Archivada: queda como historial consultable.";
    if (!repository.canRun(item)) return "Ninguna de sus fuentes puede ejecutarse todavía.";
    return "Todavía no hay resultados guardados. Usá “Buscar ahora” o esperá la próxima revisión.";
  }

  /**
   * Una búsqueda sin entidad del catálogo funciona, pero calla tres cosas que
   * el owner no tiene cómo adivinar: no puede ofrecer "Registrar compra"
   * porque no sabe qué escribir, no tiene referencia de precio curada, y una
   * consola se costea con el peso genérico en vez del suyo.
   *
   * Los lotes y las búsquedas de descubrimiento no lo declaran: existen para
   * traer cosas mezcladas, así que para ellas no estar vinculadas es lo normal.
   */
  function unlinkedNote(item) {
    if (["lot", "discovery"].includes(item.searchType)) return "";
    if (item.entityId && (item.entityType !== "game" || item.entityConsoleId)) return "";
    const falta =
      item.entityType === "game" && item.entityId && !item.entityConsoleId
        ? "no dice de qué consola es ese juego"
        : "no apunta a nada del catálogo";
    return `<p class="radar-unlinked-note">Esta búsqueda ${escapeHtml(falta)}: no va a ofrecer «Registrar compra» ni comparar contra un precio de referencia. Se arregla al editarla.</p>`;
  }

  /**
   * Una propuesta arranca plegada y una búsqueda real abierta. El Master dejó
   * setenta borradores de una sentada: mostrarlos todos desplegados convierte
   * la página en un muro y esconde justo lo que sí está corriendo.
   *
   * Plegar esconde lo pesado —criterios, resultados, avisos— pero nunca las
   * acciones: con setenta propuestas para revisar, activar una tiene que seguir
   * siendo un click y no dos.
   */
  function isExpanded(item) {
    return expanded.has(item.id) ? true : !expanded.has(`!${item.id}`) && item.status !== "draft";
  }

  /**
   * Lo que traería la búsqueda si corriera ahora. No se guardó nada: no hay
   * "Descartar", "Valorar como lote" ni "Registrar compra", porque ninguna de
   * esas acciones tiene sobre qué operar. Sólo el link a la publicación real.
   */
  function previewResultCard(result) {
    const meta = [result.conditionLabel, result.shippingLabel, result.locationLabel, result.sellerLabel]
      .filter(Boolean)
      .map((item) => `<span>${escapeHtml(item)}</span>`)
      .join("");
    const reasons = (result.reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("");
    const unverified = (result.unverified || [])
      .map((item) => `<li class="is-unverified">${escapeHtml(item)}</li>`)
      .join("");
    const total = repository.formatTotal(result);
    return `<article class="chase-result">
      ${
        result.imageUrl
          ? `<img src="${escapeHtml(result.imageUrl)}" alt="" loading="lazy" />`
          : `<span class="chase-result-placeholder" aria-hidden="true"></span>`
      }
      <div>
        <p class="eyebrow">${escapeHtml(result.listingType || repository.getSourceLabel(result.sourceId))}${
          result.confidence ? ` · ${repository.formatConfidence(result.confidence)}` : ""
        }</p>
        ${
          result.band
            ? `<p class="chase-result-band is-${escapeHtml(result.band)}">${escapeHtml(
                repository.getBandLabel(result.band)
              )}${Number.isFinite(Number(result.score)) ? ` · ${Number(result.score)}/100` : ""}</p>`
            : ""
        }
        <h3>${escapeHtml(result.title)}</h3>
        <div class="chase-result-meta">${meta || "<span>Detalles a confirmar</span>"}</div>
        ${reasons || unverified ? `<ul class="chase-result-why">${reasons}${unverified}</ul>` : ""}
      </div>
      <div class="chase-result-price">
        <strong>${escapeHtml(result.priceLabel || "Ver precio")}</strong>
        ${total ? `<span class="chase-result-total">${escapeHtml(total)}</span>` : ""}
        <a class="btn-link" href="${escapeHtml(result.listingUrl)}" target="_blank" rel="noreferrer noopener">Ver publicación</a>
      </div>
    </article>`;
  }

  function previewPanel(item) {
    if (previewId !== item.id || !previewData) return "";
    const results = previewData.results || [];
    const failed = (previewData.receipts || []).filter((receipt) => receipt.status === "failed").length;
    const queries = (previewData.queries || []).length;
    return `<div class="detail-block radar-preview">
      <p class="muted">Previsualización: ${
        queries === 1 ? "se consultó 1 consulta" : `se consultaron ${escapeHtml(String(queries))} consultas`
      } y no se guardó nada. La búsqueda sigue ${escapeHtml(
        repository.getStatusLabel(previewData.status || item.status)
      ).toLowerCase()} y su inventario quedó intacto.</p>
      <p class="muted">${escapeHtml(String(previewData.matched ?? 0))} coincidencias · ${escapeHtml(
        String(previewData.rejected ?? 0)
      )} descartadas por los criterios${
        failed ? ` · ${escapeHtml(String(failed))} ${failed === 1 ? "consulta falló" : "consultas fallaron"}` : ""
      }.</p>
      <div class="chase-results">${
        results.length
          ? results.map(previewResultCard).join("")
          : `<p class="chase-empty-results">Ninguna publicación pasó los criterios en esta previa.</p>`
      }</div>
      <div class="card-actions radar-form-actions">
        <button class="btn-link" type="button" data-preview-close="1">Cerrar la previa</button>
      </div>
    </div>`;
  }

  function searchCard(item) {
    const results = item.results || [];
    const open = isExpanded(item) || editingId === item.id;
    const chips = repository.describeCriteria(item.criteria || {});
    const blocked = repository.getBlockedSources(item);
    const sourceLabels = (item.sources || []).map((sourceId) => repository.getSourceLabel(sourceId)).join(" · ");
    const isEditing = editingId === item.id;
    return `<article class="detail-block chase-card radar-card is-${escapeHtml(item.status)}${open ? "" : " is-collapsed"}">
      <div class="chase-card-head">
        <button class="radar-card-toggle" type="button" data-toggle-card="${escapeHtml(item.id)}"
                aria-expanded="${open}" aria-label="${open ? "Plegar" : "Desplegar"} ${escapeHtml(item.name)}">${open ? "▾" : "▸"}</button>
        <div>
          <p class="eyebrow">${escapeHtml(repository.getTypeLabel(item.searchType))} · ${escapeHtml(
            item.platform || "Sin plataforma"
          )} · ${escapeHtml(sourceLabels || "Sin fuentes")}</p>
          <h2>${escapeHtml(item.name)}</h2>
          <p class="muted">Consulta: ${escapeHtml(item.searchQuery)}</p>
        </div>
        <div class="radar-card-badges">
          <span class="chase-status is-${escapeHtml(item.status)}">${escapeHtml(
            repository.getStatusLabel(item.status)
          )}</span>
          <span class="radar-priority">${escapeHtml(repository.getPriorityLabel(item.priority))}</span>
        </div>
      </div>
      ${
        open && item.status === "draft"
          ? `<p class="radar-draft-note">Propuesta guardada${
              item.origin === "master" ? " por el Master de colección" : ""
            }. No va a ejecutarse hasta que la actives.</p>`
          : ""
      }
      ${open ? unlinkedNote(item) : ""}
      ${open && chips.length ? `<div class="radar-chips">${chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("")}</div>` : ""}
      <div class="chase-card-meta">
        <span>Última búsqueda: ${escapeHtml(dateLabel(item.lastCheckedAt))}</span>
        <span>${results.length} resultados activos</span>
        ${(item.slotLabels || []).length ? `<span>Corre ${escapeHtml(item.slotLabels.join(" · "))}</span>` : ""}
        ${item.notes ? `<span>${escapeHtml(item.notes)}</span>` : ""}
        ${blocked.length ? `<span class="radar-blocked">Sin ejecutar: ${escapeHtml(blocked.map((sourceId) => repository.getSourceLabel(sourceId)).join(", "))}</span>` : ""}
        ${item.lastError ? `<span class="chase-error">${escapeHtml(item.lastError)}</span>` : ""}
      </div>
      ${searchActions(item)}
      ${previewPanel(item)}
      ${isEditing ? searchForm(item, { formId: `radarEdit-${item.id}`, submitLabel: "Guardar cambios", cancelAction: "edit" }) : ""}
      ${manualListingForm(item)}
      ${
        open
          ? `${
              results.length > 1
                ? `<div class="chase-results-sort">
                     <label for="resultSort-${escapeHtml(item.id)}">Ordenar</label>
                     <select id="resultSort-${escapeHtml(item.id)}" data-result-sort="1">${optionList(
                       RESULT_SORT_OPTIONS.map((option) => option.id),
                       resultSortBy,
                       (value) => RESULT_SORT_OPTIONS.find((option) => option.id === value).label
                     )}</select>
                   </div>`
                : ""
            }<div class="chase-results">${
              results.length
                ? sortResults(results, resultSortBy).map(resultCard).join("")
                : `<p class="chase-empty-results">${escapeHtml(emptyResultsMessage(item))}</p>`
            }</div>`
          : ""
      }
    </article>`;
  }

  /**
   * El orden elegido se aplica sobre lo ya filtrado. "Como las trae el radar"
   * no reordena nada: es el orden del servidor, y es el default porque es el
   * único que no impone un criterio nuestro.
   */
  function sortSearches(items) {
    const copia = [...items];
    if (sortBy === "name") {
      return copia.sort((a, b) => a.name.localeCompare(b.name, "es"));
    }
    if (sortBy === "priority") {
      const rank = (item) => {
        const index = PRIORITY_OPTIONS.indexOf(item.priority);
        return index === -1 ? PRIORITY_OPTIONS.length : index;
      };
      return copia.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, "es"));
    }
    if (sortBy === "results") {
      return copia.sort((a, b) => (b.results || []).length - (a.results || []).length);
    }
    if (sortBy === "checked") {
      // Sin buscar nunca es lo más atrasado que hay: va primero.
      const cuando = (item) => (item.lastCheckedAt ? Date.parse(item.lastCheckedAt) : 0);
      return copia.sort((a, b) => cuando(a) - cuando(b));
    }
    return copia;
  }

  function sortBar() {
    return `<label class="radar-sort">Orden
      <select data-sort="1">${optionList(
        SORT_OPTIONS.map((option) => option.id),
        sortBy,
        (value) => SORT_OPTIONS.find((option) => option.id === value).label
      )}</select>
    </label>`;
  }

  function filterTabs(counts) {
    return `<nav class="radar-filters" aria-label="Filtrar búsquedas">${STATUS_FILTERS.map((filter) => {
      const total = filter.id === "all" ? repository.getSearches().length : counts[filter.id] || 0;
      return `<button class="radar-filter${filter.id === statusFilter ? " is-active" : ""}" type="button"
        data-filter="${escapeHtml(filter.id)}" aria-pressed="${filter.id === statusFilter}">
        ${escapeHtml(filter.label)}<span>${total}</span>
      </button>`;
    }).join("")}</nav>`;
  }

  function render() {
    const counts = repository.getCounts();
    const items = repository.getSearchesByStatus(statusFilter);
    const sandboxNotice = repository.isSandbox()
      ? `<p class="chasing-sandbox-notice">Modo Sandbox: la conexión se prueba contra eBay, pero las publicaciones no son compras reales. Las búsquedas y sus criterios se guardan igual.</p>`
      : "";
    const emptyMessage =
      statusFilter === "all"
        ? "Agregá una búsqueda para que el radar empiece a trabajar."
        : "No hay búsquedas en este estado.";

    root.innerHTML = `<div class="back-link"><a href="./index.html">← Volver a la colección</a> · <a href="./radar.html">Para mí</a></div>
      <header class="detail-hero chasing-hero">
        <div>
          <p class="eyebrow">Collection Radar</p>
          <h1>Búsquedas del radar</h1>
          <p>Perfiles persistentes con criterios propios. Una búsqueda activa corre sola; una propuesta espera tu aprobación. Fuente actual: ${escapeHtml(
            repository.getEnvironmentLabel()
          )}.</p>
          ${sandboxNotice}
        </div>
        <div class="chasing-count">
          <strong>${counts.active}</strong>
          <span>búsquedas activas</span>
        </div>
      </header>
      <section class="detail-block chasing-add${creating ? " is-open" : ""}">
        <div>
          <p class="eyebrow">Nueva búsqueda</p>
          <h2>Agregar un objetivo al radar</h2>
          <p class="muted">Empezá por el nombre y la plataforma. Los criterios finos son opcionales y se pueden editar después.</p>
        </div>
        ${
          creating
            ? searchForm(
                { criteria: {}, sources: ["ebay-us"], name: prefillName, platform: prefillPlatform },
                { formId: "radarCreate", submitLabel: "Guardar y activar", cancelAction: "create" }
              )
            : `<div class="card-actions">
                <button class="btn-link btn-primary" type="button" data-open-create="1">Nueva búsqueda</button>
                <button class="btn-link" type="button" data-master="1"${busy ? " disabled" : ""}>Que el Master proponga</button>
              </div>`
        }
      </section>
      ${feedback ? `<p class="chasing-feedback is-${escapeHtml(feedbackTone)}" role="status">${escapeHtml(feedback)}</p>` : ""}
      ${coveragePanel()}
      ${schedulePanel()}
      <div class="radar-list-controls">${filterTabs(counts)}${sortBar()}</div>
      <section class="chasing-list">${
        items.length
          ? sortSearches(items).map(searchCard).join("")
          : `<article class="detail-block chasing-empty"><h2>Sin búsquedas</h2><p class="muted">${escapeHtml(
              emptyMessage
            )}</p></article>`
      }</section>`;
    bindEvents();
  }

  function renderUnavailable() {
    root.innerHTML = `<section class="detail-block chasing-empty">
      <p class="eyebrow">Collection Radar</p>
      <h1>No está disponible en este origen</h1>
      <p class="muted">Esta sección necesita el backend de Consolas para guardar las búsquedas y consultar sus fuentes.</p>
      <a class="btn-link" href="./index.html">Volver a la colección</a>
    </section>`;
  }

  function each(selector, handler) {
    (root.querySelectorAll?.(selector) || []).forEach(handler);
  }

  function bindEvents() {
    each("[data-toggle-card]", (button) =>
      button.addEventListener("click", () => {
        const id = button.dataset.toggleCard;
        const item = repository.getSearch(id);
        // Se guarda la decisión, no el estado: "!id" recuerda que la plegaste a
        // mano aunque su default fuera abierta.
        const abierta = item ? isExpanded(item) : false;
        expanded.delete(id);
        expanded.delete(`!${id}`);
        expanded.add(abierta ? `!${id}` : id);
        savePrefs();
        render();
      })
    );

    each("[data-result-sort]", (select) =>
      select.addEventListener("change", (event) => {
        resultSortBy = event.target.value;
        savePrefs();
        render();
      })
    );

    each("[data-toggle-coverage]", (button) =>
      button.addEventListener("click", () => {
        coverageOpen = !coverageOpen;
        savePrefs();
        render();
      })
    );

    each("[data-sort]", (select) =>
      select.addEventListener("change", (event) => {
        sortBy = event.target.value;
        savePrefs();
        render();
      })
    );

    each("[data-filter]", (button) =>
      button.addEventListener("click", () => {
        statusFilter = button.dataset.filter;
        render();
      })
    );

    each("[data-open-create]", (button) =>
      button.addEventListener("click", () => {
        creating = true;
        editingId = "";
        render();
      })
    );

    each("[data-cancel]", (button) =>
      button.addEventListener("click", () => {
        if (button.dataset.cancel === "create") creating = false;
        else editingId = "";
        render();
      })
    );

    each("[data-edit]", (button) =>
      button.addEventListener("click", () => {
        editingId = editingId === button.dataset.edit ? "" : button.dataset.edit;
        creating = false;
        render();
      })
    );

    document.getElementById("radarCreate")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = readForm(event.currentTarget);
      await perform(
        "Guardando la búsqueda…",
        () => repository.createSearch(payload),
        "Búsqueda creada y activa.",
        () => {
          creating = false;
        }
      );
    });

    if (editingId) {
      const targetId = editingId;
      document.getElementById(`radarEdit-${targetId}`)?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const payload = readForm(event.currentTarget);
        await perform(
          "Guardando los cambios…",
          () => repository.updateSearch(targetId, payload),
          "Búsqueda actualizada.",
          () => {
            editingId = "";
          }
        );
      });
    }

    each("[data-manual-entry]", (button) =>
      button.addEventListener("click", () => {
        manualEntryId = manualEntryId === button.dataset.manualEntry ? "" : button.dataset.manualEntry;
        editingId = "";
        render();
      })
    );

    each("[data-cancel-manual]", (button) =>
      button.addEventListener("click", () => {
        manualEntryId = "";
        render();
      })
    );

    if (manualEntryId) {
      const targetId = manualEntryId;
      document.getElementById(`radarManual-${targetId}`)?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const payload = {
          searchId: targetId,
          title: String(data.get("title") || "").trim(),
          listingUrl: String(data.get("listingUrl") || "").trim(),
          priceAmount: Number(data.get("priceAmount")),
          shippingAmount: data.get("shippingAmount") ? Number(data.get("shippingAmount")) : null,
          conditionLabel: String(data.get("conditionLabel") || "").trim(),
        };
        await perform(
          "Agregando…",
          () => repository.createManualListing(payload),
          "Agregada. Queda como verificación pendiente hasta que confirmes la publicación real.",
          () => {
            manualEntryId = "";
          }
        );
      });
    }

    each("[data-verify]", (button) =>
      button.addEventListener("click", () =>
        perform(
          "Marcando verificada…",
          () => repository.verifyListing(button.dataset.verify),
          "Verificada. Ya cuenta como una oportunidad confirmada."
        )
      )
    );

    each("[data-dismiss]", (button) =>
      button.addEventListener("click", () => {
        dismissId = dismissId === button.dataset.dismiss ? "" : button.dataset.dismiss;
        lotCalcId = "";
        render();
      })
    );

    each("[data-cancel-dismiss]", (button) =>
      button.addEventListener("click", () => {
        dismissId = "";
        render();
      })
    );

    if (dismissId) {
      const targetId = dismissId;
      document.getElementById(`radarDismiss-${targetId}`)?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        await perform(
          "Descartando…",
          () =>
            repository.decide(targetId, {
              decision: "dismissed",
              reason: String(data.get("reason") || ""),
              note: String(data.get("note") || "")
            }),
          "Descartada. No vuelve a aparecer en esta búsqueda.",
          () => {
            dismissId = "";
          }
        );
      });
    }

    each("[data-lot-calc]", (button) =>
      button.addEventListener("click", () => {
        const opening = lotCalcId !== button.dataset.lotCalc;
        lotCalcId = opening ? button.dataset.lotCalc : "";
        lotCalcPieceCount = 3;
        lotCalcResult = null;
        dismissId = "";
        render();
      })
    );

    each("[data-lot-cancel]", (button) =>
      button.addEventListener("click", () => {
        lotCalcId = "";
        lotCalcResult = null;
        render();
      })
    );

    each("[data-lot-add-piece]", (button) =>
      button.addEventListener("click", () => {
        lotCalcPieceCount += 1;
        render();
      })
    );

    if (lotCalcId) {
      document.getElementById(`radarLot-${lotCalcId}`)?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const num = (field) => (data.get(field) === "" || data.get(field) == null ? null : Number(data.get(field)));
        const pieces = [];
        for (let index = 0; index < lotCalcPieceCount; index += 1) {
          const name = String(data.get(`pieceName${index}`) || "").trim();
          if (!name) continue;
          pieces.push({
            name,
            comparableValue: num(`pieceValue${index}`),
            conditionFactor: num(`pieceCondition${index}`) ?? 1,
            wanted: data.get(`pieceWanted${index}`) != null,
            alreadyOwned: data.get(`pieceOwned${index}`) != null,
            isDuplicate: data.get(`pieceDuplicate${index}`) != null,
          });
        }
        try {
          busy = true;
          render();
          lotCalcResult = await repository.computeLotValuation({ totalCost: num("totalCost"), pieces });
          busy = false;
          render();
        } catch (error) {
          busy = false;
          setFeedback(error.message, "error");
          render();
        }
      });
    }

    each("[data-master]", (button) =>
      button.addEventListener("click", async () => {
        let summary = "";
        await perform(
          "Leyendo tu colección…",
          async () => {
            const result = await repository.regenerateMaster();
            summary =
              result.created > 0
                ? `${result.created} propuestas nuevas, en borrador. Revisalas y activá las que quieras.`
                : "Sin propuestas nuevas: las que el Master ve ya existen.";
          },
          "",
          () => {
            statusFilter = "draft";
            setFeedback(summary, "success");
          }
        );
      })
    );

    each("[data-run-all]", (button) =>
      button.addEventListener("click", () =>
        perform(
          "Buscando en todas las búsquedas activas…",
          () => repository.startRun(),
          "Corrida en curso: los resultados aparecen a medida que termina."
        )
      )
    );

    each("[data-run]", (button) =>
      button.addEventListener("click", async () => {
        // La corrida informa qué encontró y qué descartó por criterio.
        let summary = "Resultados actualizados.";
        await perform(
          "Buscando…",
          async () => {
            const result = await repository.runSearch(button.dataset.run);
            summary = runSummary(result);
          },
          "",
          () => setFeedback(summary, "success")
        );
      })
    );

    each("[data-preview]", (button) =>
      button.addEventListener("click", async () => {
        // Previsualizar no escribe nada, así que no recarga el inventario: el
        // resultado vive sólo en esta vista hasta que se cierre.
        if (busy) return;
        busy = true;
        previewId = button.dataset.preview;
        previewData = null;
        setFeedback("Previsualizando sin guardar nada…");
        render();
        try {
          previewData = await repository.previewSearch(previewId);
          busy = false;
          setFeedback(
            `Previa lista: ${previewData.results?.length || 0} de ${previewData.matched || 0} coincidencias. No se guardó nada.`,
            "success"
          );
        } catch (error) {
          busy = false;
          previewId = "";
          setFeedback(error.message, "error");
        }
        render();
      })
    );

    each("[data-preview-close]", (button) =>
      button.addEventListener("click", () => {
        previewId = "";
        previewData = null;
        render();
      })
    );

    each("[data-status]", (button) =>
      button.addEventListener("click", () =>
        perform(
          "Actualizando el estado…",
          () => repository.setStatus(button.dataset.status, button.dataset.next),
          `Búsqueda ${repository.getStatusLabel(button.dataset.next).toLowerCase()}.`
        )
      )
    );

    each("[data-duplicate]", (button) =>
      button.addEventListener("click", () =>
        perform(
          "Duplicando…",
          () => repository.duplicateSearch(button.dataset.duplicate),
          "Copia creada como propuesta: revisala y activala cuando quieras."
        )
      )
    );

    each("[data-delete]", (button) =>
      button.addEventListener("click", () => {
        const search = repository.getSearch(button.dataset.delete);
        const name = search?.name || "esta búsqueda";
        if (!confirm(`¿Eliminar “${name}”? Sale del radar; tu colección y tu wishlist no se tocan.`)) return;
        return perform(
          "Eliminando…",
          () => repository.deleteSearch(button.dataset.delete),
          "Búsqueda eliminada. La colección quedó intacta."
        );
      })
    );
  }

  function applyPrefillFromUrl() {
    const params = new URLSearchParams(window.location?.search || "");
    prefillName = params.get("prefillName") || "";
    prefillPlatform = params.get("prefillPlatform") || "";
    if (params.get("open") === "create") creating = true;
  }

  async function start() {
    applyPrefillFromUrl();
    try {
      await reload();
    } catch (error) {
      renderUnavailable();
    }
  }

  start();
})();
