import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [{
    name: 'copy-studio-pages',
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const pages: Record<string, string> = {
          '/': '/transcript.html',
          '/copy-studio': '/transcript.html',
          '/copy-studio/records': '/transcript-records.html',
          '/copy-studio/ft': '/transcript-admin.html',
        };
        const url = new URL(req.url || '/', 'http://localhost');
        const page = pages[url.pathname.replace(/\/$/, '') || '/'];
        if (page) req.url = page + url.search;
        next();
      });
    },
  }],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': process.env.COPY_STUDIO_API_TARGET || 'http://127.0.0.1:8765' },
  },
  build: {
    rollupOptions: {
      input: {
        transcript: 'transcript.html',
        transcriptAdmin: 'transcript-admin.html',
        transcriptRecords: 'transcript-records.html',
      },
    },
  },
});
