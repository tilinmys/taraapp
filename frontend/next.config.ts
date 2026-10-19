import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The documented local URL uses 127.0.0.1 while `next dev` binds as
  // localhost. Next 16 treats those as different origins and otherwise blocks
  // dev chunks/HMR, leaving the page looking loaded but unable to update.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
