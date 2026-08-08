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

import {
  normaliseEmail, createMagicLink, redeemMagicLink, sendMagicLink,
  sessionCookie, clearCookie, currentEmail, signOut
} from './auth.js';
import { createCheckout, createPortal, verifyWebhook, applyEvent, isSubscribed } from './stripe.js';

const JSON_HEADERS = { 'Content-Type': 'application/json; charset=utf-8' };

/**
 * The CSG generator, as a Durable Object wrapping a container.
 *
 * Written against the raw ctx.container API rather than the @cloudflare/containers
 * helper so this repo keeps its no-build-step, no-dependency shape -- there is
 * still no package.json, and wrangler bundles this file as it stands.
 *
 * Cold start is a real container boot plus a Python import of numpy, scipy and
 * trimesh, so the first request after a sleep is slow in a way the measured 1.3s
 * warm build does not suggest.
 *
 * Nothing here manages sleep -- `sleepAfter` belongs to the @cloudflare/containers
 * helper, not this raw API, so the platform reclaims the instance on its own
 * schedule. That is the main cost lever: containers bill for the time they are
 * up, not per request. If the subscription price is thin, the fix is a DO alarm
 * that calls container.stop() after an idle period, traded against making the
 * next customer wait through a cold start.
 */
export class ExportContainer {
  constructor(ctx) {
    this.ctx = ctx;
    this.container = ctx.container;
  }

  async fetch(request) {
    if (!this.container) {
      return new Response(JSON.stringify({ error: 'no container runtime bound' }),
                          { status: 503, headers: JSON_HEADERS });
    }
    if (!this.container.running) {
      /* Plain start(), no options. The generator needs no outbound network and
         turning egress off would be worth having, but that option is not
         verifiable without a deploy and a wrong name here breaks every paid
         export. Harden it once the first deploy proves the shape. */
      this.container.start();
    }
    /* 8080 is what container/server.py listens on and what the Dockerfile
       EXPOSEs; changing one means changing all three. */
    return this.container.getTcpPort(8080).fetch(request);
  }
}

function json(body, status, extra) {
  return new Response(JSON.stringify(body), {
    status: status || 200,
    headers: { ...JSON_HEADERS, ...(extra || {}) }
  });
}

const isSecure = (url) => url.protocol === 'https:';

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
  const body = JSON.stringify(payload);
  const req = () => new Request('http://export/generate', {
    method: 'POST', headers: JSON_HEADERS, body
  });

  /* EXPORT_ORIGIN wins when set, so `wrangler dev` can drive a container running
     on localhost -- the only way to exercise the paid path without deploying. In
     production it is unset and the binding below is used. */
  if (env.EXPORT_ORIGIN) {
    return fetch(new URL('/generate', env.EXPORT_ORIGIN).toString(), {
      method: 'POST', headers: JSON_HEADERS, body
    });
  }
  if (env.EXPORT_CONTAINER) {
    /* One named instance, so concurrent exports share a warm container instead
       of each paying a cold Python start. The container caps its own concurrency
       and answers 503 when full, which is a far better thing to hand the caller
       than a queue with no visible end. */
    const id = env.EXPORT_CONTAINER.idFromName('export');
    return env.EXPORT_CONTAINER.get(id).fetch(req());
  }
  if (env.EXPORT_SERVICE) return env.EXPORT_SERVICE.fetch(req());
  return null;
}

async function handleExport(request, env, url) {
  if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);

  /* Entitlement first, before parsing or spending a CSG process on someone who
     cannot have the result either way. 401 and 402 are distinguished so the page
     can say "sign in" or "subscribe" rather than one vague refusal. */
  const email = await currentEmail(request, env);
  if (!email) return json({ error: 'sign in to export', reason: 'signed_out' }, 401);
  if (!(await isSubscribed(env, email))) {
    return json({ error: 'a subscription is required to export', reason: 'not_subscribed' }, 402);
  }

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

  /* Optional: one STL rather than the whole set, so the per-part buttons in the
     print list keep working. The build is the same either way -- the generator
     makes every part regardless -- so this is a filter, not a cheaper request. */
  let part = null;
  if (body.part !== undefined && body.part !== null) {
    if (typeof body.part !== 'string' || !/^[a-z0-9_]{1,40}$/.test(body.part)) {
      return json({ error: 'unknown part' }, 400);
    }
    part = body.part;
  }

  const upstream = await callExportService(env, { pattern, style, params, part });
  if (!upstream) return json({ error: 'the export service is not configured' }, 503);

  /* Pass the service's own status and reasons through untouched. It reports the
     generator's wording, which is the same wording checkFits shows in the page,
     and rewording it here is how a customer ends up with two explanations for
     one configuration. */
  if (!upstream.ok) {
    let detail;
    try { detail = await upstream.json(); } catch { detail = { error: 'the export failed' }; }
    return json(detail, upstream.status);
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': part ? 'model/stl' : 'application/zip',
      'Content-Disposition': part
        ? `attachment; filename="${part}.stl"`
        : `attachment; filename="kumiko-lamp-${style}-${pattern}.zip"`,
      /* Deterministic for a given parameter set, but it is the paid artifact --
         never let a shared cache hold a copy. */
      'Cache-Control': 'private, no-store'
    }
  });
}

