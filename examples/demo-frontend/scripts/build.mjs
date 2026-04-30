import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const dist = path.join(root, "dist");

await fs.rm(dist, { recursive: true, force: true });
await fs.mkdir(path.join(dist, "src"), { recursive: true });

for (const file of ["index.html", "src/app.js", "src/styles.css"]) {
  await fs.copyFile(path.join(root, file), path.join(dist, file));
}

console.log("build ok");
