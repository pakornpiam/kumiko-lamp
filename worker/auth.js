/**
 * Identity: email magic links and sessions.
 *
 * Tokens are random and stored in KV rather than signed and self-describing.
 * A signed token can be validated without a lookup, but it also cannot be
 * revoked or made single-use without one -- and single-use is the property that
 * matters most here, because magic links end up in mail logs, browser history
 * and forwarded threads. Since the lookup is unavoidable, the random token is
 * strictly simpler and there is no signing scheme to get wrong.
 */

const MAGIC_TTL_S = 15 * 60;
const SESSION_TTL_S = 30 * 24 * 60 * 60;
const COOKIE = 'kumiko_session';

function randomToken() {
  const b = new Uint8Array(32);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
}

/* Deliberately loose. Email syntax is famously not a regex, and the real proof
   of an address is that its owner clicks the link we send to it. This only
   rejects obvious junk and anything with control characters or a header
   separator, which is what would matter if it reached a mail API. */
export function normaliseEmail(raw) {
  if (typeof raw !== 'string') return null;
  const e = raw.trim().toLowerCase();
  if (e.length < 3 || e.length > 254) return null;
  if (/[\s<>,;"\\]/.test(e)) return null;
  if (!/^[^@]+@[^@]+\.[^@]+$/.test(e)) return null;
  return e;
}

export async function createMagicLink(env, email, origin) {
  const token = randomToken();
  await env.KUMIKO.put(`magic:${token}`, email, { expirationTtl: MAGIC_TTL_S });
  return `${origin}/api/auth/callback?token=${token}`;
}

/** Consume a magic token and mint a session. Single-use: the delete happens
 *  before the session exists, so a replayed link cannot mint a second one. */
export async function redeemMagicLink(env, token) {
  if (typeof token !== 'string' || !/^[a-f0-9]{64}$/.test(token)) return null;
  const key = `magic:${token}`;
  const email = await env.KUMIKO.get(key);
  if (!email) return null;
  await env.KUMIKO.delete(key);

  const sid = randomToken();
  await env.KUMIKO.put(`session:${sid}`, email, { expirationTtl: SESSION_TTL_S });
  return { sid, email };
}

export function sessionCookie(sid, secure) {
  const flags = [
    `${COOKIE}=${sid}`,
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
    `Max-Age=${SESSION_TTL_S}`
  ];
  /* Secure would make the cookie invisible over plain http, which is exactly
     what `wrangler dev` serves; production is https so it is always set there. */
  if (secure) flags.push('Secure');
  return flags.join('; ');
}

export function clearCookie(secure) {
  const flags = [`${COOKIE}=`, 'Path=/', 'HttpOnly', 'SameSite=Lax', 'Max-Age=0'];
  if (secure) flags.push('Secure');
  return flags.join('; ');
}

function readCookie(request, name) {
  const raw = request.headers.get('Cookie');
  if (!raw) return null;
  for (const part of raw.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    if (part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
}

export async function currentEmail(request, env) {
  const sid = readCookie(request, COOKIE);
  if (!sid || !/^[a-f0-9]{64}$/.test(sid)) return null;
  return env.KUMIKO.get(`session:${sid}`);
}

export async function signOut(request, env) {
  const sid = readCookie(request, COOKIE);
  if (sid && /^[a-f0-9]{64}$/.test(sid)) await env.KUMIKO.delete(`session:${sid}`);
}

/**
 * Send the link.
 *
 * A seam, because there is no one answer here. Cloudflare's own send_email
 * binding only delivers to addresses already verified in the account, which is
 * fine for alerts to yourself and useless for signing up strangers -- so an
 * outside provider is required for real customers, and this picks whichever is
 * configured rather than hardcoding one.
 *
 * Returns false when nothing is configured, so the caller can tell "sent" from
 * "silently dropped" instead of reporting success to someone who will never get
 * an email.
 */
export async function sendMagicLink(env, email, link) {
  if (env.RESEND_API_KEY) {
    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        from: env.MAIL_FROM || 'Kumiko Lantern <noreply@kumikolantern.pkroaming.com>',
        to: [email],
        subject: 'Your Kumiko Lantern sign-in link',
        text: `Sign in to Kumiko Lantern:\n\n${link}\n\n` +
              `This link works once and expires in 15 minutes. ` +
              `If you did not ask for it, you can ignore this email.`
      })
    });
    if (!r.ok) {
      console.error('resend failed', r.status, (await r.text()).slice(0, 300));
      return false;
    }
    return true;
  }

  if (env.SEND_EMAIL) {
    try {
      /* Cloudflare Email Routing. Kept for a self-hosted setup where the only
         recipient is the operator; it will reject unverified destinations. */
      const { EmailMessage } = await import('cloudflare:email');
      const from = env.MAIL_FROM || 'noreply@kumikolantern.pkroaming.com';
      const raw = `From: ${from}\r\nTo: ${email}\r\n` +
                  `Subject: Your Kumiko Lantern sign-in link\r\n\r\n${link}\r\n`;
      await env.SEND_EMAIL.send(new EmailMessage(from, email, raw));
      return true;
    } catch (e) {
      console.error('send_email failed', String(e).slice(0, 300));
      return false;
    }
  }

  return false;
}
