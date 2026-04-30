import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const files = ["index.html", "src/app.js", "src/styles.css"];
const failures = [];

for (const file of files) {
  const text = await fs.readFile(path.join(process.cwd(), file), "utf8");
  if (text.includes("TODO_BROKEN")) failures.push(`${file}: contains TODO_BROKEN`);
  if (/\t/.test(text)) failures.push(`${file}: contains tab characters`);
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log("lint ok");
