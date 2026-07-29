import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * The editor talks to the figmint backend (`src/figmint/server.py`) for anything
 * that needs the filesystem — listing and hashing figures, reading and writing
 * documents. Proxying `/api` keeps both on one origin in development, so there
 * is no CORS to configure and the production build can be served by the same
 * Python process.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5273,
    proxy: {
      '/api': {
        target: process.env.FIGMINT_API ?? 'http://127.0.0.1:8420',
        changeOrigin: true,
        // The filesystem watcher pushes over a websocket on /api/watch.
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
