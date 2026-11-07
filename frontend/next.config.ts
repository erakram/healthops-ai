import path from "node:path";
import type { NextConfig } from "next";

const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname),
  devIndicators: false,
  // The browser only ever talks to Next.js; /api/* is proxied to FastAPI, so the
  // session cookie stays same-origin and no CORS setup is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  compress: false, // compression buffers server-sent events through the proxy
};

export default nextConfig;
