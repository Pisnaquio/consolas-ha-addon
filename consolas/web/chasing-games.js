(() => {
  const API = window.CONSOLAS_API_BASE || "./api";
  const root = document.getElementById("chasingGamesRoot");
  let model = null;
  let feedback = "";

  const escapeHtml = (value = "") => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const dateLabel = (value) => value ? new Intl.DateTimeFormat("es-UY", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "todavía no buscado";

  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json", "X-Consolas-Chasing-Games": "1" } : {}), ...(options.headers || {}) }
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || "No pudimos completar la acción.");
    return payload;
  }

  async function load() {
    try {
      model = await request("/chasing-games");
      render();
    } catch (error) {
      root.innerHTML = `<section class="detail-block chasing-empty"><p class="eyebrow">Chasing Games</p><h1>No está disponible en este origen</h1><p class="muted">Esta sección necesita el backend de Consolas para guardar las búsquedas y consultar eBay.</p><a class="btn-link" href="./index.html">Volver a la colección</a></section>`;
    }
  }

  function resultCard(result) {
    const meta = [result.conditionLabel, result.shippingLabel, result.locationLabel].filter(Boolean).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
    return `<article class="chase-result">
      ${result.imageUrl ? `<img src="${escapeHtml(result.imageUrl)}" alt="" loading="lazy" onerror="this.remove()" />` : ""}
      <div><p class="eyebrow">${escapeHtml(result.listingType || "eBay")}</p><h3>${escapeHtml(result.title)}</h3><div class="chase-result-meta">${meta || "<span>Detalles a confirmar</span>"}</div></div>
      <div class="chase-result-price"><strong>${escapeHtml(result.priceLabel || "Ver precio")}</strong><a class="btn-link" href="${escapeHtml(result.listingUrl)}" target="_blank" rel="noreferrer noopener">Ver en eBay</a></div>
    </article>`;
  }

  function chaseCard(item) {
    const results = item.results || [];
    const sourceLabel = model?.source || "eBay USA";
    return `<article class="detail-block chase-card ${item.enabled ? "" : "is-paused"}">
      <div class="chase-card-head"><div><p class="eyebrow">${escapeHtml(item.platform || "Sin plataforma")} · ${escapeHtml(sourceLabel)}</p><h2>${escapeHtml(item.title)}</h2><p class="muted">Consulta: ${escapeHtml(item.searchQuery)}</p></div><span class="chase-status">${item.enabled ? "Siguiendo" : "Pausada"}</span></div>
      <div class="chase-card-meta"><span>Última búsqueda: ${escapeHtml(dateLabel(item.lastCheckedAt))}</span><span>${results.length} resultados activos</span>${item.lastError ? `<span class="chase-error">${escapeHtml(item.lastError)}</span>` : ""}</div>
      <div class="card-actions chase-actions"><button class="btn-link btn-primary" type="button" data-run="${escapeHtml(item.id)}">Buscar ahora</button><button class="btn-link" type="button" data-toggle="${escapeHtml(item.id)}" data-enabled="${!item.enabled}">${item.enabled ? "Pausar" : "Reanudar"}</button><button class="btn-link chase-delete" type="button" data-delete="${escapeHtml(item.id)}">Dejar de buscar</button></div>
      <div class="chase-results">${results.length ? results.map(resultCard).join("") : `<p class="chase-empty-results">Todavía no hay resultados guardados. Usá “Buscar ahora” o esperá la próxima revisión.</p>`}</div>
    </article>`;
  }

  function render() {
    const items = model?.items || [];
    const isSandbox = model?.environment === "sandbox";
    const sourceLabel = model?.source || "eBay USA";
    const sourceNotice = isSandbox ? `<p class="chasing-sandbox-notice">Modo Sandbox: la conexión y el seguimiento se prueban contra eBay, pero las publicaciones no son compras reales.</p>` : "";
    root.innerHTML = `<div class="back-link"><a href="./index.html">← Volver a la colección</a></div>
      <header class="detail-hero chasing-hero"><div><p class="eyebrow">Chasing Games</p><h1>Juegos que estás persiguiendo</h1><p>Seguimientos independientes de Auction Watch. Elegí un juego, mantenelo activo y revisamos ${escapeHtml(sourceLabel)} periódicamente hasta que lo pauses.</p>${sourceNotice}</div><div class="chasing-count"><strong>${items.filter((item) => item.enabled).length}</strong><span>búsquedas activas</span></div></header>
      <section class="detail-block chasing-add"><div><p class="eyebrow">Nueva búsqueda</p><h2>Agregar un juego a la cacería</h2><p class="muted">Usá el título exacto y la plataforma para evitar resultados parecidos.</p></div><form id="chasingForm"><input name="title" required maxlength="200" placeholder="Ej: International Superstar Soccer Deluxe" /><input name="platform" maxlength="100" placeholder="Plataforma, ej: SNES" /><button class="btn-link btn-primary" type="submit">Empezar a buscar</button></form></section>
      ${feedback ? `<p class="chasing-feedback" role="status">${escapeHtml(feedback)}</p>` : ""}
      <section class="chasing-list">${items.length ? items.map(chaseCard).join("") : `<article class="detail-block chasing-empty"><h2>Sin juegos en seguimiento</h2><p class="muted">Agregá un título para empezar a buscarlo.</p></article>`}</section>`;
    bindEvents();
  }

  function bindEvents() {
    document.getElementById("chasingForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try { feedback = "Buscando en eBay…"; render(); await request("/chasing-games", { method: "POST", body: JSON.stringify({ title: form.get("title"), platform: form.get("platform") }) }); feedback = "La búsqueda quedó activa y sus resultados ya se guardaron."; await load(); } catch (error) { feedback = error.message; render(); }
    });
    root.querySelectorAll("[data-run]").forEach((button) => button.addEventListener("click", async () => { try { feedback = "Buscando en eBay…"; render(); await request(`/chasing-games/${button.dataset.run}/run`, { method: "POST", body: "{}" }); feedback = "Resultados actualizados."; await load(); } catch (error) { feedback = error.message; render(); } }));
    root.querySelectorAll("[data-toggle]").forEach((button) => button.addEventListener("click", async () => { try { await request(`/chasing-games/${button.dataset.toggle}/enabled`, { method: "POST", body: JSON.stringify({ enabled: button.dataset.enabled === "true" }) }); feedback = "Seguimiento actualizado."; await load(); } catch (error) { feedback = error.message; render(); } }));
    root.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", async () => { if (!confirm("¿Dejar de buscar este juego? Sus resultados guardados se eliminarán.")) return; try { await request(`/chasing-games/${button.dataset.delete}`, { method: "DELETE", body: "{}" }); feedback = "Seguimiento eliminado."; await load(); } catch (error) { feedback = error.message; render(); } }));
  }
  load();
})();
