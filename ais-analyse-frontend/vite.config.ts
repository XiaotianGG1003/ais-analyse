import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return

          if (id.includes('/echarts/') || id.includes('/zrender/')) {
            return 'charts-vendor'
          }

          if (id.includes('/leaflet') || id.includes('/leaflet-draw') || id.includes('/leaflet.heat')) {
            return 'map-vendor'
          }

          if (id.includes('/element-plus/') || id.includes('/@element-plus/')) {
            return 'ui-vendor'
          }

          if (id.includes('/vue/') || id.includes('/pinia/') || id.includes('/vue-router/')) {
            return 'vue-vendor'
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
        ws: true,
      },
    },
  },
})
