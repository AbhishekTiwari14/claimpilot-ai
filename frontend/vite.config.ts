import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv, searchForWorkspaceRoot } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxy = { '/api': { target: env.CLAIMPILOT_DEV_API_TARGET || 'http://127.0.0.1:8000' } };
  return {
    plugins: [react(), tailwind()],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: {
      proxy,
      fs: { allow: [searchForWorkspaceRoot(process.cwd()), fileURLToPath(new URL('../data/knowledge/', import.meta.url))] },
    },
    preview: { proxy },
  };
});
