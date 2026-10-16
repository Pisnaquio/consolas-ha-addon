(() => {
  /**
   * Nacho's Wishlist — los deseos reales del dueño.
   *
   * No es Oportunidades (lo que el mercado ofrece hoy) ni Chasing Games (lo
   * que el radar sale a buscar, sembrado con el top de cada consola). Es la
   * lista que el dueño arma a mano para el día que quiera darse un gusto:
   * cualquier pieza que figure acá es, por definición, un regalo que vale.
   *
   * Por eso la página muestra lo que falta antes que lo que ya está, y no
   * opina de precios: el precio de referencia es una ayuda para saber de qué
   * orden es el gasto, no un veredicto. Todo el estado sale de
   * `PersonalWishlistRepository`; esta página sólo decide cómo mostrarlo.
   */
  const wishlist = window.PersonalWishlistRepository;
  const root = document.getElementById("nachosWishlistRoot");

  const FILTERS = [
    { id: "pending", label: "Me faltan" },
    { id: "fulfilled", label: "Cumplidos" },
    { id: "all", label: "Todos" }
  ];

  let model = null;
  let error = "";
  let feedback = "";
  let feedbackTone = "info";
  let busy = false;
  let filter = "pending";
  let editor = null;
  let surprise = null;
  const openOwned = new Set();

  const escapeHtml = (value = "") =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  function setFeedback(message, tone = "info") {
    feedback = message || "";
    feedbackTone = tone;
  }

  async function reload() {
    model = await wishlist.load();
  }

  async function perform(pending, action, success) {
    if (busy) return;
    busy = true;
    setFeedback(pending);
    render();
    try {
      await action();
      await reload();
      setFeedback(success, "success");
    } catch (err) {
      setFeedback(err.message, "error");
    }
    busy = false;
    render();
  }

  function priceLine(range) {
    if (!range || (range.low == null && range.mid == null && range.high == null)) return "";
    if (range.mid != null) return `típico USD ${range.mid}`;
    return [range.low != null ? `desde USD ${range.low}` : "", range.high != null ? `hasta USD ${range.high}` : ""]
      .filter(Boolean)
      .join(" · ");
  }

  function radarSearchHref(entry) {
    const params = new URLSearchParams({
      open: "create",
      prefillName: `${entry.title} (${entry.platform})`,
      prefillPlatform: entry.platform
    });
    return `./chasing-games.html?${params.toString()}`;
  }

  // --- Hero ------------------------------------------------------------------

  function heroSection() {
    const totals = model.totals;
    const summary = totals.wishes
      ? `${totals.pending} ${totals.pending === 1 ? "deseo pendiente" : "deseos pendientes"} · ${totals.missingPieces} ${
          totals.missingPieces === 1 ? "pieza que te falta" : "piezas que te faltan"
        }${totals.fulfilled ? ` · ${totals.fulfilled} ${totals.fulfilled === 1 ? "cumplido" : "cumplidos"}` : ""}`
      : "Todavía no agregaste ningún deseo.";
    return `<header class="detail-hero nw-hero">
      <p class="eyebrow">Deseos de verdad</p>
      <h1>Nacho's Wishlist</h1>
      <p class="nw-summary">Lo que de verdad querés. No son recomendaciones ni oportunidades de mercado: cualquier cosa de acá que aparezca es un regalo que vale.</p>
      <p class="nw-count">${escapeHtml(summary)}</p>
      <div class="card-actions">
        <button class="btn-link btn-primary" type="button" data-action="new-wish"${editor ? " disabled" : ""}>Agregar deseo</button>
        ${totals.missingPieces ? `<button class="btn-link" type="button" data-action="surprise">Elegime un regalo</button>` : ""}
      </div>
    </header>`;
  }

  function surpriseBlock() {
    if (!surprise) return "";
    const { entry, wishTitle } = surprise;
    return `<section class="nw-surprise" aria-live="polite">
      <img class="nw-cover" src="${escapeHtml(entry.coverUrl)}" alt="" loading="lazy" />
      <div>
        <p class="eyebrow">Hoy te regalás</p>
        <p class="nw-surprise-title">${escapeHtml(entry.title)}</p>
        <p class="muted">${escapeHtml([entry.platform, wishTitle, priceLine(entry.priceRange)].filter(Boolean).join(" · "))}</p>
      </div>
      <div class="nw-surprise-actions">
        <a class="btn-link" href="${escapeHtml(radarSearchHref(entry))}">Buscar con Radar</a>
        <button class="btn-link" type="button" data-action="surprise">Otro</button>
        <button class="btn-link" type="button" data-action="close-surprise">Cerrar</button>
      </div>
    </section>`;
  }

  // --- Editor (alta y edición) -----------------------------------------------

  function openEditor(wish = null) {
    editor = wish
      ? {
          id: wish.id,
          kind: wish.kind,
          title: wish.title,
          note: wish.note,
          franchiseId: wish.franchiseId,
          items: wish.kind === "games" ? wish.entries.map((entry) => ({ ...entry })) : [],
          query: ""
        }
      : { id: "", kind: "games", title: "", note: "", franchiseId: "", items: [], query: "" };
    surprise = null;
    render();
    root.querySelector(editor.kind === "games" ? "[data-editor-query]" : "[data-editor-title]")?.focus();
  }

  function selectedChips() {
    if (!editor.items.length) return `<p class="muted nw-hint">Todavía no elegiste juegos.</p>`;
    return `<ul class="nw-chips">${editor.items
      .map(
        (item) => `<li class="chip">${escapeHtml(item.title)} <span class="muted">· ${escapeHtml(item.platform)}</span>
          <button type="button" class="nw-chip-remove" data-remove-item="${escapeHtml(item.key)}" aria-label="Sacar ${escapeHtml(item.title)}">×</button></li>`
      )
      .join("")}</ul>`;
  }

  function searchResults() {
    if (!editor.query.trim()) return `<p class="muted nw-hint">Buscá por juego y consola: «donkey snes», «pokemon game boy color».</p>`;
    const results = wishlist.searchGames(editor.query, model);
    if (!results.length) return `<p class="muted nw-hint">No hay juegos con ese nombre en la biblioteca.</p>`;
    const chosen = new Set(editor.items.map((item) => item.key));
    return `<ul class="nw-results">${results
      .map(
        (result) => `<li>
          <label class="nw-result${chosen.has(result.key) ? " is-chosen" : ""}">
            <input type="checkbox" data-pick-item="${escapeHtml(result.key)}"${chosen.has(result.key) ? " checked" : ""} />
            <img class="nw-thumb" src="${escapeHtml(result.coverUrl)}" alt="" loading="lazy" />
            <span><strong>${escapeHtml(result.title)}</strong><span class="muted"> · ${escapeHtml(result.platform)}${
              result.owned ? " · ya lo tenés" : ""
            }</span></span>
          </label>
        </li>`
      )
      .join("")}</ul>`;
  }

  function editorPanel() {
    if (!editor) return "";
    const franchiseOptions = model.franchiseOptions || [];
    return `<section class="detail-block nw-editor" aria-label="${editor.id ? "Editar deseo" : "Nuevo deseo"}">
      <div class="nw-editor-head">
        <h2>${editor.id ? "Editar deseo" : "Nuevo deseo"}</h2>
        ${
          franchiseOptions.length
            ? `<div class="pill-group" role="group" aria-label="Qué tipo de deseo">
                <button class="pill${editor.kind === "games" ? " is-active" : ""}" type="button" data-editor-kind="games">Juegos</button>
                <button class="pill${editor.kind === "franchise" ? " is-active" : ""}" type="button" data-editor-kind="franchise">Colección temática</button>
              </div>`
            : ""
        }
      </div>
      ${
        editor.kind === "games"
          ? `<label class="nw-field">Buscar juegos
              <input type="search" data-editor-query value="${escapeHtml(editor.query)}" placeholder="donkey snes" autocomplete="off" />
            </label>
            <div data-editor-results>${searchResults()}</div>
            <div data-editor-chips>${selectedChips()}</div>`
          : `<label class="nw-field">Colección
              <select data-editor-franchise>
                <option value="">Elegí una colección</option>
                ${franchiseOptions
                  .map(
                    (option) =>
                      `<option value="${escapeHtml(option.id)}"${option.id === editor.franchiseId ? " selected" : ""}>${escapeHtml(option.name)}</option>`
                  )
                  .join("")}
              </select>
            </label>
            <p class="muted nw-hint">Cuenta como cumplido cuando tengas en físico todos los lanzamientos principales de la estantería.</p>`
      }
      <label class="nw-field">Nombre del deseo
        <input type="text" data-editor-title value="${escapeHtml(editor.title)}" placeholder="${
          editor.kind === "games" ? "Todos los Donkey Kong de Super Nintendo" : "Colección God of War"
        }" maxlength="120" />
      </label>
      <label class="nw-field">Nota (opcional)
        <input type="text" data-editor-note value="${escapeHtml(editor.note)}" placeholder="CIB si aparece a buen precio" maxlength="240" />
      </label>
      <div class="card-actions">
        <button class="btn-link btn-primary" type="button" data-action="save-wish"${busy ? " disabled" : ""}>Guardar deseo</button>
        <button class="btn-link" type="button" data-action="cancel-editor">Cancelar</button>
      </div>
    </section>`;
  }

  /** Repinta sólo resultados y elegidos, para no robarle el foco al buscador. */
  function refreshEditorLists() {
    const results = root.querySelector("[data-editor-results]");
    const chips = root.querySelector("[data-editor-chips]");
    if (results) results.innerHTML = searchResults();
    if (chips) chips.innerHTML = selectedChips();
    bindEditorLists();
  }

  function editorTitleDefault() {
    if (editor.title.trim()) return editor.title.trim();
    if (editor.kind === "franchise") {
      const option = (model.franchiseOptions || []).find((item) => item.id === editor.franchiseId);
      return option ? `Colección ${option.name}` : "";
    }
    return editor.items.length === 1 ? editor.items[0].title : "";
  }

  function saveEditor() {
    const input = {
      id: editor.id || undefined,
      kind: editor.kind,
      title: editorTitleDefault(),
      note: editor.note,
      franchiseId: editor.franchiseId,
      items: editor.items.map((item) => ({ consoleId: item.consoleId, gameId: item.gameId }))
    };
    const editing = Boolean(editor.id);
    perform(
      "Guardando deseo…",
      async () => {
        wishlist.saveWish(input, { wishesById: model.wishesById });
        editor = null;
      },
      editing ? "Deseo actualizado." : "Deseo agregado."
    );
  }

  // --- Lista de deseos ---------------------------------------------------------

  function pieceRow(wish, entry) {
    const price = priceLine(entry.priceRange);
    const action =
      entry.source === "game"
        ? `<button class="btn-link" type="button" data-mark-owned="${escapeHtml(wish.id)}|${escapeHtml(entry.key)}"${
            busy ? " disabled" : ""
          }>Ya lo tengo</button>`
        : `<a class="btn-link" href="./franchise-collection.html?id=${encodeURIComponent(wish.franchiseId)}">Marcar en la colección</a>`;
    return `<li class="nw-piece">
      <img class="nw-cover" src="${escapeHtml(entry.coverUrl)}" alt="" loading="lazy" />
      <div class="nw-piece-id">
        <p class="nw-piece-title">${escapeHtml(entry.title)}</p>
        <p class="muted">${escapeHtml([entry.platform, price].filter(Boolean).join(" · "))}${
          entry.ownershipType === "digital" ? ` · <span class="nw-tag">la tenés en digital</span>` : ""
        }</p>
        ${entry.exists ? "" : `<p class="nw-warning">Este juego ya no está en la biblioteca de la consola.</p>`}
      </div>
      <div class="nw-piece-actions">
        <a class="btn-link" href="${escapeHtml(radarSearchHref(entry))}">Buscar con Radar</a>
        ${action}
      </div>
    </li>`;
  }

  function wishCard(wish) {
    const percent = wish.total ? Math.round((wish.owned / wish.total) * 100) : 0;
    const owned = wish.entries.filter((entry) => entry.owned);
    const showOwned = openOwned.has(wish.id);
    const progress =
      wish.total === 1 ? (wish.fulfilled ? "Ya lo tenés" : "Te falta") : `${wish.owned} de ${wish.total}`;
    return `<article class="nw-wish${wish.fulfilled ? " is-fulfilled" : ""}">
      <header class="nw-wish-head">
        <div>
          <h2 class="nw-wish-title">${escapeHtml(wish.title)}</h2>
          ${wish.note ? `<p class="nw-wish-note">${escapeHtml(wish.note)}</p>` : ""}
          ${
            wish.kind === "franchise"
              ? `<p class="muted">Estantería física de <a href="./franchise-collection.html?id=${encodeURIComponent(
                  wish.franchiseId
                )}">${escapeHtml(wish.franchiseName || wish.franchiseId)}</a></p>`
              : ""
          }
        </div>
        <div class="nw-wish-progress">
          <span class="nw-progress-value">${escapeHtml(progress)}</span>
          ${wish.total > 1 ? `<span class="nw-track"><span style="width:${percent}%"></span></span>` : ""}
        </div>
      </header>
      ${wish.total ? "" : `<p class="nw-warning">Esta colección todavía no tiene lanzamientos cargados.</p>`}
      ${wish.missing.length ? `<ul class="nw-pieces">${wish.missing.map((entry) => pieceRow(wish, entry)).join("")}</ul>` : ""}
      ${
        owned.length
          ? `<button class="nw-owned-toggle" type="button" data-toggle-owned="${escapeHtml(wish.id)}" aria-expanded="${showOwned}">
              ${showOwned ? "Ocultar" : "Ver"} lo que ya tenés (${owned.length})
            </button>
            ${
              showOwned
                ? `<ul class="nw-owned">${owned
                    .map((entry) => `<li>${escapeHtml(entry.title)} <span class="muted">· ${escapeHtml(entry.platform)}</span></li>`)
                    .join("")}</ul>`
                : ""
            }`
          : ""
      }
      <footer class="nw-wish-actions">
        <button class="btn-link" type="button" data-edit-wish="${escapeHtml(wish.id)}">Editar</button>
        <button class="btn-link" type="button" data-delete-wish="${escapeHtml(wish.id)}">Borrar deseo</button>
      </footer>
    </article>`;
  }

  function filterBar() {
    if (!model.wishes.length) return "";
    const counts = { pending: model.totals.pending, fulfilled: model.totals.fulfilled, all: model.totals.wishes };
    return `<nav class="nw-filters pill-group" aria-label="Filtrar deseos">${FILTERS.map(
      (item) =>
        `<button class="pill${filter === item.id ? " is-active" : ""}" type="button" data-filter="${item.id}">${escapeHtml(item.label)} (${counts[item.id]})</button>`
    ).join("")}</nav>`;
  }

  function wishesSection() {
    if (!model.wishes.length) {
      return `<section class="detail-block nw-empty">
        <p>Agregá lo que de verdad querés: un juego puntual, todos los de una saga en una consola, o una colección temática entera.</p>
      </section>`;
    }
    const visible = model.wishes.filter((wish) =>
      filter === "all" ? true : filter === "fulfilled" ? wish.fulfilled : !wish.fulfilled
    );
    if (!visible.length) {
      return `<p class="muted nw-hint">${filter === "fulfilled" ? "Todavía no cumpliste ningún deseo." : "No te falta nada. Hora de desear algo nuevo."}</p>`;
    }
    return `<section class="nw-wishes">${visible.map(wishCard).join("")}</section>`;
  }

  // --- Render y eventos --------------------------------------------------------

  function render() {
    if (error) {
      root.innerHTML = `<section class="detail-block">
        <p class="eyebrow">Nacho's Wishlist</p>
        <h1>No pudimos cargar la wishlist</h1>
        <p>${escapeHtml(error)}</p>
        <a class="btn-link" href="./index.html">Volver a la colección</a>
      </section>`;
      return;
    }
    if (!model) {
      root.innerHTML = `<p class="muted">Cargando…</p>`;
      return;
    }
    root.innerHTML = `<div class="back-link"><a href="${
      window.CollectionRepository?.getHomeHref?.() || "./index.html"
    }">← Volver a la colección</a></div>
      ${heroSection()}
      ${feedback ? `<p class="nw-feedback is-${escapeHtml(feedbackTone)}" role="status">${escapeHtml(feedback)}</p>` : ""}
      ${surpriseBlock()}
      ${editorPanel()}
      ${filterBar()}
      ${wishesSection()}`;
    bindEvents();
  }

  const each = (selector, handler) => root.querySelectorAll(selector).forEach(handler);

  function findWish(wishId) {
    return model.wishes.find((wish) => wish.id === wishId) || null;
  }

  function bindEditorLists() {
    each("[data-pick-item]", (input) =>
      input.addEventListener("change", () => {
        const key = input.dataset.pickItem;
        if (input.checked) {
          const result = wishlist.searchGames(editor.query, model).find((item) => item.key === key);
          if (result && !editor.items.some((item) => item.key === key)) editor.items.push(result);
        } else {
          editor.items = editor.items.filter((item) => item.key !== key);
        }
        refreshEditorLists();
      })
    );
    each("[data-remove-item]", (button) =>
      button.addEventListener("click", () => {
        editor.items = editor.items.filter((item) => item.key !== button.dataset.removeItem);
        refreshEditorLists();
      })
    );
  }

  function bindEvents() {
    root.querySelector('[data-action="new-wish"]')?.addEventListener("click", () => openEditor());
    each('[data-action="surprise"]', (button) =>
      button.addEventListener("click", () => {
        surprise = wishlist.pickSurprise(model);
        render();
      })
    );
    root.querySelector('[data-action="close-surprise"]')?.addEventListener("click", () => {
      surprise = null;
      render();
    });

    if (editor) {
      each("[data-editor-kind]", (button) =>
        button.addEventListener("click", () => {
          editor.kind = button.dataset.editorKind;
          render();
        })
      );
      root.querySelector("[data-editor-query]")?.addEventListener("input", (event) => {
        editor.query = event.target.value;
        refreshEditorLists();
      });
      root.querySelector("[data-editor-franchise]")?.addEventListener("change", (event) => {
        editor.franchiseId = event.target.value;
      });
      root.querySelector("[data-editor-title]")?.addEventListener("input", (event) => {
        editor.title = event.target.value;
      });
      root.querySelector("[data-editor-note]")?.addEventListener("input", (event) => {
        editor.note = event.target.value;
      });
      root.querySelector('[data-action="save-wish"]')?.addEventListener("click", saveEditor);
      root.querySelector('[data-action="cancel-editor"]')?.addEventListener("click", () => {
        editor = null;
        render();
      });
      bindEditorLists();
    }

    each("[data-filter]", (button) =>
      button.addEventListener("click", () => {
        filter = button.dataset.filter;
        render();
      })
    );
    each("[data-toggle-owned]", (button) =>
      button.addEventListener("click", () => {
        const id = button.dataset.toggleOwned;
        if (openOwned.has(id)) openOwned.delete(id);
        else openOwned.add(id);
        render();
      })
    );
    each("[data-edit-wish]", (button) =>
      button.addEventListener("click", () => {
        const wish = findWish(button.dataset.editWish);
        if (wish) openEditor(wish);
      })
    );
    each("[data-delete-wish]", (button) =>
      button.addEventListener("click", () => {
        const wish = findWish(button.dataset.deleteWish);
        if (!wish || !window.confirm(`¿Borrar el deseo «${wish.title}»? Los juegos no se tocan.`)) return;
        perform("Borrando deseo…", async () => wishlist.deleteWish(wish.id), "Deseo borrado.");
      })
    );
    each("[data-mark-owned]", (button) =>
      button.addEventListener("click", () => {
        const [wishId, key] = button.dataset.markOwned.split("|");
        const entry = findWish(wishId)?.entries.find((item) => item.key === key);
        if (!entry || !window.confirm(`¿Registrar ${entry.title} como tuyo en físico?`)) return;
        perform(
          `Registrando ${entry.title}…`,
          async () => wishlist.markGameOwned(entry, model.baseGamesByConsole),
          `${entry.title} quedó registrado en físico. Precio y estado se completan desde la consola.`
        );
      })
    );
  }

  async function start() {
    render();
    try {
      await reload();
    } catch (err) {
      error = err.message;
    }
    render();
  }

  start();
})();
