#!/usr/bin/env node
/**
 * check_mermaid.mjs — 校验 Markdown 里的 ```mermaid 代码块能否被 mermaid 解析。
 *
 * 仓库已在 docs/ 与 frontend/ 使用 mermaid（frontend 依赖 mermaid v12）。
 * 但 GitHub 只在渲染时才发现语法错误，本地没有反馈。
 * 这个脚本用 frontend 已安装的 mermaid + jsdom 做离线 parse，
 * 让写文档时立刻知道图画歪了。
 *
 * 用法：
 *   node scripts/check_mermaid.mjs docs/ARCHITECTURE-LAYERS.md
 *   node scripts/check_mermaid.mjs docs/ARCHITECTURE.md docs/wizard-inventory.md
 *
 * 退出码：0 = 全部通过；1 = 有块解析失败。
 *
 * 依赖走 frontend/node_modules —— 脚本从 frontend/ 目录解析包，
 * 所以必须从仓库内运行，且不要单独 npm install。
 */
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const frontendNodeModules = resolve(repoRoot, "frontend", "node_modules");

// 从 frontend/node_modules 解析 jsdom / mermaid，不依赖调用者的 cwd。
const require = createRequire(resolve(frontendNodeModules, "index.js"));
const { JSDOM: Jsdom } = require("jsdom");
const mermaidEntry = require.resolve("mermaid");

const dom = new Jsdom("<!DOCTYPE html><body></body>", { pretendToBeVisual: true });
for (const key of [
  "window", "document", "HTMLElement", "SVGElement", "Element", "Node",
  "getComputedStyle", "DOMPurify", "MutationObserver", "Range",
  "CSSStyleSheet", "StyleSheet", "CSSStyleDeclaration", "NodeList",
  "DOMRect", "XMLSerializer", "SVGSVGElement",
]) {
  if (dom.window[key] !== undefined) {
    try {
      Object.defineProperty(globalThis, key, {
        value: dom.window[key], configurable: true, writable: true,
      });
    } catch { /* 只读全局，忽略 */ }
  }
}
globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
globalThis.getBBox = () => ({ x: 0, y: 0, width: 120, height: 20 });
if (dom.window.SVGElement) dom.window.SVGElement.prototype.getBBox = globalThis.getBBox;

const mermaid = (await import(pathToFileURL(mermaidEntry).href)).default;
mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

const files = process.argv.slice(2);
if (files.length === 0) {
  console.error("用法: node scripts/check_mermaid.mjs <file.md> [...]");
  process.exit(2);
}

let failed = 0;
for (const file of files) {
  const text = readFileSync(file, "utf-8");
  const blocks = [...text.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m) => m[1]);
  if (blocks.length === 0) {
    console.log(`${file}: 无 mermaid 块`);
    continue;
  }
  let bad = 0;
  for (const [i, block] of blocks.entries()) {
    try {
      await mermaid.parse(block);
    } catch (err) {
      bad += 1;
      failed += 1;
      const msg = String(err?.message ?? err).split("\n").slice(0, 8).join("\n");
      console.error(`${file} [块 ${i + 1}] 解析失败:\n${msg}`);
    }
  }
  console.log(`${file}: ${blocks.length - bad}/${blocks.length} 个 mermaid 块通过`);
}
process.exit(failed ? 1 : 0);
