import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Backend port is overridable via BACKEND_PORT env var so run.bat can pick
// a free port without editing this file.
const backendPort = process.env.BACKEND_PORT ?? '8000'
const backendTarget = `http://localhost:${backendPort}`

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
      '/sse': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
})
