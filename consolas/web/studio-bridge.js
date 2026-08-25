(() => {
  const pathname = window.location.pathname || "";
  const isStudioIndex = pathname.endsWith("/index-studio.html") || pathname.endsWith("index-studio.html");
  const isStudioDatabase =
    pathname.endsWith("/database-studio.html") || pathname.endsWith("database-studio.html");
  const isStudioConsole =
    pathname.endsWith("/console-studio.html") || pathname.endsWith("console-studio.html");

  function rewriteLinks() {
    if (isStudioIndex) {
      document.querySelectorAll('a[href="./database.html"]').forEach((a) => {
        a.href = "./database-studio.html";
      });
    }
    if (isStudioDatabase) {
      document.querySelectorAll('a[href="./index.html"]').forEach((a) => {
        a.href = "./index-studio.html";
      });
    }
    if (isStudioConsole) {
      document.querySelectorAll('a[href="./index.html"]').forEach((a) => {
        a.href = "./index-studio.html";
      });
      document.querySelectorAll('a[href="./database.html"]').forEach((a) => {
        a.href = "./database-studio.html";
      });
    }
  }

  const observer = new MutationObserver(() => rewriteLinks());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  rewriteLinks();
})();
