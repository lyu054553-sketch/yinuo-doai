import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 与 /files 转发到后端 8100；默认连真实后端。
// VITE_USE_MOCK=1 时前端走本地示例数据，不发请求。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8100",
        changeOrigin: true,
      },
      "/files": {
        target: "http://127.0.0.1:8100",
        changeOrigin: true,
      },
    },
  },
});