async function handleAuthRequest(request, env, url) {
  if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
  let body;
  try { body = await request.json(); } catch { return json({ error: 'body must be JSON' }, 400); }

  const email = normaliseEmail(body && body.email);
  /* Answer identically whether or not the address is known or deliverable.
     Anything else turns this endpoint into a way to test which emails have
     accounts. The one exception is a misconfigured mailer, which is our fault
     and must not be reported as success. */
  if (!email) return json({ ok: true });

  const link = await createMagicLink(env, email, url.origin);
  if (env.DEV_ECHO_MAGIC_LINK === '1') return json({ ok: true, devLink: link });

  const sent = await sendMagicLink(env, email, link);
  if (!sent) return json({ error: 'sign-in email could not be sent' }, 500);
  return json({ ok: true });
}

async function handleAuthCallback(request, env, url) {
  const out = await redeemMagicLink(env, url.searchParams.get('token'));
  if (!out) {
    return Response.redirect(`${url.origin}/?signin=expired`, 302);
  }
  return new Response(null, {
    status: 302,
    headers: {
      Location: `${url.origin}/?signin=ok`,
      'Set-Cookie': sessionCookie(out.sid, isSecure(url))
    }
  });
}

async function handleWebhook(request, env) {
  if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
  /* Raw text, not request.json(): the signature covers the exact bytes Stripe
     sent, and a parse/re-stringify round trip changes them. */
  const raw = await request.text();
  const event = await verifyWebhook(env, raw, request.headers.get('Stripe-Signature'));
  if (!event) return json({ error: 'bad signature' }, 400);
  const outcome = await applyEvent(env, event);
  return json({ received: true, outcome });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (p === '/api/export') return handleExport(request, env, url);
    if (p === '/api/auth/request') return handleAuthRequest(request, env, url);
    if (p === '/api/auth/callback') return handleAuthCallback(request, env, url);
    if (p === '/api/stripe-webhook') return handleWebhook(request, env);

    if (p === '/api/me') {
      const email = await currentEmail(request, env);
      return json({
        signedIn: !!email,
        email: email || null,
        subscribed: await isSubscribed(env, email),
        priceConfigured: !!(env.STRIPE_SECRET_KEY && env.STRIPE_PRICE_ID)
      }, 200, { 'Cache-Control': 'private, no-store' });
    }

    if (p === '/api/auth/signout') {
      if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
      await signOut(request, env);
      return json({ ok: true }, 200, { 'Set-Cookie': clearCookie(isSecure(url)) });
    }

    if (p === '/api/checkout') {
      if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
      const email = await currentEmail(request, env);
      if (!email) return json({ error: 'sign in first', reason: 'signed_out' }, 401);
      if (!env.STRIPE_SECRET_KEY || !env.STRIPE_PRICE_ID) {
        return json({ error: 'billing is not configured' }, 503);
      }
      try {
        return json({ url: await createCheckout(env, email, url.origin) });
      } catch {
        return json({ error: 'could not start checkout' }, 502);
      }
    }

    if (p === '/api/portal') {
      if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
      const email = await currentEmail(request, env);
      if (!email) return json({ error: 'sign in first', reason: 'signed_out' }, 401);
      try {
        const portal = await createPortal(env, email, url.origin);
        return portal ? json({ url: portal }) : json({ error: 'no billing account yet' }, 404);
      } catch {
        return json({ error: 'could not open the billing portal' }, 502);
      }
    }

    if (p === '/api/health') {
      return json({
        ok: true,
        exportConfigured: !!(env.EXPORT_CONTAINER || env.EXPORT_SERVICE || env.EXPORT_ORIGIN),
        billingConfigured: !!(env.STRIPE_SECRET_KEY && env.STRIPE_PRICE_ID),
        mailConfigured: !!(env.RESEND_API_KEY || env.SEND_EMAIL) || env.DEV_ECHO_MAGIC_LINK === '1'
      });
    }

    if (p.startsWith('/api/')) return json({ error: 'not found' }, 404);

    /* Everything else is the app itself. With `main` set this Worker sees every
       request, so the assets binding has to be called explicitly -- without this
       line the site stops serving entirely. */
    return env.ASSETS.fetch(request);
  }
};
