import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["three", "@react-three/fiber", "@react-three/drei"],
  turbopack: {
    root: process.cwd(),
  },
};

export default nextConfig;
