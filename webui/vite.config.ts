import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiTarget = process.env.DAQ_DEV_API_TARGET || 'http://127.0.0.1:8081';

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/chat': apiTarget,
      '/feedback': apiTarget,
      '/history': apiTarget,
      '/health': apiTarget,
      '/attachments': apiTarget,
      '/enterprise': apiTarget,
      '/identity': apiTarget,
      '/authenticated': apiTarget,
      '/review': apiTarget,
    },
  },
});
