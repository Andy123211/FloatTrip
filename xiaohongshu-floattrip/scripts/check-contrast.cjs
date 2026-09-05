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
  await new Promise(resolve => server.listen(4181, "127.0.0.1", resolve));
  const browser = await chromium.launch({ headless: true, executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1440 } });
  const failures = [];
  for (let i = 1; i <= 6; i += 1) {
    await page.goto(`http://127.0.0.1:4181/?poster=${i}`, { waitUntil: "networkidle" });
    const result = await page.evaluate(() => {
      const parse = value => {
        const m = value.match(/[\d.]+/g)?.map(Number) || [0,0,0,1];
        return [m[0], m[1], m[2], m.length > 3 ? m[3] : 1];
      };
      const luminance = rgb => {
        const values = rgb.slice(0,3).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
        return .2126 * values[0] + .7152 * values[1] + .0722 * values[2];
      };
      const ratio = (a,b) => { const l1=luminance(a), l2=luminance(b); return (Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05); };
      const isHidden = el => {
        let node = el;
        while (node) { const s=getComputedStyle(node); if (s.display==="none" || s.visibility==="hidden" || Number(s.opacity)===0) return true; node=node.parentElement; }
        return false;
      };
      const background = el => {
        let node = el;
        while (node) { const bg=parse(getComputedStyle(node).backgroundColor); if (bg[3] >= .85) return bg; node=node.parentElement; }
        return [245,248,250,1];
      };
      return [...document.querySelectorAll("body *")].flatMap(el => {
        if (!(el instanceof HTMLElement)) return [];
        const own=[...el.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent.trim()).join(" ");
        if (!own) return [];
        const s=getComputedStyle(el); if (isHidden(el)) return [];
        const size=parseFloat(s.fontSize), weight=parseInt(s.fontWeight)||400;
        const needed=size>=24 || (size>=19 && weight>=700) ? 3 : 4.5;
        const actual=ratio(parse(s.color),background(el));
        return actual + .01 < needed ? [{ text:own.slice(0,50), ratio:Number(actual.toFixed(2)), needed }] : [];
      });
    });
    if (result.length) failures.push({ poster: i, issues: result });
  }
  await browser.close(); server.close();
  if (failures.length) { console.error(JSON.stringify(failures,null,2)); process.exit(1); }
  console.log("Contrast check passed for all visible text in six posters.");
})().catch(error => { console.error(error); server.close(); process.exit(1); });
