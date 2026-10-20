import assert from "node:assert/strict";
import { readFile, readdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { it } from "node:test";
import { build } from "vite";

it("excludes the platform shell and synthetic UC2 chunks from the normal production build", async () => {
  const outDir = await mkdtemp(join(tmpdir(), "nsp-production-isolation-"));
  const chunks = [];
  try {
    await build({
      root: fileURLToPath(new URL("../../", import.meta.url)), logLevel: "silent",
      plugins: [{ name: "assert-uc2-isolation", generateBundle(_options, bundle) {
        for (const item of Object.values(bundle)) if (item.type === "chunk") chunks.push(...Object.keys(item.modules));
      } }],
      build: { outDir, emptyOutDir: false },
    });
    assert.ok(chunks.length > 0);
    assert.ok(!chunks.some((path) => /\/src\/(?:uc2|platform)\//.test(path)), chunks.filter((path) => /\/src\/(?:uc2|platform)\//.test(path)).join("\n"));
    for (const name of await readdir(join(outDir, "assets"))) {
      const text = await readFile(join(outDir, "assets", name), "utf8");
      for (const marker of ["SYNTHETIC DEMO DATA", "synthetic-workflows-v1", "platform-navigation", "usecase=uc2"]) assert.ok(!text.includes(marker), marker);
    }
  } finally {
    await rm(outDir, { recursive: true, force: true });
  }
});