/**
 * Stripe: checkout sessions in, webhooks out, entitlement in KV.
 *
 * Entitlement is written only by the webhook, never by the browser and never by
 * the checkout redirect. A success redirect proves someone came back from a
 * Stripe page; it does not prove a payment settled, and it is trivially forged
 * by typing the URL. The webhook is the only statement Stripe actually signs.
 */

const API = 'https://api.stripe.com/v1';

function form(obj) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== undefined) p.set(k, String(v));
  return p;
}

async function stripe(env, path, body) {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`,
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: form(body)
  });
  const text = await r.text();
  if (!r.ok) {
    console.error('stripe', path, r.status, text.slice(0, 400));
    throw new Error(`stripe ${path} failed`);
  }
  return JSON.parse(text);
}

export async function createCheckout(env, email, origin) {
  const s = await stripe(env, '/checkout/sessions', {
    mode: 'subscription',
    'line_items[0][price]': env.STRIPE_PRICE_ID,
    'line_items[0][quantity]': 1,
    customer_email: email,
    /* Ties the Stripe customer back to the identity that started checkout, so
       the webhook does not have to trust whatever email Stripe ends up with. */
    'metadata[email]': email,
    'subscription_data[metadata][email]': email,
    success_url: `${origin}/?subscribed=1`,
    cancel_url: `${origin}/?checkout=cancelled`
  });
  return s.url;
}

export async function createPortal(env, email, origin) {
  const customerId = await env.KUMIKO.get(`customer:${email}`);
  if (!customerId) return null;
  const s = await stripe(env, '/billing_portal/sessions', {
    customer: customerId,
    return_url: `${origin}/`
  });
  return s.url;
}

/* Constant time, so a comparison cannot be turned into an oracle for guessing a
   valid signature one byte at a time. */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function hmacHex(secret, message) {
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(message));
  return [...new Uint8Array(sig)].map((x) => x.toString(16).padStart(2, '0')).join('');
}

/**
 * Verify a Stripe webhook signature over the RAW body.
 *
 * It must be the raw bytes: JSON.parse followed by JSON.stringify reorders keys
 * and drops whitespace, and the signature is over what Stripe actually sent.
 * The timestamp check is not decoration either -- without it a captured request
 * stays valid forever and can be replayed to re-grant a cancelled subscription.
 */
export async function verifyWebhook(env, rawBody, header, toleranceS = 300) {
  if (!header) return null;
  let t = null;
  const v1 = [];
  for (const part of header.split(',')) {
    const [k, v] = part.split('=', 2);
    if (k === 't') t = v;
    else if (k === 'v1') v1.push(v);
  }
  if (!t || !v1.length) return null;

  const age = Math.abs(Math.floor(Date.now() / 1000) - Number(t));
  if (!Number.isFinite(age) || age > toleranceS) return null;

  const expected = await hmacHex(env.STRIPE_WEBHOOK_SECRET, `${t}.${rawBody}`);
  if (!v1.some((s) => timingSafeEqual(s, expected))) return null;

  try {
    return JSON.parse(rawBody);
  } catch {
    return null;
  }
}

/** Active for our purposes: Stripe still considers the sub live. `past_due`
 *  counts -- a card that failed to renew is a billing problem, not a reason to
 *  lock someone out mid-retry. */
const ACTIVE = new Set(['active', 'trialing', 'past_due']);

async function emailFor(env, sub) {
  const meta = sub.metadata && sub.metadata.email;
  if (meta) return meta.toLowerCase();
  /* Subscriptions created outside our checkout have no metadata, so fall back
     to the customer -> email mapping the first event recorded. */
  if (typeof sub.customer === 'string') {
    return env.KUMIKO.get(`email:${sub.customer}`);
  }
  return null;
}

export async function applyEvent(env, event) {
  const o = event.data && event.data.object;
  if (!o) return 'ignored';

  if (event.type === 'checkout.session.completed') {
    const email = (o.metadata && o.metadata.email) ||
                  (o.customer_email || '').toLowerCase();
    if (email && typeof o.customer === 'string') {
      await env.KUMIKO.put(`customer:${email}`, o.customer);
      await env.KUMIKO.put(`email:${o.customer}`, email);
    }
    return 'linked';
  }

  if (event.type.startsWith('customer.subscription.')) {
    const email = await emailFor(env, o);
    if (!email) return 'no email';
    const active = event.type !== 'customer.subscription.deleted' &&
                   ACTIVE.has(o.status);
    if (active) {
      await env.KUMIKO.put(`sub:${email}`, JSON.stringify({
        status: o.status,
        subscriptionId: o.id,
        currentPeriodEnd: o.current_period_end || 0
      }));
    } else {
      await env.KUMIKO.delete(`sub:${email}`);
    }
    if (typeof o.customer === 'string') {
      await env.KUMIKO.put(`email:${o.customer}`, email);
      await env.KUMIKO.put(`customer:${email}`, o.customer);
    }
    return active ? 'granted' : 'revoked';
  }

  return 'ignored';
}

export async function isSubscribed(env, email) {
  if (!email) return false;
  const raw = await env.KUMIKO.get(`sub:${email}`);
  if (!raw) return false;
  try {
    const s = JSON.parse(raw);
    /* KV is eventually consistent and Stripe can be late, so trust the period
       end we were told rather than presence alone. */
    if (s.currentPeriodEnd && s.currentPeriodEnd * 1000 < Date.now() - 86400000) {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}
