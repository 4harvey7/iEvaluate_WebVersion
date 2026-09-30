import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Production builds must point at an https API; the same origin is used to
// build the Content-Security-Policy connect-src.
function requireHttpsApiBase(env) {
  const raw = (env.VITE_API_BASE || "").trim();
  let url = null;
  try {
    url = new URL(raw);
  } catch {
    url = null;
  }
  if (!url || url.protocol !== "https:") {
    throw new Error(
      `[build guard] VITE_API_BASE must be an https:// URL for production builds (got "${raw || "<empty>"}"). ` +
        `Set it in the Vercel project env, e.g. VITE_API_BASE=https://your-api.onrender.com`
    );
  }
  return url.origin;
}

function cspPlugin(apiOrigin) {
  const policy = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' blob: data:",
    `connect-src 'self' ${apiOrigin}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");
  return {
    name: "inject-csp-meta",
    apply: "build",
    transformIndexHtml() {
      return [
        {
          tag: "meta",
          attrs: { "http-equiv": "Content-Security-Policy", content: policy },
          injectTo: "head-prepend",
        },
      ];
    },
  };
}

export default defineConfig(({ command, mode }) => {
  const plugins = [react()];
  if (command === "build") {
    const env = loadEnv(mode, process.cwd(), "");
    plugins.push(cspPlugin(requireHttpsApiBase(env)));
  }
  return {
    plugins,
    server: { port: 5173 },
  };
});
