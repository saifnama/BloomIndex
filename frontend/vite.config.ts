import path from 'node:path';
import { defineConfig } from 'vite';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  // tanstackRouter MUST come before react() — it generates routeTree.gen.ts
  // from src/routes/ which the React transform then picks up.
  plugins: [
    tanstackRouter({ target: 'react', autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
  server: {
    port: 5173,
    proxy: {
      '/search': {
        target: 'http://localhost:8000',
        timeout: 600000,
      },
      '/paper': {
        target: 'http://localhost:8000',
        timeout: 600000,
      },
      '/ner': {
        target: 'http://localhost:8000',
        timeout: 600000,
      },
      '/rag': {
        target: 'http://localhost:8000',
        timeout: 600000,
      },
      '/health': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
      '/api': {
        target: 'http://localhost:8000',
        timeout: 600000,
      },
      '/doi': 'http://localhost:8000',
    },
  },
});
