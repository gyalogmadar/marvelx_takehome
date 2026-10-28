import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API runs separately (uvicorn on :8000); in production FastAPI serves ui/dist itself.
export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      ['/claims', '/policies', '/benchmarks', '/health'].map((p) => [p, { target: 'http://localhost:8000', changeOrigin: true }]),
    ),
  },
})
