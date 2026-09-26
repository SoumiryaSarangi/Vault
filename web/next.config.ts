// Owner: Anushka. Build config is gated (TEAM_PROTOCOL §4).
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // LAN mode (docs/soum_lan_demo.md): the dashboard is opened as http://<hub-ip>:3000, so the dev server must
  // accept its own assets and hot-reload socket from private network addresses (added by Soum).
  allowedDevOrigins: ["10.*.*.*", "192.168.*.*", "172.*.*.*"],
};

export default nextConfig;
