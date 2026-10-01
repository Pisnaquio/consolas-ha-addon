import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const helperSource = await readFile(new URL("../ha-deep-link.js", import.meta.url), "utf8");

function loadHelper({ parentPathname = null, parentThrows = false } = {}) {
  const messages = [];
  const replacements = [];
  const window = {
    location: {
      origin: "https://ha.example.test",
      replace(url) {
        replacements.push(url);
      }
    }
  };
  if (parentPathname !== null || parentThrows) {
    window.parent = {
      get location() {
        if (parentThrows) throw new Error("SecurityError");
        return { pathname: parentPathname };
      },
      postMessage(message, targetOrigin) {
        messages.push({ message, targetOrigin });
      }
    };
  } else {
    window.parent = window;
  }
  vm.runInContext(helperSource, vm.createContext({ window }), { filename: "ha-deep-link.js" });
  return { api: window.ConsolasHaDeepLink, messages, replacements };
}

test("parses the discard route from the Home Assistant panel path", () => {
  const { api } = loadHelper();
  assert.deepEqual(
    { ...api.parseDiscardRoute("/d522fbd3_consolas/auction-watch/discard/Remotes/7919%3A58") },
    { panelPath: "/d522fbd3_consolas", key: "remotes:7919:58" }
  );
  assert.equal(api.parseDiscardRoute("/d522fbd3_consolas"), null);
  assert.equal(api.parseDiscardRoute("/d522fbd3_consolas/auction-watch/discard/remotes/"), null);
  assert.equal(api.parseDiscardRoute("/d522fbd3_consolas/auction-watch/discard/%E0%A4%A/1"), null);
});

test("opens the focused opportunity and resets the panel route", () => {
  const { messages, replacements } = loadHelper({
    parentPathname: "/d522fbd3_consolas/auction-watch/discard/remotes/7919%3A58"
  });
  assert.deepEqual(replacements, ["./opportunities.html?focus=remotes%3A7919%3A58"]);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].targetOrigin, "https://ha.example.test");
  assert.deepEqual(
    { ...messages[0].message, options: { ...messages[0].message.options } },
    { type: "home-assistant/navigate", path: "/d522fbd3_consolas", options: { replace: true } }
  );
});

test("does nothing outside a panel link or a same-origin frame", () => {
  assert.deepEqual(loadHelper().replacements, []);
  assert.deepEqual(loadHelper({ parentPathname: "/d522fbd3_consolas" }).replacements, []);
  assert.deepEqual(loadHelper({ parentThrows: true }).replacements, []);
});
