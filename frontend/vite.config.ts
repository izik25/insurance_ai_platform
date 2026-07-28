import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // frontend/src/api.ts uses relative paths ("/api", "/public/v1") so the
    // same code works against the nginx reverse proxy in production - this
    // proxies the same paths to the local backend during `npm run dev`.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/public': 'http://127.0.0.1:8000',
    },
  },
})
