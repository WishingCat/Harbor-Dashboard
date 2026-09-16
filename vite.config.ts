import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Preserve the browser's Host so the API can validate same-origin writes.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/docs': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/openapi.json': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/redoc': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
});
