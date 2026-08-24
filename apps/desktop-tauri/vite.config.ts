import { resolve } from "node:path";
import { defineConfig, normalizePath } from "vite";
import { viteStaticCopy } from "vite-plugin-static-copy";

const repositoryRoot = resolve(import.meta.dirname, "../..");
const repositoryAsset = (relativePath: string): string =>
  normalizePath(resolve(repositoryRoot, relativePath));

export default defineConfig({
  // Tauri serves bundled assets from its own protocol, so release builds must
  // not assume an HTTP origin rooted at `/`.
  base: "./",
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
  build: {
    target: "es2023",
    sourcemap: true,
  },
  plugins: [
    viteStaticCopy({
      targets: [
        {
          src: repositoryAsset("live2d/phone_live2d_page.html"),
          dest: "live2d",
          rename: { stripBase: true as const },
        },
        ...[
          "bg.png",
          "live2dcubismcore.min.js",
          "pixi.min.js",
          "pixi-unsafe-eval.min.js",
          "cubism4.min.js",
          "html2canvas.min.js",
          "amadeus-live2d-motion.js",
          "amadeus-live2d-host.js",
          "amadeus-live2d-runtime.js",
          "amadeus-live2d-phone.css",
        ].map((file) => ({
          src: repositoryAsset(`resources/${file}`),
          dest: "resources",
          rename: { stripBase: true as const },
        })),
        {
          src: repositoryAsset("resources/icons/*.svg"),
          dest: "resources/icons",
          rename: { stripBase: true as const },
        },
        ...[
          "amadeusV1.cdi3.json",
          "amadeusV1.moc3",
          "amadeusV1.model3.json",
          "amadeusV1.physics3.json",
        ].map((file) => ({
          src: repositoryAsset(`resources/live2d/kurisu/${file}`),
          dest: "resources/live2d/kurisu",
          rename: { stripBase: true as const },
        })),
        {
          src: repositoryAsset("resources/live2d/kurisu/amadeusV1.1024/*"),
          dest: "resources/live2d/kurisu/amadeusV1.1024",
          rename: { stripBase: true as const },
        },
        {
          src: repositoryAsset("resources/live2d/kurisu/motions/*.json"),
          dest: "resources/live2d/kurisu/motions",
          rename: { stripBase: true as const },
        },
      ],
    }),
  ],
});
