(() => {
  /**
   * Collection Radar · Value Ladder (PRD §11.1).
   *
   * Una ficha de consola muestra un precio. Eso no alcanza para decidir: una
   * Atari de cuatro switches y una Heavy Sixer no son la misma compra aunque
   * el catálogo las llame «Atari 2600». Esta guía explica qué variante conviene
   * para jugar, cuál para coleccionar y por qué existe el premium.
   *
   * Regla de esta pantalla: **no inventar datos**. Una Value Ladder es un
   * conjunto de afirmaciones factuales sobre hardware. Lo que está verificado
   * se muestra con su fuente y su fecha; lo que falta se declara como pendiente
   * en vez de completarse con una estimación que parezca un dato.
   *
   * Por eso no hay importes por variante: el catálogo guarda un precio por
   * consola, no por revisión, y repartirlo entre variantes sería atribuirle una
   * precisión que no tiene.
   */

  const escapeHtml = (value = "") =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  function findLadder(payload, consoleId) {
    if (!payload || !consoleId) return null;
    const ladder = payload.ladders?.[consoleId];
    if (!ladder || !Array.isArray(ladder.variants) || !ladder.variants.length) return null;
    return ladder;
  }

  function badges(variant, { playPick, collectPick, ownedVariantId }) {
    const marks = [];
    if (variant.id === ownedVariantId) marks.push(`<span class="ladder-badge is-mine">La tuya</span>`);
    if (variant.id === playPick) marks.push(`<span class="ladder-badge is-play">Para jugar</span>`);
    if (variant.id === collectPick) marks.push(`<span class="ladder-badge is-collect">Para coleccionar</span>`);
    return marks.join("");
  }

  function fact(label, value) {
    if (!value) return "";
    return `<div class="ladder-fact"><small>${escapeHtml(label)}</small><p>${escapeHtml(value)}</p></div>`;
  }

  function variantCard(variant, context) {
    const meta = [variant.years, variant.region].filter(Boolean).join(" · ");
    const owned = variant.id === context.ownedVariantId;
    const pickable = context.owned;
    return `<article class="ladder-variant${owned ? " is-mine" : ""}" data-variant="${escapeHtml(variant.id)}">
      <header>
        <div>
          <h4>${escapeHtml(variant.name)}</h4>
          ${meta ? `<p class="ladder-meta">${escapeHtml(meta)}</p>` : ""}
        </div>
        <div class="ladder-badges">${badges(variant, context)}</div>
      </header>
      <div class="ladder-facts">
        ${fact("Cómo reconocerla", variant.identify)}
        ${fact("Diferencias técnicas", variant.technical)}
        ${fact("Confiabilidad", variant.reliability)}
      </div>
      ${variant.note ? `<p class="ladder-note">${escapeHtml(variant.note)}</p>` : ""}
      ${
        pickable
          ? `<button class="btn-link ladder-pick" type="button" data-ladder-own="${escapeHtml(variant.id)}">${
              owned ? "No es la mía" : "Esta es la mía"
            }</button>`
          : ""
      }
    </article>`;
  }

  function sources(ladder) {
    const list = (ladder.sources || [])
      .map((source) =>
        source.url
          ? `<li><a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(source.label || source.url)}</a></li>`
          : `<li>${escapeHtml(source.label || "")}</li>`
      )
      .join("");
    if (!list) return "";
    return `<div class="ladder-sources">
      <small>Fuentes · verificado ${escapeHtml(ladder.verifiedAt || "sin fecha")}</small>
      <ul>${list}</ul>
    </div>`;
  }

  function pending(ladder, shared = []) {
    const list = (entries) => entries.map((entry) => `<li>${escapeHtml(entry)}</li>`).join("");
    const own = list(ladder.pending || []);
    const all = list(shared || []);
    if (!own && !all) return "";
    return `<div class="ladder-pending">
      <small>Todavía sin verificar</small>
      ${own ? `<ul>${own}</ul>` : ""}
      ${all ? `<small>En toda la guía</small><ul>${all}</ul>` : ""}
    </div>`;
  }

  function upgradeHint(ladder, owned) {
    // Un upgrade sólo se sugiere si la guía ya nombra una variante mejor que la
    // que el usuario tiene. No se calcula nada: se dice lo que está escrito.
    const byId = (id) => (ladder.variants || []).find((variant) => variant.id === id);
    const hints = [];
    if (ladder.playPick && ladder.playPick !== owned.id) {
      const pick = byId(ladder.playPick);
      if (pick) hints.push(`para jugar, la recomendada es la ${pick.name}`);
    }
    if (ladder.collectPick && ladder.collectPick !== owned.id) {
      const pick = byId(ladder.collectPick);
      if (pick) hints.push(`para coleccionar, la buscada es la ${pick.name}`);
    }
    if (!hints.length) return " Es la que la guía recomienda.";
    return ` Si alguna vez la cambiás: ${hints.join("; ")}.`;
  }

  function ownedLine(ladder, ownedVariantId, owned) {
    if (!owned) return "";
    const match = (ladder.variants || []).find((variant) => variant.id === ownedVariantId);
    if (match) return `<p class="ladder-owned">Tenés una ${escapeHtml(match.name)}.${escapeHtml(upgradeHint(ladder, match))}</p>`;
    return `<p class="ladder-owned is-missing">No registraste qué variante tenés. Marcala abajo y la guía te dice si vale la pena cambiarla.</p>`;
  }

  function renderMissing(consoleName) {
    return `<section class="detail-block value-ladder is-empty" aria-label="Guía de modelos y valor">
      <div class="section-head"><h3>Guía de modelos y valor</h3></div>
      <p>Todavía no hay guía de variantes para ${escapeHtml(consoleName || "esta consola")}. Se cargan primero las consolas que tenés, después las que buscás.</p>
    </section>`;
  }

  function render(ladder, options = {}) {
    if (!ladder) return renderMissing(options.consoleName);
    const context = {
      playPick: ladder.playPick || "",
      collectPick: ladder.collectPick || "",
      ownedVariantId: options.ownedVariantId || "",
      owned: options.owned === true,
    };
    return `<section class="detail-block value-ladder" aria-label="Guía de modelos y valor">
      <div class="section-head">
        <h3>Guía de modelos y valor</h3>
        <span class="ladder-family">${escapeHtml(ladder.family || "")}</span>
      </div>
      <p class="ladder-intro">Qué variante conviene para jugar, cuál para coleccionar y por qué una cuesta más que otra.</p>
      ${ownedLine(ladder, context.ownedVariantId, context.owned)}
      <div class="ladder-variants">${(ladder.variants || []).map((variant) => variantCard(variant, context)).join("")}</div>
      <p class="ladder-price-note">${escapeHtml(options.priceNote || "")}</p>
      ${pending(ladder, options.pendingAll)}
      ${sources(ladder)}
    </section>`;
  }

  window.ValueLadder = { findLadder, render };
})();
