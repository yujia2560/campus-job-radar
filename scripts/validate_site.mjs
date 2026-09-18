import fs from "node:fs";

const file = process.argv[2] || "site/index.html";
const source = fs.readFileSync(file, "utf8");
if (!source.includes("秋招岗位雷达") || !source.includes('id="jobs"')) {
  throw new Error("Dashboard is missing its primary content surface");
}

const jsonMatch = source.match(/<script type="application\/json" id="payload">([\s\S]*?)<\/script>/);
if (!jsonMatch) throw new Error("Embedded dashboard payload is missing");
const payload = JSON.parse(jsonMatch[1]);
if (!Array.isArray(payload.jobs) || !Array.isArray(payload.sources)) {
  throw new Error("Dashboard payload has an invalid shape");
}

const scripts = [...source.matchAll(/<script(?![^>]*application\/json)[^>]*>([\s\S]*?)<\/script>/g)];
for (const [, script] of scripts) {
  // Parsing only: the dashboard code is not executed during validation.
  new Function(script);
}

console.log(JSON.stringify({ file, jobs: payload.jobs.length, sources: payload.sources.length }));
