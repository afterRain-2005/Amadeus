#!/usr/bin/env node
// Extract the legacy Live2D page's inline code so the packaged WebView can use
// a strict CSP without script-src/style-src unsafe-inline.
import { readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const htmlPath = resolve(root, 'live2d', 'phone_live2d_page.html')
const html = await readFile(htmlPath, 'utf8')
const inlineScripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
const style = html.match(/<style>([\s\S]*?)<\/style>/)

if (
  inlineScripts.length === 0 &&
  !style &&
  html.includes('amadeus-live2d-runtime.js') &&
  html.includes('amadeus-live2d-phone.css')
) {
  console.log('Live2D assets are already externalized')
  process.exit(0)
}

if (inlineScripts.length !== 2 || !style) {
  throw new Error(`expected two inline scripts and one style block; got scripts=${inlineScripts.length}, style=${Boolean(style)}`)
}

await writeFile(
  resolve(root, 'resources', 'amadeus-live2d-host.js'),
  `${inlineScripts[0][1].trim()}\n`,
  'utf8',
)
await writeFile(
  resolve(root, 'resources', 'amadeus-live2d-runtime.js'),
  `${inlineScripts[1][1].trim()}\n`,
  'utf8',
)
await writeFile(
  resolve(root, 'resources', 'amadeus-live2d-phone.css'),
  `${style[1].trim()}\n`,
  'utf8',
)

let externalized = html.replace(
  inlineScripts[0][0],
  '<script src="../resources/amadeus-live2d-host.js"></script>',
)
externalized = externalized.replace(
  style[0],
  '<link rel="stylesheet" href="../resources/amadeus-live2d-phone.css">',
)
externalized = externalized.replace(
  inlineScripts[1][0],
  '<script src="../resources/amadeus-live2d-runtime.js"></script>',
)
await writeFile(htmlPath, externalized, 'utf8')

console.log('externalized Live2D inline script/style assets')
