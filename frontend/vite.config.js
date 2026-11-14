import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Vite configuration for the Wepwawet interface.
// - The build goes into the Python package (src/wepwawet/web/dist), which ships it in the wheel.
// - `npm run dev` proxies /api and /auth to a backend started with `wepwawet serve --port 8650`.
const backend = `http://127.0.0.1:${process.env.API_PORT ?? 8650}`

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/auth': { target: backend, changeOrigin: true },
    },
  },
  // MapLibre's worker is bundled separately and passed to setWorkerUrl().
  worker: { format: 'es' },
  build: {
    // Never inline assets as data: URIs; the CSP only allows files from the app itself.
    assetsInlineLimit: 0,
    outDir: fileURLToPath(new URL('../src/wepwawet/web/dist', import.meta.url)),
    emptyOutDir: true,
    // MapLibre alone is ~800 kB minified; it is loaded once, from disk.
    chunkSizeWarningLimit: 1500,
  },
})
