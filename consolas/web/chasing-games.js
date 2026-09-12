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
  const PRIORITY_OPTIONS = ["alta", "media-alta", "media", "baja"];

  let statusFilter = "all";
  let editingId = "";
  let prefillName = "";
  let prefillPlatform = "";
  let manualEntryId = "";
  let lotCalcId = "";
  let lotCalcPieceCount = 3;
  let lotCalcResult = null;
  let creating = false;
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
    await Promise.all([repository.load(), repository.loadRuns().catch(() => null)]);
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
          <label>Mínimo de piezas del lote
            <input name="minLotSize" type="number" min="1" step="1"
                   value="${escapeHtml(criteria.minLotSize ?? "")}" placeholder="6" />
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
      searchType: text("searchType") || "chase",
      priority: text("priority") || "media",
      notes: text("notes"),
      sources: sources.length ? sources : ["ebay-us"],
      criteria: {
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
      </div>
    </article>${lotCalculator(result)}`;
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

  function searchCard(item) {
    const results = item.results || [];
    const chips = repository.describeCriteria(item.criteria || {});
    const blocked = repository.getBlockedSources(item);
    const sourceLabels = (item.sources || []).map((sourceId) => repository.getSourceLabel(sourceId)).join(" · ");
    const isEditing = editingId === item.id;
    return `<article class="detail-block chase-card radar-card is-${escapeHtml(item.status)}">
      <div class="chase-card-head">
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
        item.status === "draft"
          ? `<p class="radar-draft-note">Propuesta guardada${
              item.origin === "master" ? " por el Master de colección" : ""
            }. No va a ejecutarse hasta que la actives.</p>`
          : ""
      }
      ${chips.length ? `<div class="radar-chips">${chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("")}</div>` : ""}
      <div class="chase-card-meta">
        <span>Última búsqueda: ${escapeHtml(dateLabel(item.lastCheckedAt))}</span>
        <span>${results.length} resultados activos</span>
        ${(item.slotLabels || []).length ? `<span>Corre ${escapeHtml(item.slotLabels.join(" · "))}</span>` : ""}
        ${item.notes ? `<span>${escapeHtml(item.notes)}</span>` : ""}
        ${blocked.length ? `<span class="radar-blocked">Sin ejecutar: ${escapeHtml(blocked.map((sourceId) => repository.getSourceLabel(sourceId)).join(", "))}</span>` : ""}
        ${item.lastError ? `<span class="chase-error">${escapeHtml(item.lastError)}</span>` : ""}
      </div>
      ${searchActions(item)}
      ${isEditing ? searchForm(item, { formId: `radarEdit-${item.id}`, submitLabel: "Guardar cambios", cancelAction: "edit" }) : ""}
      ${manualListingForm(item)}
      <div class="chase-results">${
        results.length
          ? results.map(resultCard).join("")
          : `<p class="chase-empty-results">${escapeHtml(emptyResultsMessage(item))}</p>`
      }</div>
    </article>`;
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
      ${schedulePanel()}
      ${filterTabs(counts)}
      <section class="chasing-list">${
        items.length
          ? items.map(searchCard).join("")
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

    each("[data-lot-calc]", (button) =>
      button.addEventListener("click", () => {
        const opening = lotCalcId !== button.dataset.lotCalc;
        lotCalcId = opening ? button.dataset.lotCalc : "";
        lotCalcPieceCount = 3;
        lotCalcResult = null;
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
