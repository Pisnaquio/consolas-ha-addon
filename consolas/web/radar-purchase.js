(() => {
  /**
   * "Registrar compra" — la única acción del radar que escribe la colección
   * (PRD §9, §24), y sólo mediante `CollectionRepository`/`DataStore`, nunca
   * directo a `fetch`.
   *
   * Dos pasos, deliberadamente separados:
   *  1. El servidor deja evidencia (`RadarRepository.recordPurchase`): cuenta
   *     para el presupuesto y queda en el historial aunque el paso 2 falle.
   *  2. Acá, si la entidad es una consola, se escribe la colección real.
   *
   * v1 alcanza consolas. Un juego o accesorio no tiene todavía una identidad
   * compuesta {consoleId, gameId} establecida en el resto de la app (ver
   * docs/BACKLOG.md, Franchise Collection Tracker) — inventar un esquema acá
   * se pisaría con ese trabajo. El servidor ya acepta esos entityType para
   * el presupuesto y el historial; falta sólo la escritura de colección,
   * a propósito.
   */

  const WRITABLE_ENTITY_TYPES = new Set(["console"]);

  function canWriteCollection(entityType) {
    return WRITABLE_ENTITY_TYPES.has(entityType);
  }

  /** El mismo par que usa cualquier ficha de consola al marcar "Tengo" + precio pagado. */
  function writeConsoleOwnership({ entityId, priceAmount, currency }) {
    window.DataStore?.updateOverride?.(entityId, { tengo: true, categoria: "coleccion" });
    window.DataStore?.updateDetailEdit?.(entityId, {
      precioPagado: priceAmount,
      monedaPago: currency || "USD",
      formaObtencion: "Collection Radar"
    });
  }

  /**
   * @param {{listingId:string, entityType:string, entityId:string, priceAmount:number, currency?:string, purchasedAt?:string}} purchase
   * @returns {Promise<{ok:true, purchase:object, decision:object, collectionWritten:boolean}>}
   */
  async function registerPurchase(purchase) {
    const repo = window.RadarRepository;
    if (!repo) throw new Error("RadarRepository no está disponible.");
    const { listingId, entityType, entityId, priceAmount, currency, purchasedAt } = purchase || {};

    const result = await repo.recordPurchase({ listingId, entityType, entityId, priceAmount, currency, purchasedAt });

    let collectionWritten = false;
    if (canWriteCollection(entityType) && entityId) {
      writeConsoleOwnership({ entityId, priceAmount, currency });
      collectionWritten = true;
    }

    return { ...result, collectionWritten };
  }

  window.RadarPurchase = { registerPurchase, canWriteCollection };
})();
