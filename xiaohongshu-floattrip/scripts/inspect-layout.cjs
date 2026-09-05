const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const mime = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png" };
const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, "http://127.0.0.1").pathname);
  const requested = pathname === "/" ? "index.html" : pathname.slice(1);
  const resolved = path.resolve(root, requested);
  if (!resolved.startsWith(root) || !fs.existsSync(resolved)) { res.writeHead(404); res.end(); return; }
  res.setHeader("Content-Type", mime[path.extname(resolved)] || "application/octet-stream");
  fs.createReadStream(resolved).pipe(res);
});

(async () => {
  await new Promise(resolve => server.listen(4180, "127.0.0.1", resolve));
  const browser = await chromium.launch({ headless: true, executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1440 } });
  const findings = [];
  for (let i = 1; i <= 6; i += 1) {
    await page.goto(`http://127.0.0.1:4180/?poster=${i}`, { waitUntil: "networkidle" });
    const result = await page.evaluate(() => {
      const canvas = { left: 0, top: 0, right: 1080, bottom: 1440 };
      return [...document.querySelectorAll("body *")].flatMap(el => {
        if (!(el instanceof HTMLElement || el instanceof SVGElement)) return [];
        const style = getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return [];
        if (!el.textContent?.trim() && el.tagName !== "IMG") return [];
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) return [];
        const overflow = r.left < canvas.left - 1 || r.top < canvas.top - 1 || r.right > canvas.right + 1 || r.bottom > canvas.bottom + 1;
        const ownText = [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent.trim()).join(" ");
        return overflow && (ownText || el.tagName === "IMG") ? [{ tag: el.tagName, cls: el.className?.baseVal || el.className || "", text: ownText.slice(0, 60), rect: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)] }] : [];
      });
    });
    if (result.length) findings.push({ poster: i, issues: result });
  }
  await browser.close(); server.close();
  if (findings.length) { console.error(JSON.stringify(findings, null, 2)); process.exit(1); }
  console.log("Layout check passed: no visible text or image exceeds the 1080x1440 canvas.");
})().catch(error => { console.error(error); server.close(); process.exit(1); });
