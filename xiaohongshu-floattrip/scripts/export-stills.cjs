const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const outputDir = path.join(root, "output");
const files = [
  "01-cover.png",
  "02-why-multi-agent.png",
  "03-agent-orchestration.png",
  "04-personal-memory.png",
  "05-editable-itinerary.png",
  "06-open-source.png",
];

const mime = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png" };
const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, "http://127.0.0.1").pathname);
  const requested = pathname === "/" ? "index.html" : pathname.slice(1);
  const resolved = path.resolve(root, requested);
  if (!resolved.startsWith(root) || !fs.existsSync(resolved)) {
    res.writeHead(404); res.end("not found"); return;
  }
  res.setHeader("Content-Type", mime[path.extname(resolved)] || "application/octet-stream");
  fs.createReadStream(resolved).pipe(res);
});

(async () => {
  fs.mkdirSync(outputDir, { recursive: true });
  await new Promise(resolve => server.listen(4179, "127.0.0.1", resolve));
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1440 }, deviceScaleFactor: 1 });
  for (let i = 0; i < files.length; i += 1) {
    await page.goto(`http://127.0.0.1:4179/?poster=${i + 1}`, { waitUntil: "networkidle" });
    await page.screenshot({ path: path.join(outputDir, files[i]), type: "png" });
  }
  await browser.close();
  server.close();
})().catch(error => {
  console.error(error);
  server.close();
  process.exit(1);
});
