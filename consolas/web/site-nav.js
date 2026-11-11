// Shared navigation for the Estante layout: a sticky top bar on wide screens
// and a bottom tab bar on phones. Pages opt in by loading this script; the
// styles live in estante-shell.css.
(() => {
  const ICONS = {
    collection:
      '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"></rect><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"></rect><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"></rect><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"></rect>',
    wishlist: '<path d="M12 20s-7-4.4-7-9.6A4 4 0 0 1 12 8a4 4 0 0 1 7 2.4C19 15.6 12 20 12 20z"></path>',
    radar: '<circle cx="12" cy="12" r="8.5"></circle><circle cx="12" cy="12" r="4"></circle><path d="M12 12l6-6"></path>',
    auctions: '<path d="M14 4l6 6M9 9l6 6M12 12l-8 8M15 3l6 6-2 2-6-6zM9 9l6 6-2 2-6-6z"></path>',
    database: '<ellipse cx="12" cy="6" rx="7.5" ry="2.8"></ellipse><path d="M4.5 6v6c0 1.5 3.4 2.8 7.500 2.800s7.500-1.300 7.500-2.800V6M4.500 12v6c0 1.500 3.400 2.800 7.500 2.800s7.500-1.300 7.500-2.800v-6"></path>'
  };

  // `pages` lists every document that belongs to the section; the first one is
  // the section's own landing page.
  const SECTIONS = [
    {
      id: "collection",
      label: "Colección",
      pages: ["index.html", "console.html", "console-games.html", "console-accessories.html", "collection-control.html"]
    },
    { id: "wishlist", label: "Wishlist", pages: ["nachos-wishlist.html", "franchise-collection.html"] },
    { id: "radar", label: "Radar", pages: ["radar.html", "chasing-games.html"] },
    { id: "auctions", label: "Subastas", pages: ["opportunities.html", "auction-watch-action.html"] },
    { id: "database", label: "Base", longLabel: "Base de consolas", pages: ["database.html"] }
  ];

  function currentPage(pathname = window.location.pathname || "") {
    const file = pathname.split("/").pop() || "";
    return file === "" ? "index.html" : file;
  }

  function findSection(page) {
    return SECTIONS.find((section) => section.pages.includes(page)) || null;
  }

  function linkMarkup(section, activeId, { icon }) {
    const active = section.id === activeId;
    const label = icon ? section.label : section.longLabel || section.label;
    const svg = icon
      ? `<svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[section.id]}</svg>`
      : "";
    return `<a href="./${section.pages[0]}"${active ? ' aria-current="page"' : ""}>${svg}<span>${label}</span></a>`;
  }

  function render() {
    if (!document.body || document.querySelector(".site-nav")) return;
    const page = currentPage();
    const section = findSection(page);
    const activeId = section?.id || "";

    const top = document.createElement("header");
    top.className = "site-nav";
    top.innerHTML = `
      <a class="site-nav__brand" href="./index.html">Consolas</a>
      <nav class="site-nav__links" aria-label="Principal">
        ${SECTIONS.map((item) => linkMarkup(item, activeId, { icon: false })).join("")}
      </nav>`;

    const bottom = document.createElement("nav");
    bottom.className = "site-tabbar";
    bottom.setAttribute("aria-label", "Principal");
    bottom.innerHTML = SECTIONS.map((item) => linkMarkup(item, activeId, { icon: true })).join("");

    document.body.prepend(top);
    document.body.append(bottom);
    document.body.classList.add("has-site-nav");
    // A section's landing page needs no "back to home" link: the bar covers it.
    if (section && section.pages[0] === page) document.body.dataset.navRoot = section.id;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { SECTIONS, currentPage, findSection };
    return;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", render);
  else render();
})();
