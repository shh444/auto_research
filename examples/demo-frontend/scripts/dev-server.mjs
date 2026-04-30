import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const args = new Set(process.argv.slice(2));
const portArg = process.argv.findIndex((arg) => arg === "--port");
const hostArg = process.argv.findIndex((arg) => arg === "--host");
const port = portArg >= 0 ? Number(process.argv[portArg + 1]) : 3000;
const host = hostArg >= 0 ? process.argv[hostArg + 1] : "127.0.0.1";

const types = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host}`);
    let pathname = decodeURIComponent(url.pathname);
    if (pathname === "/" || args.has("--spa")) pathname = "/index.html";
    const fullPath = path.normalize(path.join(root, pathname));
    if (!fullPath.startsWith(root)) {
      res.writeHead(403);
      res.end("Forbidden");
      return;
    }
    const data = await fs.readFile(fullPath);
    res.writeHead(200, {
      "Content-Type": types[path.extname(fullPath)] || "application/octet-stream",
    });
    res.end(data);
  } catch {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Not found");
  }
});

server.listen(port, host, () => {
  console.log(`Demo frontend running at http://${host}:${port}`);
});
