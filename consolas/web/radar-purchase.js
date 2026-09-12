(() => {
  /**
   * "Registrar compra" — la única acción del radar que escribe la colección
   * (PRD §9, §24), y sólo mediante `CollectionRepository`/`DataStore`, nunca
   * directo a `fetch`.
   *
   * Dos pasos, deliberadamente separados:
   *  1. El servidor deja evidencia (`RadarRepository.recordPurchase`): cuenta
   *     para el presupuesto y queda en el historial aunque el paso 2 falle.
   *  2. Acá se escribe la colección real, si la entidad alcanza para saber
   *     exactamente qué escribir.
   *
   * Una consola es su propia entidad. Un juego, no: el mismo id existe en
   * varias plataformas, así que su identidad real es el par
   * {consoleId, gameId} — el mismo `catalogRef` que usa el Franchise Tracker.
   * Sin la consola no se escribe nada: no se adivina la plataforma.
   *
   * Los accesorios todavía no: `persistAccessoryEntityState` existe, pero un
   * accesorio del radar no tiene id de catálogo con el que casarlo, y crear
   * uno manual desde acá inventaría entradas que después nadie reconoce.
   */

  const GAMES_URL = "./data/console-games.json";

  function repo() {
    const value = window.CollectionRepository;
    if (!value) throw new Error("CollectionRepository no está disponible.");
    return value;
  }

  /**
   * Qué puede escribir esta versión. Es la única fuente de verdad: la UI
   * pregunta acá antes de ofrecer el botón, y `registerPurchase` vuelve a
   * preguntar antes de escribir.
   */
  function canWriteCollection(entity) {
    const { entityType, entityConsoleId } = entity || {};
    if (entityType === "console") return true;
    if (entityType === "game") return Boolean(entityConsoleId);
    return false;
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

  async function fetchBaseGames(consoleId) {
    const response = await fetch(GAMES_URL, { cache: "no-store" });
    if (!response.ok) throw new Error("No se pudo cargar el catálogo de juegos.");
    const payload = await response.json();
    return payload.byConsole?.[consoleId]?.juegosCatalogo || [];
  }

  /**
   * Exactamente el mismo patch que escribe la ficha de la consola al elegir
   * "Físico" en un juego, para que el radar no invente una forma distinta de
   * decir lo mismo. El precio pagado no va acá: vive en el historial de
   * compras del servidor, que es lo que alimenta el presupuesto. Ninguna
   * pantalla de juegos lo lee todavía, y escribirlo sería un campo muerto.
   */
  async function writeGameOwnership({ entityId, entityConsoleId, entityName }) {
    const baseGames = await fetchBaseGames(entityConsoleId);
    const patch = { ownershipType: "physical", loTengo: true, keepInWishlist: false };

    // Normalmente el juego ya existe: el Master arma estas búsquedas a partir
    // de tu propia wishlist. Si no está, `persistGamePatch` lo crea como
    // manual — y sin nombre quedaría una entrada anónima en la biblioteca.
    // El nombre sólo se manda al crear: pisarlo en un juego que ya existe
    // reemplazaría el nombre real del catálogo por el de la búsqueda.
    const existing = repo().getGamesForConsole({ [entityConsoleId]: baseGames }, entityConsoleId);
    const found = existing.some((game) => String(game?.id) === String(entityId));
    if (!found && entityName) patch.nombre = entityName;

    repo().persistGamePatch(entityConsoleId, entityId, patch, baseGames);
  }

  /**
   * @param {{listingId:string, entityType:string, entityId:string, entityConsoleId?:string, priceAmount:number, currency?:string, purchasedAt?:string}} purchase
   * @returns {Promise<{ok:true, purchase:object, decision:object, collectionWritten:boolean}>}
   */
  async function registerPurchase(purchase) {
    const repository = window.RadarRepository;
    if (!repository) throw new Error("RadarRepository no está disponible.");
    const {
      listingId, entityType, entityId, entityConsoleId, entityName, priceAmount, currency, purchasedAt
    } = purchase || {};

    const result = await repository.recordPurchase({
      listingId,
      entityType,
      entityId,
      entityConsoleId,
      priceAmount,
      currency,
      purchasedAt
    });

    let collectionWritten = false;
    if (entityId && canWriteCollection({ entityType, entityConsoleId })) {
      if (entityType === "console") writeConsoleOwnership({ entityId, priceAmount, currency });
      else await writeGameOwnership({ entityId, entityConsoleId, entityName });
      collectionWritten = true;
    }

    return { ...result, collectionWritten };
  }

  window.RadarPurchase = { registerPurchase, canWriteCollection };
})();
