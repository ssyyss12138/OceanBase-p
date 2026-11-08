/**
 * Headless browser smoke test (optional, requires Playwright).
 *
 *   npm i --no-save playwright && npx playwright install chromium
 *   node scripts/browser_check.mjs
 *
 * The app must already be running on http://127.0.0.1:5173 and the browser
 * needs network access to the model host (proxy can be set via KB_PROXY).
 */
import { chromium } from "playwright";

const url = process.env.KB_URL || "http://127.0.0.1:5173";
const proxy = process.env.KB_PROXY || "http://127.0.0.1:7890";
const args = ["--no-sandbox", "--disable-gpu", "--proxy-bypass-list=127.0.0.1;localhost"];
if (proxy) args.push(`--proxy-server=${proxy}`);

const browser = await chromium.launch({ channel: "chromium", args });
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const logs = [];
page.on("console", (message) => logs.push(`[console:${message.type()}] ${message.text()}`));
page.on("pageerror", (error) => logs.push(`[pageerror] ${error.message}`));
page.on("requestfailed", (request) => logs.push(`[requestfailed] ${request.url()} ${request.failure()?.errorText}`));

const bodyText = () => page.evaluate(() => document.body.innerText);

try {
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(
    () => document.body.innerText.includes("模型就绪") || document.body.innerText.includes("模型加载失败"),
    null,
    { timeout: 300000 },
  );
  const afterLoad = await bodyText();
  if (afterLoad.includes("模型加载失败")) {
    throw new Error(`model failed to load\n${afterLoad.slice(0, 1500)}\n${logs.slice(-20).join("\n")}`);
  }

  await page.getByRole("button", { name: "导入示例语料" }).click();
  await page.waitForFunction(
    () => document.body.innerText.includes("导入完成") || document.body.innerText.includes("导入失败"),
    null,
    { timeout: 300000 },
  );

  await page.getByPlaceholder("输入问题，例如：混合检索怎么融合两路结果？").fill("混合检索怎么融合全文和向量结果");
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await page.waitForFunction(
    () => document.body.innerText.includes("检索完成") || document.body.innerText.includes("检索失败"),
    null,
    { timeout: 120000 },
  );

  await page.getByRole("button", { name: "运行基准测试", exact: true }).click();
  await page.waitForFunction(
    () => document.body.innerText.includes("基准测试完成") || document.body.innerText.includes("基准测试失败"),
    null,
    { timeout: 300000 },
  );

  const finalText = await bodyText();
  console.log("=== PAGE TEXT ===");
  console.log(finalText.slice(0, 4000));
  console.log("=== BROWSER LOGS ===");
  console.log(logs.slice(-40).join("\n"));
} catch (error) {
  console.error("SMOKE FAILED:", error.message);
  console.error("=== PAGE TEXT ===");
  console.error((await bodyText()).slice(0, 2000));
  console.error("=== BROWSER LOGS ===");
  console.error(logs.slice(-40).join("\n"));
  process.exitCode = 1;
} finally {
  await browser.close();
}
