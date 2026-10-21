// Click-through test of the page. Needs a running server and Playwright:
//   NODE_PATH=<dir with playwright> node tests/ui_test.js http://127.0.0.1:8765
const { chromium } = require("playwright");
const path = require("path");
const assert = require("assert");
const URL = process.argv[2] || "http://127.0.0.1:8765";
const SHOTS = path.join(__dirname, "..", "screenshots");

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
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
    const tile = (name) => page.click(`.tiles button:has-text("${name}")`);
    const home = () => page.click("#home");
    // nothing off-screen and no button with clipped text
    const fits = async (where) => {
      const bad = await page.evaluate(() => [...document.querySelectorAll("#app button, #msg")].filter((e) => {
        const r = e.getBoundingClientRect();
        if (!r.width) return false;
        return r.right > innerWidth + 1 || r.bottom > innerHeight + 1 || e.scrollHeight > e.clientHeight + 2;
      }).map((e) => e.textContent.trim().slice(0, 30)));
      assert.deepStrictEqual(bad, [], `${w}x${h} ${where}: clipped or off-screen`);
    };

    await fits("home");
    await shot("1-home");

    // keyboard tile: type, prediction, physical keys, Enter = SPEAK
    await tile("Keyboard");
    await page.click('.keyboard button:text-is("I")');
    await page.click('.keyboard button:text-is("space")');
    await page.click('.keyboard button:text-is("N")');
    await page.click('.keyboard button:text-is("E")');
    const sugg = await page.$$eval("#suggest button", (b) => b.map((x) => x.textContent));
    assert(sugg.length >= 4 && sugg.every((s) => s.toLowerCase().startsWith("ne")), "prediction: " + sugg);
    await fits("keyboard");
    await shot("2-keyboard");
    await page.click('#suggest button:text-is("need")');
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
    await page.click('.phrases button:text-is("I\'m thirsty")');
    await said("thirsty");

    // Hospital Staff: paging
    await home();
    await tile("Hospital Staff");
    assert.strictEqual(await page.locator(".phrases button").count(), 9);
    await page.click(".head .more");
    await fits("staff p2");
    await shot("4-staff-page2");
    await page.click('.phrases button:text-is("I need suction")');
    await said("suction");

    // pain flow
    await home();
    await tile("Pain");
    await fits("pain");
    await shot("5-pain");
    await page.click('.pain button:text-is("7")');
    await fits("pain where");
    await shot("6-pain-where");
    await page.click('.pain button:text-is("Right leg")');
    await said("My pain is 7 out of 10, in my right leg.");
    await page.click('.pain button:text-is("3")');
    await page.click('.pain button:text-is("Just say the number")');
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
    await page.click('.hrow .say:text-is("My pain is 3 out of 10.")');
    await said("My pain is 3 out of 10.");
    await page.click('.hrow:has(.say:text-is("My pain is 7 out of 10, in my right leg.")) .star');
    await shot("8-star");
    await page.click('#modal button:text-is("Feelings")');

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
    await shot("9-settings");
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
    await page.click(`.phrases button:text-is("${name}")`);
    await said(name);
    await home();
    await tile("Feelings");
    await page.click(".head .more");
    assert(await page.isVisible('.phrases button:text-is("My pain is 7 out of 10, in my right leg.")'), "starred phrase not kept");
    // half-typed message survives reload
    await page.keyboard.type("half typ");
    await page.waitForTimeout(1800);
    await page.reload();
    await page.waitForSelector(".tiles button");
    assert.match(await page.textContent("#msg"), /half typ/);
    await page.click("#clear");
    await page.evaluate(async () => { D.settings.side = "left"; applySettings(); await saveNow(); });

    assert(speaks.length >= 10 && speaks.every((s) => s === 200), "speak responses: " + speaks);
    console.log(`${w}x${h}: OK (${speaks.length} /speak calls, all 200)`);
    await page.close();
  }
  assert.deepStrictEqual(errors, [], "page errors");
  await browser.close();
  console.log("UI test passed");
})().catch((e) => { console.error("UI test FAILED:", e.message); process.exit(1); });
