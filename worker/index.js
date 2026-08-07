/**
 * The configurator's edge. Serves the static app, and owns the paid export.
 *
 * The split is deliberate: this file knows about entitlement and nothing about
 * geometry; the container knows about geometry and nothing about payment. The
 * container is never routed publicly, so this is the only way to reach it.
 *
 * Reaching the container is a seam rather than a hardcoded call. EXPORT_SERVICE
 * is a binding (a Cloudflare Container or a service binding) and EXPORT_ORIGIN
 * is a plain URL -- the second is what makes `wrangler dev` against a container
 * running on localhost possible, which is the only way to exercise this without
 * deploying.
 */

const JSON_HEADERS = { 'Content-Type': 'application/json; charset=utf-8' };

function json(body, status) {
  return new Response(JSON.stringify(body), { status: status || 200, headers: JSON_HEADERS });
}

/* The browser sends its whole slider set. Numbers only, and a fixed ceiling on
   how many: the generator rejects unknown names itself, but that costs a
   process spawn, and an unbounded object is a free way to make us pay for it. */
function cleanParams(raw) {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const keys = Object.keys(raw);
  if (keys.length > 60) return null;
  const out = {};
  for (const k of keys) {
    if (!/^[a-z][a-z0-9_]{0,39}$/.test(k)) return null;
    const v = raw[k];
    if (typeof v !== 'number' || !Number.isFinite(v)) return null;
    out[k] = v;
  }
  return out;
}

async function callExportService(env, payload) {
  const req = new Request('http://export/generate', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload)
  });
  if (env.EXPORT_SERVICE) return env.EXPORT_SERVICE.fetch(req);
  if (env.EXPORT_ORIGIN) {
    return fetch(new URL('/generate', env.EXPORT_ORIGIN).toString(), {
      method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload)
    });
  }
  return null;
}

async function handleExport(request, env) {
  if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: 'body must be JSON' }, 400);
  }
  if (body === null || typeof body !== 'object') {
    return json({ error: 'body must be a JSON object' }, 400);
  }

  const params = cleanParams(body.params || {});
  if (params === null) return json({ error: 'params must be an object of numbers' }, 400);

  const style = body.style === 'modern' ? 'modern' : 'classic';
  const pattern = typeof body.pattern === 'string' ? body.pattern : 'asanoha';
  if (!/^[a-z0-9_]{1,40}$/.test(pattern)) return json({ error: 'unknown pattern' }, 400);

  const upstream = await callExportService(env, { pattern, style, params });
  if (!upstream) {
    return json({ error: 'the export service is not configured' }, 503);
  }

  /* Pass the service's own status and reasons through untouched. It reports the
     generator's wording, which is the same wording checkFits shows in the page,
     and rewording it here is how a customer ends up with two explanations for
     one configuration. */
  if (!upstream.ok) {
    let detail;
    try { detail = await upstream.json(); } catch { detail = { error: 'the export failed' }; }
    return json(detail, upstream.status);
  }

  const filename = `kumiko-lamp-${style}-${pattern}.zip`;
  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': 'application/zip',
      'Content-Disposition': `attachment; filename="${filename}"`,
      /* A build is deterministic for a given parameter set, but it is also the
         paid artifact -- never let a shared cache hold a copy. */
      'Cache-Control': 'private, no-store'
    }
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/api/export') return handleExport(request, env);
    if (url.pathname === '/api/health') {
      return json({ ok: true, exportConfigured: !!(env.EXPORT_SERVICE || env.EXPORT_ORIGIN) });
    }
    if (url.pathname.startsWith('/api/')) return json({ error: 'not found' }, 404);

    /* Everything else is the app itself. With `main` set this Worker sees every
       request, so the assets binding has to be called explicitly -- without this
       line the site stops serving entirely. */
    return env.ASSETS.fetch(request);
  }
};
