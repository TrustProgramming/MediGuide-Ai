import type { Config, Context } from "@netlify/edge-functions";

// Forwards every request to the MediGuide FastAPI backend hosted on Render.
// The backend's "/" is a JSON health check, so visitors landing on the site
// root are sent to the patient UI at /ui instead.
export default async (request: Request, context: Context) => {
  const backend = Netlify.env.get("BACKEND_URL")?.trim().replace(/\/+$/, "");
  if (!backend) {
    // Not configured yet: serve the static setup page from /public.
    return context.next();
  }

  const url = new URL(request.url);
  if (url.pathname === "/") {
    return Response.redirect(new URL("/ui", url), 302);
  }

  const target = new URL(url.pathname + url.search, backend);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.set("x-forwarded-host", url.host);
  headers.set("x-forwarded-proto", url.protocol.replace(":", ""));

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "manual",
    });
  } catch {
    return wakingUp();
  }

  // Render's free tier answers with a gateway error while the service boots.
  if ([502, 503, 504].includes(upstream.status) && request.method === "GET") {
    return wakingUp();
  }

  const responseHeaders = new Headers(upstream.headers);
  const location = responseHeaders.get("location");
  if (location?.startsWith(backend)) {
    responseHeaders.set("location", location.slice(backend.length) || "/");
  }
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
};

function wakingUp(): Response {
  const html = `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="8"><title>MediGuide is starting…</title>
<style>body{font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#f4f8f7;color:#16302b}
main{text-align:center;max-width:28rem;padding:2rem}</style></head>
<body><main><h1>MediGuide is waking up</h1>
<p>The server was idle and is starting again. This page reloads automatically in a few seconds.</p></main></body></html>`;
  return new Response(html, {
    status: 503,
    headers: { "content-type": "text/html; charset=utf-8", "retry-after": "8", "cache-control": "no-store" },
  });
}

export const config: Config = { path: "/*" };
