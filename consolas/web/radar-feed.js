(() => {
  /**
   * Collection Radar · Para mí.
   *
   * La lista corta de decisiones del PRD §17: hasta diez oportunidades, cada una
   * con por qué sirve, cuánto cuesta de verdad y qué hacer con ella.
   *
   * La calibración del 2026-09 ordenó esta pantalla: de 26 «tal vez», 9 decían
   * «caro». Casi ninguno era un rechazo — eran «avisame si baja». Por eso
   * **Seguir** es la acción principal y una baja de precio encabeza el feed.
   *
   * Títulos y descripciones vienen de un marketplace: se escapan siempre y
   * nunca se interpretan.
   */
  const repository = window.RadarRepository;
  const root = document.getElementById("radarFeedRoot");

  const DISMISS_REASONS = [
    { id: "caro", label: "Está caro" },
    { id: "condicion", label: "Por su condición" },
    { id: "region", label: "Región equivocada" },
    { id: "ya-lo-tengo", label: "Ya lo tengo" },
    { id: "no-es-lo-que-busco", label: "No es lo que busco" },
    { id: "dudoso", label: "Me genera dudas" },
    { id: "no-me-interesa", label: "No me interesa" },
  ];

  const SNOOZE_OPTIONS = [
    { days: 7, label: "una semana" },
    { days: 30, label: "un mes" },
    { days: 90, label: "tres meses" },
  ];

  let feed = null;
  let budget = null;
  let dismissing = "";
  let purchasing = "";
  let editingBudget = false;
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

  const money = (amount, currency) =>
    amount == null ? "" : `${currency || "USD"} ${Number(amount).toLocaleString("es-UY", { maximumFractionDigits: 2 })}`;

  function setFeedback(message, tone = "info") {
    feedback = message || "";
    feedbackTone = tone;
  }

  async function reload() {
    const [nextFeed, nextBudget] = await Promise.all([
      repository.loadFeed(),
      repository.loadBudget().catch(() => null), // el presupuesto es complementario: sin él, el feed igual funciona
    ]);
    feed = nextFeed;
    budget = nextBudget;
    render();
  }

  async function perform(pending, action, success) {
    if (busy) return false;
    busy = true;
    setFeedback(pending);
    render();
    try {
      await action();
      busy = false;
      setFeedback(success, "success");
      await reload();
      return true;
    } catch (error) {
      busy = false;
      setFeedback(error.message, "error");
      render();
      return false;
    }
  }

  function budgetWidget() {
    if (!budget) return "";
    if (editingBudget) {
      return `<form class="budget-widget budget-edit" data-budget-form="1">
        <label for="budgetInput">Presupuesto mensual (USD)</label>
        <input id="budgetInput" name="monthlyBudgetUsd" type="number" min="0" step="1"
          value="${budget.monthlyBudgetUsd != null ? budget.monthlyBudgetUsd : ""}"
          placeholder="Sin configurar" />
        <button class="btn-link btn-primary" type="submit">Guardar</button>
        <button class="btn-link" type="button" data-cancel-budget="1">Cancelar</button>
      </form>`;
    }
    if (!budget.configured) {
      return `<div class="budget-widget">
        <span class="muted">Presupuesto mensual sin configurar.</span>
        <button class="btn-link" type="button" data-edit-budget="1">Configurar</button>
      </div>`;
    }
    const overBudget = budget.available != null && budget.available < 0;
    return `<div class="budget-widget${overBudget ? " is-over" : ""}">
      <div><strong>${escapeHtml(money(budget.monthlyBudgetUsd, "USD"))}</strong><span>presupuesto de ${escapeHtml(budget.month)}</span></div>
      <div><strong>${escapeHtml(money(budget.spent, "USD"))}</strong><span>gastado (${budget.spentCount})</span></div>
      <div><strong>${escapeHtml(money(budget.reserved, "USD"))}</strong><span>reservado (${budget.reservedCount})</span></div>
      <div><strong>${escapeHtml(money(budget.available, "USD"))}</strong><span>${overBudget ? "sobre el presupuesto" : "disponible"}</span></div>
      <button class="btn-link" type="button" data-edit-budget="1">Editar</button>
    </div>`;
  }

  function dropBadge(drop) {
    if (!drop) return "";
    const percent = Math.round(drop.ratio * 100);
    return `<span class="drop">
      ↓ ${percent}%
      <s>${escapeHtml(money(drop.previous, "USD"))}</s>
      ${drop.material ? "· baja importante" : ""}
    </span>`;
  }

  function decisionLine(item) {
    const decision = item.decision;
    if (!decision) return "";
    if (decision.decision === "following") return `<p class="feed-decision is-following">La estás siguiendo</p>`;
    if (decision.decision === "snoozed") {
      return `<p class="feed-decision">Dormida hasta ${escapeHtml(repository.formatSlotTime(decision.snoozeUntil))}</p>`;
    }
    return "";
  }

  /** La búsqueda que matcheó tiene que decir a qué consola escribiría "Registrar compra". */
  function purchasableEntity(item) {
    const match = (item.matches || []).find((m) => m.entityType && m.entityId);
    return match && window.RadarPurchase?.canWriteCollection(match.entityType) ? match : null;
  }

  function actions(item) {
    const id = escapeHtml(item.id);
    const following = item.decision?.decision === "following";
    const reserved = item.decision?.reserved === true;
    const purchased = item.decision?.decision === "purchased";
    const entity = purchasableEntity(item);
    const snoozeMenu = SNOOZE_OPTIONS.map(
      (option) => `<button class="btn-link" type="button" data-snooze="${id}" data-days="${option.days}">${escapeHtml(option.label)}</button>`
    ).join("");
    return `<div class="card-actions feed-actions">
      <a class="btn-link btn-primary" href="${escapeHtml(item.listingUrl)}" target="_blank" rel="noreferrer noopener">Ver publicación</a>
      <button class="btn-link${following ? " is-on" : ""}" type="button" data-follow="${id}">
        ${following ? "Dejar de seguir" : "Seguir"}
      </button>
      ${
        following
          ? `<button class="btn-link${reserved ? " is-on" : ""}" type="button" data-reserve="${id}">
               ${reserved ? "Quitar del presupuesto" : "Reservar en el presupuesto"}
             </button>`
          : ""
      }
      ${snoozeMenu}
      ${
        entity && !purchased
          ? `<button class="btn-link btn-primary" type="button" data-purchase="${id}">Registrar compra</button>`
          : ""
      }
      <button class="btn-link chase-delete" type="button" data-dismiss="${id}">Descartar</button>
    </div>`;
  }

  function purchaseForm(item) {
    if (purchasing !== item.id) return "";
    const entity = purchasableEntity(item);
    if (!entity) return "";
    const id = escapeHtml(item.id);
    return `<form class="purchase-form" data-purchase-form="${id}" data-entity-type="${escapeHtml(entity.entityType)}" data-entity-id="${escapeHtml(entity.entityId)}">
      <p class="muted">Se va a marcar «Tengo» en ${escapeHtml(entity.entityType === "console" ? "esta consola" : entity.entityId)} con el precio pagado.</p>
      <label class="visually-hidden" for="price-${id}">Precio pagado</label>
      <input id="price-${id}" name="priceAmount" type="number" min="0" step="0.01"
        value="${item.priceAmount != null ? item.priceAmount : ""}" placeholder="Precio pagado" required />
      <button class="btn-link btn-primary" type="submit">Confirmar compra</button>
      <button class="btn-link" type="button" data-cancel-purchase="1">Cancelar</button>
    </form>`;
  }

  function dismissForm(item) {
    if (dismissing !== item.id) return "";
    return `<form class="dismiss-form" data-dismiss-form="${escapeHtml(item.id)}">
      <label class="visually-hidden" for="reason-${escapeHtml(item.id)}">Motivo</label>
      <select id="reason-${escapeHtml(item.id)}" name="reason">
        ${DISMISS_REASONS.map((reason) => `<option value="${escapeHtml(reason.id)}">${escapeHtml(reason.label)}</option>`).join("")}
      </select>
      <input name="note" maxlength="400" placeholder="Nota opcional" />
      <button class="btn-link btn-primary" type="submit">Descartar</button>
      <button class="btn-link" type="button" data-cancel-dismiss="1">Cancelar</button>
    </form>`;
  }

  function card(item) {
    const reasons = (item.matches?.[0]?.reasons || []).slice(0, 3).map((r) => `<li>${escapeHtml(r)}</li>`).join("");
    const unverified = (item.matches?.[0]?.unverified || []).slice(0, 2).map((u) => `<li class="u">${escapeHtml(u)}</li>`).join("");
    const searches = (item.matches || []).map((m) => m.searchName).filter(Boolean);
    const imported = item.valuation?.cost?.importedTotal;

    return `<article class="detail-block chase-card feed-card${item.priceDrop ? " has-drop" : ""}">
      <div class="feed-card-head">
        <div>
          <p class="eyebrow">${escapeHtml(item.listingType || item.sourceLabel)}${
            item.band ? ` · ${escapeHtml(repository.getBandLabel(item.band))}` : ""
          }${item.score != null ? ` · ${item.score}/100` : ""}</p>
          <h2>${escapeHtml(item.title)}</h2>
          ${searches.length ? `<p class="feed-match">Coincide con ${escapeHtml(searches.join(" · "))}</p>` : ""}
        </div>
        <div class="feed-price">
          <strong>${escapeHtml(item.priceLabel || "Ver precio")}</strong>
          ${item.shippingLabel ? `<span>${escapeHtml(item.shippingLabel)}</span>` : ""}
          ${imported != null ? `<span>≈ ${escapeHtml(money(imported, item.priceCurrency))} puesto acá</span>` : ""}
        </div>
      </div>
      ${item.priceDrop ? dropBadge(item.priceDrop) : ""}
      ${reasons || unverified ? `<ul class="feed-why">${reasons}${unverified}</ul>` : ""}
      ${decisionLine(item)}
      ${actions(item)}
      ${dismissForm(item)}
      ${purchaseForm(item)}
    </article>`;
  }

  function render() {
    const items = feed?.items || [];
    const following = feed?.following || [];
    const counts = feed?.counts || {};
    const sandbox = feed?.environment === "sandbox";

    root.innerHTML = `<div class="back-link"><a href="./index.html">← Volver a la colección</a></div>
      <header class="detail-hero feed-hero">
        <div>
          <p class="eyebrow">Collection Radar</p>
          <h1>Para mí</h1>
          <p>Las oportunidades accionables de hoy, con por qué sirven y cuánto cuestan de verdad. Lo que descartás no vuelve; lo que dormís vuelve si baja de precio.</p>
          ${sandbox ? `<p class="chasing-sandbox-notice">Modo Sandbox: las publicaciones no son compras reales.</p>` : ""}
        </div>
        <div class="feed-counts">
          <div><strong>${counts.feed || 0}</strong><span>hoy</span></div>
          <div><strong>${counts.following || 0}</strong><span>siguiendo</span></div>
          <div><strong>${counts.dismissed || 0}</strong><span>descartadas</span></div>
          <div><strong>${counts.snoozed || 0}</strong><span>dormidas</span></div>
        </div>
      </header>
      ${budgetWidget()}
      ${feedback ? `<p class="chasing-feedback is-${escapeHtml(feedbackTone)}" role="status">${escapeHtml(feedback)}</p>` : ""}
      <section class="feed-list">${
        items.length
          ? items.map(card).join("")
          : `<article class="detail-block feed-empty">
              <h2>Nada accionable ahora</h2>
              <p>Sin novedades no hay aviso. Cuando una corrida traiga algo que encaje, aparece acá.</p>
              <a class="btn-link" href="./chasing-games.html">Ver búsquedas</a>
            </article>`
      }</section>
      ${
        following.length
          ? `<h2 class="feed-section-title">Siguiendo</h2>
             <p class="muted">Te avisamos si alguna baja de precio.</p>
             <section class="feed-list">${following.map(card).join("")}</section>`
          : ""
      }`;
    bindEvents();
  }

  function renderUnavailable() {
    root.innerHTML = `<section class="detail-block feed-empty">
      <p class="eyebrow">Collection Radar</p>
      <h1>No está disponible en este origen</h1>
      <p>Esta sección necesita el backend de Consolas.</p>
      <a class="btn-link" href="./index.html">Volver a la colección</a>
    </section>`;
  }

  const each = (selector, handler) => (root.querySelectorAll?.(selector) || []).forEach(handler);

  function bindEvents() {
    each("[data-follow]", (button) =>
      button.addEventListener("click", () => {
        const id = button.dataset.follow;
        const item = [...(feed?.items || []), ...(feed?.following || [])].find((entry) => entry.id === id);
        const isFollowing = item?.decision?.decision === "following";
        return perform(
          isFollowing ? "Quitando de seguidas…" : "Siguiendo…",
          () => (isFollowing ? repository.clearDecision(id) : repository.decide(id, { decision: "following" })),
          isFollowing ? "Ya no la seguís." : "La vas a ver acá hasta que decidas, y te avisa si baja."
        );
      })
    );

    each("[data-reserve]", (button) =>
      button.addEventListener("click", () => {
        const id = button.dataset.reserve;
        const item = [...(feed?.items || []), ...(feed?.following || [])].find((entry) => entry.id === id);
        const isReserved = item?.decision?.reserved === true;
        return perform(
          isReserved ? "Quitando del presupuesto…" : "Reservando…",
          () => repository.decide(id, { decision: "following", reserved: !isReserved }),
          isReserved ? "Ya no cuenta como plan probable." : "Cuenta como plan probable en el presupuesto del mes."
        );
      })
    );

    each("[data-edit-budget]", (button) =>
      button.addEventListener("click", () => {
        editingBudget = true;
        render();
      })
    );

    each("[data-cancel-budget]", (button) =>
      button.addEventListener("click", () => {
        editingBudget = false;
        render();
      })
    );

    const budgetForm = root.querySelector?.("[data-budget-form]");
    budgetForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const raw = String(data.get("monthlyBudgetUsd") || "").trim();
      await perform(
        "Guardando presupuesto…",
        () => repository.updateBudget(raw === "" ? null : Number(raw)),
        "Presupuesto actualizado."
      );
      editingBudget = false;
      render();
    });

    each("[data-purchase]", (button) =>
      button.addEventListener("click", () => {
        purchasing = purchasing === button.dataset.purchase ? "" : button.dataset.purchase;
        render();
      })
    );

    each("[data-cancel-purchase]", (button) =>
      button.addEventListener("click", () => {
        purchasing = "";
        render();
      })
    );

    const purchaseFormEl = root.querySelector?.("[data-purchase-form]");
    purchaseFormEl?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const listingId = event.currentTarget.dataset.purchaseForm;
      const entityType = event.currentTarget.dataset.entityType;
      const entityId = event.currentTarget.dataset.entityId;
      const priceAmount = Number(data.get("priceAmount"));
      await perform(
        "Registrando compra…",
        () => window.RadarPurchase.registerPurchase({ listingId, entityType, entityId, priceAmount, currency: "USD" }),
        "Compra registrada. Ya la marcamos como tuya en la colección."
      );
      purchasing = "";
      render();
    });

    each("[data-snooze]", (button) =>
      button.addEventListener("click", () => {
        const until = new Date(Date.now() + Number(button.dataset.days) * 86400000).toISOString();
        return perform(
          "Guardando…",
          () => repository.decide(button.dataset.snooze, { decision: "snoozed", snoozeUntil: until }),
          "Dormida. Vuelve antes si baja de precio de forma importante."
        );
      })
    );

    each("[data-dismiss]", (button) =>
      button.addEventListener("click", () => {
        dismissing = dismissing === button.dataset.dismiss ? "" : button.dataset.dismiss;
        render();
      })
    );

    each("[data-cancel-dismiss]", (button) =>
      button.addEventListener("click", () => {
        dismissing = "";
        render();
      })
    );

    const form = root.querySelector?.(`[data-dismiss-form]`);
    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const id = event.currentTarget.dataset.dismissForm;
      await perform(
        "Descartando…",
        () =>
          repository.decide(id, {
            decision: "dismissed",
            reason: String(data.get("reason") || ""),
            note: String(data.get("note") || "").trim(),
          }),
        "Descartada. No vuelve a aparecer.",
      );
      dismissing = "";
      render();
    });
  }

  async function start() {
    try {
      await reload();
    } catch (error) {
      renderUnavailable();
    }
  }

  start();
})();
