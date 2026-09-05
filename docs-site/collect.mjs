// collect.mjs — assemble Quartz's content/ from every *.md in the repo.
//
//   node collect.mjs --repo .. --out ../.quartz-engine/content --inject-frontmatter
//
// Enumerates tracked markdown with `git ls-files` (so .gitignore already skips
// node_modules/, build output, etc.), filters through the root .documentignore,
// mirrors survivors into <out>/<same relative path> (folder structure preserved),
// and injects frontmatter where missing. Never mutates repo files.

import { execSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join, basename } from "node:path";
import ignore from "ignore";

const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i > -1 ? process.argv[i + 1] : d;
};
const REPO = arg("--repo", ".");
const OUT = arg("--out", "content");
const INJECT = process.argv.includes("--inject-frontmatter");

// 1. Enumerate tracked markdown (respects .gitignore automatically).
const files = execSync(`git -C "${REPO}" ls-files "*.md"`, { encoding: "utf8" })
  .split("\n")
  .map((s) => s.trim())
  .filter(Boolean);

// 2. Exclusion matcher: .documentignore + always-exclude the site's own machinery.
const ig = ignore().add(["docs-site/", ".quartz-engine/", "public/", "content/"]);
const difPath = join(REPO, ".documentignore");
if (existsSync(difPath)) ig.add(readFileSync(difPath, "utf8"));

const shown = files.filter((f) => !ig.ignores(f));
const hidden = files.filter((f) => ig.ignores(f));

// 3. Mirror (structure-preserving) + inject frontmatter where missing.
for (const rel of shown) {
  let text = readFileSync(join(REPO, rel), "utf8");
  if (INJECT && !text.startsWith("---")) {
    const h1 = (text.match(/^#\s+(.+)$/m) || [])[1];
    const title = (h1 || basename(rel).replace(/\.md$/, "")).trim();
    const topDir = rel.includes("/") ? rel.split("/")[0] : "root";
    const fm =
      `---\n` +
      `title: ${JSON.stringify(title)}\n` +
      `source: ${rel}\n` +
      `tags: [${topDir}]\n` +
      `---\n\n`;
    text = fm + text;
  }
  const dest = join(OUT, rel);
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, text);
}

// 4. Ensure a landing page at the site root (only if the repo has none).
const indexDest = join(OUT, "index.md");
if (!existsSync(indexDest)) {
  const topDirs = [
    ...new Set(shown.filter((f) => f.includes("/")).map((f) => f.split("/")[0])),
  ].sort();
  const body =
    `---\ntitle: LEO Customer 360 — Documentation\n---\n\n` +
    `Auto-generated index of ${shown.length} documents across the repository.\n\n` +
    `## Sections\n\n` +
    topDirs.map((d) => `- [${d}/](${d}/)`).join("\n") +
    "\n";
  writeFileSync(indexDest, body);
}

console.log(`Collected ${shown.length}/${files.length} markdown files → ${OUT}`);
if (hidden.length) {
  console.log(`Excluded ${hidden.length} by .documentignore:`);
  for (const f of hidden) console.log(`  - ${f}`);
}
