// Click-through test of the page. Needs a running server and Playwright:
//   NODE_PATH=<dir with playwright> node tests/ui_test.js http://127.0.0.1:8765
const { chromium } = require("playwright");
const path = require("path");
const assert = require("assert");
const URL = process.argv[2] || "http://127.0.0.1:8765";
const SHOTS = path.join(__dirname, "..", "screenshots");

(async () => {
  // show scrollbars like the real kiosk (headless hides them by default)
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"], ignoreDefaultArgs: ["--hide-scrollbars"] });
  const errors = [];
  for (const [w, h] of [[1280, 853], [1920, 1280]]) {
    const page = await browser.newPage({ viewport: { width: w, height: h }, hasTouch: true });
    page.on("pageerror", (e) => errors.push(String(e)));
    const speaks = [];
    page.on("response", (r) => { if (r.url().endsWith("/speak")) speaks.push(r.status()); });
    await page.goto(URL);
    await page.waitForSelector(".tiles button");
    const shot = (n) => page.screenshot({ path: path.join(SHOTS, `${w}x${h}-${n}.png`) });
    const said = (t) => page.waitForFunction((t) => document.querySelector("#lastsaid").textContent.includes(t), t, { timeout: 8000 });
    // a freshly drawn screen ignores taps for 400 ms (double-tap guard), so wait like a person would
    const pc = async (sel) => { await page.waitForTimeout(450); await page.click(sel); };
    const tile = (name) => pc(`.tiles button:has-text("${name}")`);
    const home = () => page.click("#home");
    // nothing off-screen and no button with clipped text
    const fits = async (where) => {
      const bad = await page.evaluate(() => [...document.querySelectorAll("#app button, #msg")].filter((e) => {
        const r = e.getBoundingClientRect();
        if (!r.width) return false;
        if (e.closest(".scroll")) return e.scrollHeight > e.clientHeight + 2; // rows below the fold are fine in a list
        return r.right > innerWidth + 1 || r.bottom > innerHeight + 1 || e.scrollHeight > e.clientHeight + 2;
      }).map((e) => e.textContent.trim().slice(0, 30)));
      assert.deepStrictEqual(bad, [], `${w}x${h} ${where}: clipped or off-screen`);
    };

    await fits("home");
    const tilesNow = await page.$$eval(".tiles button", (b) => b.map((x) => x.textContent));
    assert.strictEqual(tilesNow[0], "I love you");
    assert.strictEqual(tilesNow.length % 4, 0, "home grid has a hole: " + tilesNow.length);
    await tile("I love you");
    await said("I love you");
    assert(await page.isVisible(".tiles"), "love tile left the home screen");
    await page.click("#holdon");
    await said("Hold on. Give me a minute, I'm typing.");
    const col = await page.$$eval("#controls button", (b) => b.map((x) => [x.id, x.getBoundingClientRect().height]));
    const hgt = Object.fromEntries(col);
    assert(hgt.yes >= Math.max(hgt.notsure, hgt.holdon, hgt.home) && hgt.no >= hgt.holdon, "YES/NO must stay biggest: " + JSON.stringify(hgt));
    await shot("1-home");

    // keyboard tile: type, prediction, physical keys, Enter = SPEAK
    await tile("Keyboard");
    await pc('.keyboard button:text-is("I")');
    await pc('.keyboard button:text-is("space")');
    await pc('.keyboard button:text-is("N")');
    await pc('.keyboard button:text-is("E")');
    const sugg = await page.$$eval("#suggest button", (b) => b.map((x) => x.textContent));
    assert(sugg.length >= 4 && sugg.every((s) => s.toLowerCase().startsWith("ne")), "prediction: " + sugg);
    await fits("keyboard");
    await shot("2-keyboard");
    await pc('#suggest button:text-is("need")');
    await page.keyboard.type("water");
    assert.match(await page.textContent("#msg"), /^i need water$/);
    await page.keyboard.press("Enter");
    await said("i need water");
    await page.waitForTimeout(800);
    await page.click("#clear");
    assert.strictEqual(await page.textContent("#clear"), "UNDO");
    // learned bigram: after "i " the next-word guess offers "need"; physical typing from HOME opens the keyboard
    await home();
    await page.keyboard.type("i ");
    assert(await page.isVisible("#suggest"), "typing did not open keyboard");
    const next = await page.$$eval("#suggest button", (b) => b.map((x) => x.textContent));
    assert(next.includes("need"), "next-word prediction: " + next);
    await page.click("#clear");

    // phrase tile: one tap speaks
    await home();
    await tile("Needs");
    await fits("needs");
    await shot("3-needs");
    await pc('.phrases button:text-is("I\'m thirsty")');
    await said("thirsty");

    // Hospital Staff: one scrolling list, 3 across, the third row cut off to show there is more
    await home();
    await tile("Hospital Staff");
    assert.strictEqual(await page.locator(".phrases button").count(), 21);
    const rows = await page.evaluate(() => { const g = document.querySelector(".phrases"), b = g.querySelector("button");
      return (g.clientHeight + parseFloat(getComputedStyle(g).rowGap)) / (b.offsetHeight + parseFloat(getComputedStyle(g).rowGap)); });
    assert(rows > 2.3 && rows < 2.7, "visible rows: " + rows);
    await fits("staff");
    // a finger swipe scrolls and says nothing
    const before0 = await page.textContent("#lastsaid");
    await page.waitForTimeout(450);
    const lb = await page.locator(".phrases").boundingBox();
    const cdp = await page.context().newCDPSession(page);
    const tx = lb.x + lb.width / 2, ty = lb.y + lb.height * 0.85;
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: tx, y: ty }] });
    for (let i = 1; i <= 12; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: tx, y: ty - 25 * i }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await page.waitForTimeout(800);
    assert(await page.$eval(".phrases", (g) => g.scrollTop) > 100, "swipe did not scroll");
    assert.strictEqual(await page.textContent("#lastsaid"), before0, "a swipe spoke");
    // a shaky press that slides 15 px is a scroll, not a press; one that slides 4 px is a press
    const sb = await page.locator('.phrases button:text-is("I need my glasses")');
    await sb.scrollIntoViewIfNeeded();
    const bb = await sb.boundingBox();
    await page.mouse.move(bb.x + 30, bb.y + 30); await page.mouse.down(); await page.mouse.move(bb.x + 30, bb.y + 45); await page.mouse.up();
    await page.waitForTimeout(300);
    assert.strictEqual(await page.textContent("#lastsaid"), before0, "a 15 px slide spoke");
    await shot("4-staff-scrolled");
    await page.mouse.move(bb.x + 30, bb.y + 30); await page.mouse.down(); await page.mouse.move(bb.x + 34, bb.y + 30); await page.mouse.up();
    await said("my glasses");
    // far down the list: scroll to the last phrase and tap it
    await page.locator('.phrases button:text-is("Please explain what you are doing before you do it.")').scrollIntoViewIfNeeded();
    await pc('.phrases button:text-is("Please explain what you are doing before you do it.")');
    await said("before you do it");

    // pain flow
    await home();
    await tile("Pain");
    await fits("pain");
    await shot("5-pain");
    await pc('.pain button:text-is("7")');
    await fits("pain where");
    await shot("6-pain-where");
    await pc('.pain button:text-is("Right leg")');
    await said("My pain is 7 out of 10, in my right leg.");
    await pc('.pain button:text-is("3")');
    await pc('.pain button:text-is("Just say the number")');
    await said("My pain is 3 out of 10.");

    // YES / NO / NOT SURE
    await page.click("#yes"); await said("Yes"); await page.waitForTimeout(750);
    await page.click("#no"); await said("No"); await page.waitForTimeout(750);
    await page.click("#notsure"); await said("not sure");

    // history: repeat + star into Feelings
    await home();
    await tile("History");
    await shot("7-history");
    assert.strictEqual(await page.textContent(".hrow .say"), "I'm not sure, ask me another way.");
    await page.waitForTimeout(750);
    await pc('.hrow .say:text-is("My pain is 3 out of 10.")');
    await said("My pain is 3 out of 10.");
    await pc('.hrow:has(.say:text-is("My pain is 7 out of 10, in my right leg.")) .star');
    await shot("8-star");
    await page.click('#modal button:text-is("Feelings")');

    // star picker must leave YES/NO/HELP reachable
    await pc('.hrow .star');
    const modalBox = await page.locator("#modal").boundingBox();
    const helpBox = await page.locator("#help").boundingBox();
    assert(modalBox.x >= helpBox.x + helpBox.width, "star picker covers HELP");
    await page.click("#yes"); // still works with the picker open
    await page.click('#modal button:text-is("Cancel")');

    // double tap guard: two fast taps on pain "7" must not also pick a body part
    await page.click("#home");
    await tile("Pain");
    await page.waitForTimeout(450);
    const saidBefore = await page.textContent("#lastsaid");
    const seven = await page.locator('.pain button:text-is("7")').boundingBox();
    await page.mouse.click(seven.x + 20, seven.y + 20);
    await page.mouse.click(seven.x + 20, seven.y + 20);
    await page.waitForTimeout(300);
    assert.match(await page.textContent(".head .title"), /Where\?/);
    assert.strictEqual(await page.textContent("#lastsaid"), saidBefore, "double tap picked a body part");
    await page.click("#home");

    // HELP: short tap does nothing, 1 s hold fires
    const before = await page.textContent("#lastsaid");
    const hb = await page.locator("#help").boundingBox();
    await page.mouse.move(hb.x + 20, hb.y + 20);
    await page.mouse.down(); await page.waitForTimeout(300); await page.mouse.up();
    await page.waitForTimeout(500);
    assert.strictEqual(await page.textContent("#lastsaid"), before, "short tap fired HELP");
    await page.mouse.down(); await page.waitForTimeout(1200); await page.mouse.up();
    await said("I need help");

    // settings: 1 s does nothing, 3 s hold opens
    const gb = await page.locator("#gear").boundingBox();
    await page.mouse.move(gb.x + 10, gb.y + 10);
    await page.mouse.down(); await page.waitForTimeout(1000); await page.mouse.up();
    assert(await page.isHidden("#settings"), "settings opened too early");
    await page.mouse.down(); await page.waitForTimeout(3300); await page.mouse.up();
    await page.waitForSelector("#settings:not([hidden])");
    await page.waitForSelector('#sbody button.on:text-is("Ryan")'); // friendly voice names, Ryan until the family picks
    await shot("9-settings");
    // updates (server answers faked here; the real flow is in test_server.py): offer, then a plain failure message
    const fake = (status, obj) => (r) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(obj) });
    await page.route("**/update/check", fake(200, { current: "a".repeat(40), latest: "b".repeat(40), available: true, can_undo: false }));
    await page.route("**/update/install", fake(502, { error: "Could not reach GitHub (no internet?). Nothing was changed. Try again later." }));
    await page.click('#sbody button:text-is("Check for updates")');
    await page.waitForSelector("#sbody button.install");
    assert.strictEqual(await page.textContent("#ustat"), "Update available.");
    await page.click("#sbody button.install");
    await page.waitForFunction(() => document.querySelector("#ustat").textContent.includes("Nothing was changed"));
    await page.unroute("**/update/check"); await page.unroute("**/update/install");
    const name = w === 1280 ? "Mike" : "Anna";
    await page.click('#sbody input[placeholder="Type a name"]');
    for (const ch of name) await page.click(`#skeys button:text-is("${ch.toUpperCase()}")`);
    await page.click('#sbody button:text-is("Add name")');
    await page.click('#sbody button:text-is("Right side")');
    await page.click('#sbody button:text-is("Done")');
    await page.waitForTimeout(1200); // let the save land
    await shot("10-right-side");

    // reload: everything persisted
    await page.reload();
    await page.waitForSelector(".tiles button");
    assert(await page.evaluate(() => document.body.classList.contains("side-right")), "side setting not kept");
    await tile("People");
    await pc(`.phrases button:text-is("${name}")`);
    await said(name);
    await home();
    await tile("Feelings");
    const starred = page.locator('.phrases button:text-is("My pain is 7 out of 10, in my right leg.")');
    await starred.scrollIntoViewIfNeeded();
    assert(await starred.isVisible(), "starred phrase not kept");
    // half-typed message survives reload
    await page.keyboard.type("half typ");
    await page.waitForTimeout(1800);
    await page.reload();
    await page.waitForSelector(".tiles button");
    assert.match(await page.textContent("#msg"), /half typ/);
    await page.click("#clear");
    await page.evaluate(async () => { D.settings.side = "left"; applySettings(); await saveNow(); });

    // long message scrolls instead of hiding the start
    await page.evaluate(() => setMsg("one two three four five six seven eight nine ten ".repeat(8)));
    assert(await page.evaluate(() => { const m = document.querySelector("#msg"); return m.scrollHeight > m.clientHeight && m.scrollTop > 0; }), "long message does not scroll");
    await page.click("#clear");

    assert(speaks.length >= 10 && speaks.every((s) => s === 200), "speak responses: " + speaks);
    console.log(`${w}x${h}: OK (${speaks.length} /speak calls, all 200)`);
    await page.close();
  }
  // server down: YES and HELP still speak (browser voice); no voice at all: text shown huge
  {
    const page = await browser.newPage({ viewport: { width: 1280, height: 853 }, hasTouch: true });
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.route("**/data", (r) => r.abort());
    await page.route("**/speak", (r) => r.fulfill({ status: 503, body: "{}" }));
    await page.addInitScript(() => {
      window.__spoken = [];
      speechSynthesis.getVoices = () => [{ name: "fake" }];
      speechSynthesis.speak = (u) => window.__spoken.push(u.text);
    });
    await page.goto(URL);
    await page.click("#holdon"); // wired before the data loads, like YES/NO
    await page.waitForTimeout(750);
    await page.click("#yes");
    const hb = await page.locator("#help").boundingBox();
    await page.mouse.move(hb.x + 20, hb.y + 20);
    await page.mouse.down(); await page.waitForTimeout(1200); await page.mouse.up();
    await page.waitForFunction(() => window.__spoken.includes("I need help"), null, { timeout: 8000 });
    assert.deepStrictEqual(await page.evaluate(() => window.__spoken), ["Hold on. Give me a minute, I'm typing.", "Yes", "I need help"]);
    await page.evaluate(() => { speechSynthesis.getVoices = () => []; });
    await page.waitForTimeout(750);
    await page.click("#no");
    // the stalled fake voice already put "Yes" up big after 3 s; wait for "No" to replace it (was a race)
    await page.waitForFunction(() => { const b = document.querySelector("#modal:not([hidden]) .bigtext"); return b && b.textContent === "No"; });
    assert.strictEqual(await page.textContent("#modal .bigtext"), "No");
    await page.screenshot({ path: path.join(SHOTS, "1280x853-11-no-voice.png") });
    await page.click('#modal button:text-is("OK")');
    console.log("offline/no-voice: OK");
    await page.close();
  }
  assert.deepStrictEqual(errors, [], "page errors");
  await browser.close();
  console.log("UI test passed");
})().catch((e) => { console.error("UI test FAILED:", e.message); process.exit(1); });
