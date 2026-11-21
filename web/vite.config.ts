import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The browser never holds a secret. Everything that needs a credential goes
// through the backend, so the dev server proxies /api and /ws to it.
const BACKEND = process.env.ETHYNEON_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/ws': { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
})
