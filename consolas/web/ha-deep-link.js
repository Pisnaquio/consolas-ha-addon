// Links de los correos de Auction Watch que entran por el panel de Home
// Assistant: <panel>/auction-watch/discard/<fuente>/<lote>.
//
// Ingress siempre carga la raíz del add-on en un iframe del mismo origen, así
// que la ruta pedida sólo existe en la URL del panel (la ventana padre). Se lee
// de ahí, se abre Oportunidades con ese lote enfocado para que el usuario
// confirme el descarte, y se devuelve el panel a su raíz para que volver al
// inicio no repita la redirección.
(() => {
  const DISCARD_ROUTE = /^(.*)\/auction-watch\/discard\/([^/]+)\/([^/]+)\/?$/;

  function decodeSegment(value = "") {
    try {
      return decodeURIComponent(value).trim();
    } catch (_error) {
      return "";
    }
  }

  function parseDiscardRoute(pathname = "") {
    const match = DISCARD_ROUTE.exec(String(pathname || ""));
    if (!match) return null;
    const source = decodeSegment(match[2]).toLowerCase();
    const lotId = decodeSegment(match[3]);
    if (!source || !lotId) return null;
    return { panelPath: match[1] || "/", key: `${source}:${lotId}` };
  }

  function readPanelPathname(win) {
    try {
      if (win.parent && win.parent !== win) return win.parent.location.pathname;
    } catch (_error) {
      // Otro origen: no hay panel de Home Assistant que leer.
    }
    return "";
  }

  function run(win) {
    const route = parseDiscardRoute(readPanelPathname(win));
    if (!route) return false;
    win.parent.postMessage(
      { type: "home-assistant/navigate", path: route.panelPath, options: { replace: true } },
      win.location.origin
    );
    win.location.replace(`./opportunities.html?focus=${encodeURIComponent(route.key)}`);
    return true;
  }

  window.ConsolasHaDeepLink = { parseDiscardRoute, run };
  run(window);
})();
