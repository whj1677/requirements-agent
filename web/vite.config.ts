import { defineConfig } from 'vite';
export default defineConfig({
  build: { rollupOptions: { output: {
    manualChunks(id) { if (id.includes('node_modules/react')) return 'react-vendor'; },
  } } },
});
