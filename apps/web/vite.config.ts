import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
// From vitest/config rather than vite: it is the same defineConfig widened to
// accept the `test` block below.
import { defineConfig } from 'vitest/config'

// The API and the interface share one origin in production, served by Caddy.
// In development Vite proxies /api and /accounts to Django so that session
// cookies and CSRF behave exactly as they will in the deployed setup — a
// cross-origin dev setup would need CORS relaxation that must never reach
// production.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: false },
      '/accounts': { target: 'http://localhost:8000', changeOrigin: false },
      '/healthz': { target: 'http://localhost:8000', changeOrigin: false },
      // allauth's login and MFA pages load their own scripts from here; without
      // it, recovery codes and the login page's onload script 404 in development.
      '/static': { target: 'http://localhost:8000', changeOrigin: false },
    },
  },
  build: {
    // Assets are built in CI, never on the pilot server (section 11.3):
    // a 2 GiB host running a bundler is how the pilot runs out of memory.
    outDir: 'dist',
    sourcemap: true,
    target: 'es2022',
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Unit tests only; the Playwright journeys in e2e/ run with `pnpm e2e`.
    include: ['src/**/*.test.{ts,tsx}'],
    css: true,
  },
})
