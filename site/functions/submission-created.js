// Netlify event function: runs on Netlify's servers the moment the "watch"
// form on mycarsd.online receives a (non-spam) submission, and announces it
// to the operator's Telegram chat. Replaces waiting for the hourly
// scripts/raw_sweep.py --fetch on the PC, which could be an hour late and is
// silent while the PC is off or paused.
//
// PRIVACY: same rule as scripts/fetch_watches.py announce_new() - the message
// carries the identifier, the note and the contact TYPE only, never the
// contact itself. Telegram is a third party; the Netlify email and
// scripts/check_watches.py have the number.
//
// Secrets come from Netlify environment variables (scope: functions):
//   TELEGRAM_BOT_TOKEN, TELEGRAM_NOTIFY_CHAT_ID
// Deployed by scripts/netlify_deploy.py (zipped from site/functions/) with
// every deploy - a deploy that omits it would remove it from the site.
"use strict";
const https = require("https");

const FORM_NAME = "watch";

function contactType(contact) {
  const c = (contact || "").trim();
  if (!c) return "none";
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(c)) return "email";
  const digits = c.replace(/[^0-9]/g, "");
  if (digits.length >= 7 && digits.length <= 15) return "phone";
  return "unparsed";
}

function sendTelegram(token, chatId, text) {
  const body = JSON.stringify({
    chat_id: chatId, text: text.slice(0, 4000), disable_web_page_preview: true,
  });
  return new Promise((resolve, reject) => {
    const req = https.request({
      hostname: "api.telegram.org", method: "POST",
      path: `/bot${token}/sendMessage`,
      headers: { "Content-Type": "application/json",
                 "Content-Length": Buffer.byteLength(body) },
      timeout: 15000,
    }, (res) => {
      let data = "";
      res.on("data", (d) => { data += d; });
      res.on("end", () => resolve({ status: res.statusCode, body: data.slice(0, 300) }));
    });
    req.on("timeout", () => req.destroy(new Error("telegram timeout")));
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

exports.handler = async (event) => {
  let payload;
  try {
    payload = JSON.parse(event.body || "{}").payload || {};
  } catch (e) {
    console.log("bad body:", e.message);
    return { statusCode: 400, body: "bad body" };
  }
  if (payload.form_name && payload.form_name !== FORM_NAME) {
    console.log("ignoring form", payload.form_name);
    return { statusCode: 200, body: "ignored" };
  }
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_NOTIFY_CHAT_ID;
  if (!token || !chatId) {
    console.log("TELEGRAM_BOT_TOKEN / TELEGRAM_NOTIFY_CHAT_ID not set");
    return { statusCode: 200, body: "not configured" };
  }

  const data = payload.data || {};
  const ident = (data.identifier || "").trim() || "(no identifier)";
  const note = (data.note || "").replace(/\s+/g, " ").trim();
  const when = (payload.created_at || new Date().toISOString())
    .slice(0, 16).replace("T", " ");
  const lines = [
    "CarHunt: new standing-watch registration (instant)",
    `• ${ident} · ${when} UTC · contact: ${contactType(data.contact)}`,
  ];
  if (note) lines.push(`  note: ${note.slice(0, 160)}`);
  lines.push("The hourly sweep checks it against all posts; a hit is a "
             + "separate alert. Contact: python scripts\check_watches.py");

  try {
    const r = await sendTelegram(token, chatId, lines.join("\n"));
    console.log("telegram", r.status, r.body);
  } catch (e) {
    console.log("telegram failed:", e.message);
  }
  // Always 200: a failure here must never make Netlify treat the submission
  // itself as failed. The hourly fetch and the Netlify email still exist.
  return { statusCode: 200, body: "ok" };
};
