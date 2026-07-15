import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  base: '/v2-assets/',
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { outDir: resolve(__dirname, '../static-v2'), emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:18000' } }
})
