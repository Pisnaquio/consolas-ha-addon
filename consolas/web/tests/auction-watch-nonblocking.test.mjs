import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

for (const file of ["app.js", "console.js"]) {
  test(`${file} does not block the main collection render on Auction Watch`, async () => {
    const source = await readFile(new URL(`../${file}`, import.meta.url), "utf8");

    assert.doesNotMatch(source, /await\s+loadAuctionWatchSnapshot\s*\(/);
    assert.match(source, /const auctionWatchRefresh = loadAuctionWatchSnapshot\(\)\.catch/);
    assert.match(source, /auctionWatchRefresh\.then\(\(\) =>/);
  });
}

test("all Auction Watch consumers use the same cache-busted repository asset", async () => {
  const files = [
    "index.html",
    "opportunities.html",
    "auction-watch-action.html",
    "console.html",
    "console-games.html",
    "console-accessories.html",
    "console-studio.html"
  ];

  for (const file of files) {
    const source = await readFile(new URL(`../${file}`, import.meta.url), "utf8");
    assert.match(source, /auction-watch-repository\.js\?v=20260827a/);
  }
});
