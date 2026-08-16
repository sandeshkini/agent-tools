import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { marked } from 'marked'

const ROOT = process.env.CONTENT_DIR || '/content'
const VENDOR_DIR = path.join(path.dirname(new URL(import.meta.url).pathname), 'vendor')
const BASE = process.env.BASE_PATH || ''            // external prefix (traefik strips it)
const PORT = process.env.PORT || 8080
const TOKEN = process.env.PUBLISH_TOKEN || ''       // required for POST/PUT publish endpoints
// ── notification (fires on publish) — config-driven; no-op until ntfy is set ──
const NTFY_URL = process.env.NTFY_URL || ''         // e.g. http://ntfy:80  (internal)
const NTFY_TOPIC = process.env.NTFY_TOPIC || ''
const NTFY_TOKEN = process.env.NTFY_TOKEN || ''
const PUBLIC_BASE = process.env.PUBLIC_BASE || 'https://artifacts.kingdomofluna.com'   // apps.kingdomofluna.com is retired — orphaned 2026-08-15, nothing listens there
// Pangolin resource for this hostname has SSO off (other systems need to push
// without an interactive login) -- so this process must enforce on its own
// that the push-only hostname can ONLY reach the token-gated write routes.
// Everything else (viewing the board, listing content) stays exclusively on
// the SSO-protected hostname. Mirrors the register.dispatch.* pattern.
const PUSH_ONLY_HOST = process.env.PUSH_ONLY_HOST || 'push.artifacts.kingdomofluna.com'
const PUSH_ONLY_ROUTES = [
  ['POST', '/api/publish'],
  ['POST', '/api/artifacts'],
  // GET /api/list — added 2026-08-16 so list_artifacts works from machines (aibo-mac, aibo-dev)
  // that can only reach the artifacts board through this no-SSO hostname, not aibo's internal
  // docker network. Still token-gated below (same requireToken() as the write routes) -- this
  // host stays "nothing without the token", not "read is public here now".
  ['GET', '/api/list'],
  // POST /api/publish-files — added 2026-08-16, see the handler below for the full writeup.
  // Same no-SSO-but-token-gated shape as the other write routes.
  ['POST', '/api/publish-files'],
]

const ARTIFACTS_DIR = path.join(ROOT, 'artifacts')
for (const d of ['summaries', 'apps', 'artifacts']) fs.mkdirSync(path.join(ROOT, d), { recursive: true })

async function notifyPublish(title, relUrl) {
  if (!NTFY_URL || !NTFY_TOPIC) return              // not configured → skip silently
  try {
    // Use ntfy's JSON publishing API (POST to root with a JSON body) rather than the
    // header-based API: HTTP headers are Latin-1 only, so a Title header containing an
    // emoji or em-dash throws "Cannot convert argument to a ByteString" and the push is
    // silently dropped. The JSON body is UTF-8, so any title works.
    await fetch(`${NTFY_URL.replace(/\/$/, '')}/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(NTFY_TOKEN ? { Authorization: `Bearer ${NTFY_TOKEN}` } : {}),
      },
      body: JSON.stringify({
        topic: NTFY_TOPIC,
        title: `New artifact: ${title}`,
        message: 'Published — tap to open',
        click: PUBLIC_BASE + relUrl,
        tags: ['card_file_box'],
      }),
    })
  } catch (e) { console.error('ntfy notify failed:', e.message) }
}

// Design language follows ~/Documents/guidelines/design-system.md (the "global" system shared
// by Digest/Noted/Tracker/future apps) — same palette, mono chrome, card/badge/chip components —
// so the board feels like part of the same family instead of a one-off tool aesthetic.
const HEAD_FONTS = `<link rel=preconnect href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap" rel=stylesheet>`
const CSS = `<style>
:root{
  color-scheme:dark;
  --bg:#0d1117;--surface:#161b22;--surface2:#21262d;
  --border:rgba(255,255,255,.08);--border-med:rgba(255,255,255,.15);
  --text:#e2e8f0;--muted:#8b949e;--accent:#58a6ff;
  --mono:'JetBrains Mono','SF Mono',Consolas,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;font-size:15px;line-height:1.65}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}

.topbar{position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border);
  padding:.7rem 1.5rem;display:flex;align-items:center;gap:.6rem}
.topbar-brand{font-family:var(--mono);font-size:.8rem;font-weight:700;letter-spacing:.04em;color:var(--text)}
.topbar-sep{color:var(--border-med)}
.topbar-title{font-family:var(--mono);font-size:.78rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.topbar-back{margin-left:auto;flex-shrink:0;font-family:var(--mono);font-size:.72rem;color:var(--muted);
  padding:.32rem .7rem;border:1px solid var(--border);border-radius:6px;transition:.15s}
.topbar-back:hover{color:var(--accent);border-color:var(--accent);text-decoration:none}

.wrap{max-width:820px;margin:0 auto;padding:2rem 1.5rem 4rem}

.board-hdr{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:18px;flex-wrap:wrap}
h1{font-size:1.5rem;font-weight:700;letter-spacing:-.01em;margin:0 0 4px}
.sub{color:var(--muted);margin:0;font-size:.82rem;font-family:var(--mono)}
.search{background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);
  padding:.6rem .85rem;font-size:.85rem;min-width:220px;outline:none;transition:border-color .15s}
.search:focus{border-color:var(--accent)}.search::placeholder{color:var(--muted)}

.toolbar{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}
.select{background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);
  padding:.6rem .85rem;font-size:.85rem;font-family:var(--mono);outline:none;cursor:pointer;transition:border-color .15s}
.select:hover{border-color:var(--border-med)}.select:focus{border-color:var(--accent)}

.group{margin-bottom:4px}
.day-hdr{font-family:var(--mono);font-size:.7rem;font-weight:700;color:var(--muted);text-transform:uppercase;
  letter-spacing:.08em;margin:24px 2px 8px}
.group:first-child .day-hdr{margin-top:0}

.card{display:flex;align-items:center;gap:13px;padding:11px 14px;margin:6px 0;background:var(--surface);
  border:1px solid var(--border);border-radius:10px;transition:border-color .15s,background .15s,transform .15s}
.card:hover{border-color:rgba(88,166,255,.5);background:var(--surface2);transform:translateX(2px);text-decoration:none}
.type-ico{flex-shrink:0;width:32px;height:32px;border-radius:8px;border:1px solid;display:flex;
  align-items:center;justify-content:center;font-size:.95rem;line-height:1}
.grow{flex:1;min-width:0}
.card .t{font-weight:600;color:var(--text);font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .d{font-size:.72rem;color:var(--muted);margin-top:2px;font-family:var(--mono);text-transform:capitalize;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chev{flex-shrink:0;color:var(--muted);font-size:1.05rem;opacity:.4;transition:.15s ease}
.card:hover .chev{opacity:1;color:var(--accent);transform:translateX(2px)}
.empty{color:var(--muted);padding:48px 0;text-align:center;font-size:.88rem;font-family:var(--mono)}

.content{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:26px 30px}
.content h2{color:var(--text);font-size:1.2rem;margin:1.1em 0 .5em}
.content table{border-collapse:collapse;margin:12px 0;width:100%}
.content th,.content td{border:1px solid var(--border);padding:6px 11px;text-align:left}
.content code{background:var(--bg);padding:2px 6px;border-radius:5px;font-family:var(--mono);font-size:.85em}
.content pre{background:var(--bg);padding:14px;border-radius:9px;overflow:auto}
.content blockquote{border-left:3px solid var(--border-med);margin:0;padding-left:14px;color:var(--muted)}

.verpager{display:flex;align-items:center;justify-content:space-between;margin-top:16px;padding-top:14px;
  border-top:1px solid var(--border);font-size:.82rem;color:var(--muted);font-family:var(--mono)}
.verpager a{color:var(--accent)}.verpager-cur{color:var(--muted)}

@media (max-width:640px){
  .topbar{padding:.6rem 1rem}
  .topbar-title,.topbar-sep{display:none}
  .wrap{padding:1.25rem 1rem 3rem}
  .board-hdr{flex-direction:column;align-items:stretch;gap:12px}
  .search{min-width:0;width:100%}
  h1{font-size:1.3rem}
  .card{padding:10px 12px;gap:10px}
  .card .t{font-size:.88rem}
  .type-ico{width:28px;height:28px;font-size:.85rem}
  .chev{display:none}
  .select{flex:1;min-width:0}
  .content{padding:18px 16px}
  .verpager{flex-wrap:wrap;gap:8px}
}
</style>`
const esc = s => String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
// `back:false` (index page only) hides the topbar's "← board" link since we're already there.
const page = (t, b, { back = true } = {}) => `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>${esc(t)}</title>${HEAD_FONTS}${CSS}<body>
<div class=topbar><span class=topbar-brand>🗂️ artifacts</span>${back ? `<span class=topbar-sep>/</span><span class=topbar-title>${esc(t)}</span>` : ''}${back ? `<a class=topbar-back href="${BASE}/">← board</a>` : ''}</div>
<div class=wrap>${b}</div>
</body>`
const pretty = n => n.replace(/\.code\.[a-z0-9+#-]+$/i, '').replace(/\.(md|html?|svg|mmd)$/i, '').replace(/^\d{4}-\d{2}-\d{2}[-_]?/, '').replace(/[-_]/g, ' ').trim().replace(/\b\w/g, c => c.toUpperCase()) || n
const when = ms => new Date(ms).toLocaleTimeString('en-CA', { timeStyle: 'short' })
// "Today" / "Yesterday" / "Aug 11, 2026" — groups the board index into date sections instead
// of repeating a full timestamp on every row.
function dayLabel(ms) {
  const d = new Date(ms), now = new Date()
  const midnight = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((midnight(now) - midnight(d)) / 86400000)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Yesterday'
  return d.toLocaleDateString('en-CA', { dateStyle: 'medium' })
}
const safe = p => path.normalize(p).replace(/^(\.\.[/\\])+/, '')
const slug = s => (s || 'untitled').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60)
const langSlug = s => (s || 'text').toLowerCase().replace(/[^a-z0-9+#-]/g, '') || 'text'

// ── per-type styling (color + icon + name), shared by legacy filename-sniffed items and
// new meta.json-backed artifacts ─────────────────────────────────────────────────────────
const TYPE_COLOR = { markdown: '#8b949e', html: '#58a6ff', svg: '#bc8cff', mermaid: '#d29922', code: '#3fb950', react: '#56d4dd', app: '#8b949e' }
const TYPE_LABEL = { markdown: '📄 summary', html: '🌐 html', svg: '🎨 svg', mermaid: '🔀 mermaid', code: '&lt;/&gt; code', react: '⚛️ react', app: '🧩 app' }
const TYPE_ICON = { markdown: '📄', html: '🌐', svg: '🎨', mermaid: '🔀', code: '{ }', react: '⚛️', app: '🧩' }
const TYPE_NAME = { markdown: 'summary', html: 'html', svg: 'svg', mermaid: 'mermaid', code: 'code', react: 'react', app: 'app' }
// compact icon square used on board-index rows — replaces the old text pill
const typeIconHtml = type => {
  const c = TYPE_COLOR[type] || TYPE_COLOR.markdown, i = TYPE_ICON[type] || TYPE_ICON.markdown
  return `<span class=type-ico style="background:${c}1f;color:${c};border-color:${c}40">${i}</span>`
}
// legacy (pre-versioning) content is typed by sniffing its filename/folder — no metadata store
function legacyItemType(kind, name) {
  if (kind === 'app') {
    try { if (fs.readFileSync(path.join(ROOT, 'apps', name, '.type'), 'utf8').trim() === 'react') return 'react' } catch { /* no marker → plain app */ }
    return 'app'
  }
  if (/\.svg$/i.test(name)) return 'svg'
  if (/\.mmd$/i.test(name)) return 'mermaid'
  if (/\.code\.[a-z0-9+#-]+$/i.test(name)) return 'code'
  if (/\.html?$/i.test(name)) return 'html'
  return 'markdown'
}
// ── "who published this" — same sidecar-marker trick as the `.type` react marker above: a
// plain-text file next to (apps: inside) the content, written only if the publisher sent a
// `source` string. One field, not two — it used to be split into source ("which system", e.g.
// "cptr") + computer ("which machine", e.g. "aibo-linux"), but every real caller sends both
// together 1:1, so mcp-tools now composes them into one string ("cptr@aibo-linux") before it
// ever reaches here. Absent sidecar → 'manual' (dropped on disk / published without declaring
// one — same bucket, no way to tell them apart after the fact).
const legacySourcePath = (kind, name) => kind === 'app' ? path.join(ROOT, 'apps', name, '.source') : path.join(ROOT, 'summaries', `${name}.source`)
const readLegacySource = (kind, name) => { try { return fs.readFileSync(legacySourcePath(kind, name), 'utf8').trim() || 'manual' } catch { return 'manual' } }

// ── versioned artifacts (content/artifacts/<id>/meta.json + v1/, v2/, ...) ──────────
// Additive: legacy `summaries/`+`apps/` content (and the /api/publish endpoint that writes
// it) is untouched and keeps serving from its existing URLs forever.
const OPENABLE_TYPES = new Set(['html', 'react', 'app'])   // served raw, no board chrome — "just open it"
const artifactDir = id => path.join(ARTIFACTS_DIR, id)
const newArtifactId = title => `${slug(title)}-${crypto.randomBytes(3).toString('hex')}`
function readMeta(id) {
  try { return JSON.parse(fs.readFileSync(path.join(artifactDir(id), 'meta.json'), 'utf8')) }
  catch { return null }
}
const writeMeta = (id, meta) => fs.writeFileSync(path.join(artifactDir(id), 'meta.json'), JSON.stringify(meta, null, 2))
function versionFile(type, language) {
  if (type === 'svg') return 'content.svg'
  if (type === 'mermaid') return 'content.mmd'
  if (type === 'code') return `content.code.${langSlug(language)}`
  if (OPENABLE_TYPES.has(type)) return 'index.html'
  return 'content.md'
}
const versionPager = (id, n, total) => `<div class=verpager>
${n > 1 ? `<a href="${BASE}/a/${encodeURIComponent(id)}/v/${n - 1}">← v${n - 1}</a>` : '<span></span>'}
<span class=verpager-cur>v${n} of ${total}</span>
${n < total ? `<a href="${BASE}/a/${encodeURIComponent(id)}/v/${n + 1}">v${n + 1} →</a>` : '<span></span>'}
</div>`

function allItems() {
  const out = []
  for (const [kind, sub] of [['summary', 'summaries'], ['app', 'apps']]) {
    const dir = path.join(ROOT, sub)
    for (const name of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
      if (name.startsWith('.') || name.endsWith('.source')) continue   // dotfiles + source sidecars aren't items
      const st = fs.statSync(path.join(dir, name))
      const href = kind === 'app' ? `${BASE}/apps/${encodeURIComponent(name)}/` : `${BASE}/summaries/${encodeURIComponent(name)}`
      out.push({ kind, name: pretty(name), href, type: legacyItemType(kind, name), source: readLegacySource(kind, name), versions: 1, t: st.birthtimeMs || st.mtimeMs })
    }
  }
  for (const id of fs.existsSync(ARTIFACTS_DIR) ? fs.readdirSync(ARTIFACTS_DIR) : []) {
    if (id.startsWith('.')) continue
    const meta = readMeta(id)
    if (!meta || !meta.versions?.length) continue
    const latest = meta.versions[meta.versions.length - 1]
    out.push({ kind: 'artifact', id, name: meta.title, href: `${BASE}/a/${encodeURIComponent(id)}`, type: meta.type, source: meta.source || 'manual', versions: meta.versions.length, t: latest.created })
  }
  return out.sort((a, b) => b.t - a.t)   // most-recent first (an update bumps its artifact back to the top)
}
function indexPage() {
  const items = allItems()
  const types = [...new Set(items.map(i => i.type))]
  // bucket into same-day groups, preserving the newest-first order already on `items`
  const groups = []
  for (const i of items) {
    const label = dayLabel(i.t)
    const g = groups[groups.length - 1]
    if (g && g.label === label) g.items.push(i)
    else groups.push({ label, items: [i] })
  }
  const sources = [...new Set(items.map(i => i.source || 'manual'))].sort()
  const row = i => `<a class=card data-type="${i.type}" data-source="${esc(i.source || 'manual')}" data-q="${esc(i.name.toLowerCase())}" href="${i.href}">${typeIconHtml(i.type)}` +
    `<span class="grow"><div class=t>${esc(i.name)}</div><div class=d>${TYPE_NAME[i.type] || 'summary'} · ${esc(i.source || 'manual')} · ${esc(when(i.t))}${i.versions > 1 ? ` · v${i.versions}` : ''}</div></span>` +
    `<span class=chev>›</span></a>`
  const groupsHtml = groups.map(g => `<div class=group><div class=day-hdr>${esc(g.label)}</div>${g.items.map(row).join('')}</div>`).join('')
  // Plain table-style filters: a search box + two dropdowns, always visible, independent of each
  // other — click a dropdown, pick a value, done. No hidden syntax, nothing that appears/disappears
  // based on how much data exists.
  const typeOpts = `<option value="all">All types</option>` + types.map(t =>
    `<option value="${t}">${esc(TYPE_LABEL[t].replace(/^[^ ]+ /, ''))}</option>`).join('')
  const sourceOpts = `<option value="all">All sources</option>` + sources.map(s =>
    `<option value="${esc(s)}">${esc(s)}</option>`).join('')
  return page('Artifacts', `
<div class=board-hdr><div><h1>Artifacts</h1><p class=sub>${items.length} published, newest first.</p></div>
<input id=search class=search placeholder="Search by name…" oninput="filterBoard()"></div>
<div class=toolbar>
<select id=typeFilter class=select onchange="filterBoard()">${typeOpts}</select>
<select id=sourceFilter class=select onchange="filterBoard()">${sourceOpts}</select>
</div>
<div id=list>${groupsHtml || `<p class=empty>Nothing published yet.</p>`}</div>
<p id=noresults class=empty style="display:none">No matches.</p>
<script>
function filterBoard(){
  var q=(document.getElementById('search').value||'').toLowerCase();
  var type=document.getElementById('typeFilter').value;
  var source=document.getElementById('sourceFilter').value;
  var anyVisible=false;
  document.querySelectorAll('#list .group').forEach(function(g){
    var groupVisible=false;
    g.querySelectorAll('.card').forEach(function(c){
      var okType = type==='all' || c.dataset.type===type;
      var okSource = source==='all' || c.dataset.source===source;
      var okQuery = !q || c.dataset.q.indexOf(q)>-1;
      var show = okType&&okSource&&okQuery;
      c.style.display = show ? '' : 'none';
      if(show) groupVisible=true;
    });
    g.style.display = groupVisible ? '' : 'none';
    if(groupVisible) anyVisible=true;
  });
  document.getElementById('noresults').style.display = anyVisible ? 'none' : '';
}
</script>`, { back: false })
}

const TYPES = {
  '.html': 'text/html', '.htm': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.md': 'text/markdown', '.txt': 'text/plain',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
  '.pdf': 'application/pdf', '.zip': 'application/zip', '.mp4': 'video/mp4', '.mp3': 'audio/mpeg',
  '.wav': 'audio/wav', '.woff': 'font/woff', '.woff2': 'font/woff2', '.csv': 'text/csv',
}

// ── type-specific render helpers (svg/mermaid/code pages share the board's page() chrome —
// the topbar's own "← board" link handles navigation, so these are just the content) ──
const svgPage = raw => `<div class="content" style="text-align:center">${raw}</div>
<details style="margin-top:14px"><summary style="cursor:pointer;color:var(--muted)">View source</summary><pre style="overflow:auto"><code>${esc(raw)}</code></pre></details>`
const mermaidPage = raw => `<div class="content"><div class="mermaid">${esc(raw)}</div></div>
<details style="margin-top:14px"><summary style="cursor:pointer;color:var(--muted)">View source</summary><pre style="overflow:auto"><code>${esc(raw)}</code></pre></details>
<script src="${BASE}/vendor/mermaid.min.js"></script>
<script>mermaid.initialize({startOnLoad:true, theme:'dark'})</script>`
const codePage = (raw, lang) => `<div class="content"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<span style="color:var(--muted);font-size:.8rem;font-family:var(--mono)">${esc(lang)}</span>
<button onclick="navigator.clipboard.writeText(document.getElementById('src').textContent)" style="background:var(--surface2);border:1px solid var(--border-med);color:var(--text);border-radius:6px;padding:4px 10px;cursor:pointer;font-size:.78rem;font-family:var(--mono)">Copy</button>
</div><pre><code id=src class="language-${esc(lang)}">${esc(raw)}</code></pre></div>
<link rel=stylesheet href="${BASE}/vendor/github-dark.min.css">
<script src="${BASE}/vendor/highlight.min.js"></script>
<script>hljs.highlightAll()</script>`
const markdownPage = raw => `<article class=content>${marked.parse(raw)}</article>`

// React artifacts are written as a plain self-running index.html — open it and it runs, no
// preview wrapper/sandbox/chrome. Component must be named `App` (export default is fine too —
// `export default function App(){}` still leaves `App` bound in module scope). React/ReactDOM/
// Babel are vendored (offline-safe); optional extra libraries resolve via the import map below
// and do hit a CDN (esm.sh) — that's the one piece that isn't vendored, expand as needed.
const reactRuntimeHtml = (title, jsx) => `<!doctype html>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>${esc(title)}</title>
<style>html,body{margin:0;background:#0f1115;color:#e6e6e6;font-family:system-ui,-apple-system,sans-serif}#root{min-height:100vh}</style>
<div id="root"></div>
<script src="${BASE}/vendor/react.production.min.js"></script>
<script src="${BASE}/vendor/react-dom.production.min.js"></script>
<script src="${BASE}/vendor/babel.min.js"></script>
<script type="importmap">
{"imports": {
  "recharts": "https://esm.sh/recharts@2?bundle",
  "lucide-react": "https://esm.sh/lucide-react@0.400?bundle",
  "d3": "https://esm.sh/d3@7?bundle"
}}
</script>
<script type="text/babel" data-presets="react" data-type="module">
window.addEventListener('error', e => {
  document.getElementById('root').innerHTML =
    '<pre style="color:#ff6b6b;padding:20px;white-space:pre-wrap">' + (e.error?.stack || e.message) + '</pre>'
})
${jsx}

ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App))
</script>`

function renderVersionBody(meta, raw) {
  if (meta.type === 'svg') return svgPage(raw)
  if (meta.type === 'mermaid') return mermaidPage(raw)
  if (meta.type === 'code') return codePage(raw, meta.language || 'text')
  return markdownPage(raw)   // default / type === 'markdown'
}

const send = (res, code, body, type = 'text/html; charset=utf-8') => { res.writeHead(code, { 'content-type': type }); res.end(body) }

async function readBody(req, max = 5_000_000) {
  let n = 0, chunks = []
  for await (const c of req) { n += c.length; if (n > max) throw new Error('too large'); chunks.push(c) }
  return Buffer.concat(chunks).toString('utf8')
}
const requireToken = (req, res) => {
  if (TOKEN && req.headers['x-publish-token'] === TOKEN) return true
  send(res, 401, JSON.stringify({ error: 'bad token' }), 'application/json')
  return false
}

const server = http.createServer(async (req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0])

  // Requests arriving on the push-only (no-SSO) hostname may ONLY reach the
  // token-gated write routes -- everything else 404s here, before any route
  // below gets a chance to serve it. Requests on any other Host (the SSO'd
  // hostname, or plain container-to-container calls with no Host match) are
  // unaffected and fall through to normal routing.
  const reqHost = (req.headers.host || '').split(':')[0]
  if (reqHost === PUSH_ONLY_HOST) {
    const isPublish = PUSH_ONLY_ROUTES.some(([m, p]) => req.method === m && url === p)
    const isEdit = req.method === 'PUT' && /^\/api\/artifacts\/[^/]+$/.test(url)
    if (!isPublish && !isEdit) {
      return send(res, 404, JSON.stringify({ error: 'not found' }), 'application/json')
    }
  }

  // ── publish API (token-gated, one-shot, no id/versioning) ──
  // POST {title, content, type: markdown|html|svg|mermaid|code|react|app, language?, source?, computer?}
  // `type` is the preferred field. `kind`/`format` (the original md|html|app pair) still work
  // unchanged for back-compat — nothing already calling this API needs to change, forever.
  // `source` (optional, free text — e.g. "cptr", "aibo-mac-pipeline") identifies which system
  // published this; `computer` (optional, e.g. "aibo-linux", "aibo-mac") identifies which
  // physical machine sent it — separate dimensions (a "cptr" publish can come from either
  // machine). Both stored as sidecar markers (same trick as the `.type` react marker), shown on
  // the board and filterable. Omitted → 'manual' / 'unknown' respectively.
  if (req.method === 'POST' && url === '/api/publish') {
    if (!requireToken(req, res)) return
    try {
      const body = JSON.parse(await readBody(req))
      const { title, content = '', language = '', source = '', computer = '' } = body
      const type = body.type || (body.kind === 'app' ? 'app' : body.format === 'html' ? 'html' : 'markdown')
      if (!content) return send(res, 400, JSON.stringify({ error: 'content required' }), 'application/json')
      const date = new Date().toISOString().slice(0, 10)
      const base = `${date}-${slug(title)}`
      let rel
      if (type === 'app' || type === 'react') {
        const dir = path.join(ROOT, 'apps', base)
        fs.mkdirSync(dir, { recursive: true })
        fs.writeFileSync(path.join(dir, 'index.html'), type === 'react' ? reactRuntimeHtml(title, content) : content)
        fs.writeFileSync(path.join(dir, '.type'), type)
        if (source) fs.writeFileSync(path.join(dir, '.source'), source)
        if (computer) fs.writeFileSync(path.join(dir, '.computer'), computer)
        rel = `/apps/${base}/`
      } else if (type === 'svg') {
        fs.writeFileSync(path.join(ROOT, 'summaries', `${base}.svg`), content); rel = `/summaries/${base}.svg`
      } else if (type === 'mermaid') {
        fs.writeFileSync(path.join(ROOT, 'summaries', `${base}.mmd`), content); rel = `/summaries/${base}.mmd`
      } else if (type === 'code') {
        const l = langSlug(language)
        fs.writeFileSync(path.join(ROOT, 'summaries', `${base}.code.${l}`), content); rel = `/summaries/${base}.code.${l}`
      } else if (type === 'html') {
        fs.writeFileSync(path.join(ROOT, 'summaries', `${base}.html`), content); rel = `/summaries/${base}.html`
      } else {
        fs.writeFileSync(path.join(ROOT, 'summaries', `${base}.md`), content); rel = `/summaries/${base}.md`
      }
      if (type !== 'app' && type !== 'react') {
        const disk = path.basename(rel)
        if (source) fs.writeFileSync(path.join(ROOT, 'summaries', `${disk}.source`), source)
        if (computer) fs.writeFileSync(path.join(ROOT, 'summaries', `${disk}.computer`), computer)
      }
      notifyPublish(title || pretty(path.basename(rel)), BASE + rel)   // fire-and-forget push
      return send(res, 200, JSON.stringify({ ok: true, url: BASE + rel }), 'application/json')
    } catch (e) { return send(res, 400, JSON.stringify({ error: String(e.message || e) }), 'application/json') }
  }

  // ── versioned artifacts API (token-gated) ──
  // POST /api/artifacts            {title, content, type?, language?, source?, computer?} → {id, url, version:1}
  // PUT  /api/artifacts/:id        {content, language?, source?, computer?}               → {id, url, version:N}
  // `source`/`computer` are artifact-level (like title/type), not per-version: set on create,
  // optionally reset on update if a different system/machine pushes a new version.
  if (req.method === 'POST' && url === '/api/artifacts') {
    if (!requireToken(req, res)) return
    try {
      const body = JSON.parse(await readBody(req))
      const { title, content = '', language = '', source = '', computer = '' } = body
      const type = body.type || 'markdown'
      if (!title) return send(res, 400, JSON.stringify({ error: 'title required' }), 'application/json')
      if (!content) return send(res, 400, JSON.stringify({ error: 'content required' }), 'application/json')
      const id = newArtifactId(title)
      const now = Date.now()
      const fname = versionFile(type, language)
      fs.mkdirSync(path.join(artifactDir(id), 'v1'), { recursive: true })
      fs.writeFileSync(path.join(artifactDir(id), 'v1', fname), type === 'react' ? reactRuntimeHtml(title, content) : content)
      writeMeta(id, { id, title, type, language: type === 'code' ? langSlug(language) : '', source: source || 'manual', computer: computer || 'unknown', created: now, versions: [{ n: 1, created: now, file: fname }] })
      const rel = `/a/${id}`
      notifyPublish(title, BASE + rel)
      return send(res, 200, JSON.stringify({ ok: true, id, url: BASE + rel, version: 1 }), 'application/json')
    } catch (e) { return send(res, 400, JSON.stringify({ error: String(e.message || e) }), 'application/json') }
  }
  let m = url.match(/^\/api\/artifacts\/([^/]+)$/)
  if (req.method === 'PUT' && m) {
    if (!requireToken(req, res)) return
    try {
      const id = m[1]
      const meta = readMeta(id)
      if (!meta) return send(res, 404, JSON.stringify({ error: 'artifact not found' }), 'application/json')
      const body = JSON.parse(await readBody(req))
      const content = body.content || ''
      const language = body.language || meta.language || ''
      if (!content) return send(res, 400, JSON.stringify({ error: 'content required' }), 'application/json')
      const n = meta.versions.length + 1
      const now = Date.now()
      const fname = versionFile(meta.type, language)
      fs.mkdirSync(path.join(artifactDir(id), `v${n}`), { recursive: true })
      fs.writeFileSync(path.join(artifactDir(id), `v${n}`, fname), meta.type === 'react' ? reactRuntimeHtml(meta.title, content) : content)
      if (meta.type === 'code' && language) meta.language = langSlug(language)
      if (body.source) meta.source = body.source
      if (body.computer) meta.computer = body.computer
      meta.versions.push({ n, created: now, file: fname })
      writeMeta(id, meta)
      const rel = `/a/${id}`
      notifyPublish(`${meta.title} (v${n})`, BASE + rel)
      return send(res, 200, JSON.stringify({ ok: true, id, url: BASE + rel, version: n }), 'application/json')
    } catch (e) { return send(res, 400, JSON.stringify({ error: String(e.message || e) }), 'application/json') }
  }

  // ── multi-file / binary bundle publish (token-gated) — added 2026-08-16 ──
  // POST {title, source?, files:[{path, content_base64}]}
  // The general answer to "an artifact should be able to take a webpage or any file or anything
  // at all": one or more files, each raw-byte-exact. Covers a single arbitrary file (a bundle of
  // one — a PDF, an image, a zip, whatever), and equally a full multi-file webpage export
  // (HTML+CSS+JS+images with relative links preserved) — same mechanism either way.
  //
  // Each file arrives as base64 in the JSON body. Critically, that base64 is expected to be
  // produced by the CALLING PROCESS reading its own local disk (mcp-tools' Python code, or any
  // other script) — never typed out by an LLM mid-conversation. That distinction is the whole
  // point: an earlier attempt to publish a screenshot by having an agent reproduce a ~21,000-
  // character base64 blob as literal generated text silently dropped a character and corrupted
  // the image (see git log / Services/artifacts.md for the writeup). Decoded here with
  // Buffer.from(..., 'base64') — binary-safe, unlike the utf8-forced reads the text-content
  // routes above use — and written byte-exact. Served back by the existing /apps/:id/*rel GET
  // route below, which was already binary-safe (plain Buffer read, no encoding assumption).
  if (req.method === 'POST' && url === '/api/publish-files') {
    if (!requireToken(req, res)) return
    try {
      const body = JSON.parse(await readBody(req, 150_000_000))   // base64 bloats ~33%; generous cap for real binaries/images
      const { title, source = '' } = body
      const files = Array.isArray(body.files) ? body.files : []
      if (!files.length) return send(res, 400, JSON.stringify({ error: 'files required' }), 'application/json')
      const date = new Date().toISOString().slice(0, 10)
      const base = `${date}-${slug(title)}`
      const dir = path.join(ROOT, 'apps', base)
      fs.mkdirSync(dir, { recursive: true })
      let hasIndex = false
      const written = []
      for (const f of files) {
        const rel = safe(String(f.path || '').replace(/^\/+/, ''))
        const dest = path.join(dir, rel)
        // belt-and-suspenders against path traversal: safe() already strips leading ../, this
        // re-checks the resolved path never leaves the bundle dir even via a mid-string ../
        if (!rel || rel.startsWith('..') || (!dest.startsWith(dir + path.sep) && dest !== dir)) {
          return send(res, 400, JSON.stringify({ error: `bad path: ${f.path}` }), 'application/json')
        }
        fs.mkdirSync(path.dirname(dest), { recursive: true })
        fs.writeFileSync(dest, Buffer.from(f.content_base64 || '', 'base64'))
        if (rel === 'index.html') hasIndex = true
        written.push(rel)
      }
      if (!hasIndex && written.length > 1) {
        // no entry point supplied and more than one file — synthesize a plain listing page so
        // the bundle is still directly openable at its base URL instead of a dead end.
        const links = written.map(w => `<li><a href="${encodeURI(w)}">${esc(w)}</a></li>`).join('')
        fs.writeFileSync(path.join(dir, 'index.html'),
          page(title || base, `<div class="content"><h2>${esc(title || base)}</h2><ul>${links}</ul></div>`, { back: false }))
        hasIndex = true
      }
      fs.writeFileSync(path.join(dir, '.type'), 'app')
      if (source) fs.writeFileSync(path.join(dir, '.source'), source)
      // hasIndex (supplied or synthesized) → base URL serves it via the existing default-to-
      // index.html rule below. Otherwise there's exactly one file (see the synthesize branch
      // above) — link straight to it.
      const rel = hasIndex ? `/apps/${base}/` : `/apps/${base}/${encodeURI(written[0])}`
      notifyPublish(title || base, BASE + rel)
      return send(res, 200, JSON.stringify({ ok: true, url: BASE + rel, files: written.length }), 'application/json')
    } catch (e) { return send(res, 400, JSON.stringify({ error: String(e.message || e) }), 'application/json') }
  }

  if (req.method !== 'GET') return send(res, 405, 'method not allowed')

  // ── JSON list API ── GET /api/list → { items:[{kind, name, href, type, source, computer, versions, created}] }
  // (newest first). Powers the OWUI native Artifacts panel + list_artifacts MCP tool.
  if (url === '/api/list') {
    // On the push-only host this is a listed exception (see PUSH_ONLY_ROUTES above) but still
    // requires the token -- everywhere else (SSO'd host, internal container-to-container calls)
    // is unchanged, no token needed, same as before.
    if (reqHost === PUSH_ONLY_HOST && !requireToken(req, res)) return
    const items = allItems().map(i => ({ kind: i.kind, id: i.id, name: i.name, href: i.href, type: i.type, source: i.source, computer: i.computer, versions: i.versions, created: i.t }))
    return send(res, 200, JSON.stringify({ items }), 'application/json')
  }
  m = url.match(/^\/api\/artifacts\/([^/]+)$/)
  if (m) {
    const meta = readMeta(m[1])
    if (!meta) return send(res, 404, JSON.stringify({ error: 'not found' }), 'application/json')
    return send(res, 200, JSON.stringify({ ...meta, url: `${BASE}/a/${meta.id}` }), 'application/json')
  }

  if (url === '/' || url === '') return send(res, 200, indexPage())

  m = url.match(/^\/vendor\/([^/]+)$/)
  if (m) {
    const f = path.join(VENDOR_DIR, safe(m[1]))
    if (!fs.existsSync(f)) return send(res, 404, 'not found')
    return send(res, 200, fs.readFileSync(f), TYPES[path.extname(f)] || 'application/octet-stream')
  }

  // ── versioned artifact viewer — GET /a/:id (latest) or /a/:id/v/:n ──
  // Openable types (html/react/app) are served exactly as written, no chrome — open it, it
  // runs. Rendered types (markdown/svg/mermaid/code) go through the board's page() chrome
  // with a version pager once there's more than one version.
  m = url.match(/^\/a\/([^/]+)(?:\/v\/(\d+))?\/?$/)
  if (m) {
    const meta = readMeta(m[1])
    if (!meta) return send(res, 404, page('Not found', '<p class=empty>Not found</p>'))
    const n = m[2] ? parseInt(m[2], 10) : meta.versions.length
    const ver = meta.versions.find(v => v.n === n)
    if (!ver) return send(res, 404, page('Not found', '<p class=empty>That version doesn\'t exist</p>'))
    const f = path.join(artifactDir(meta.id), `v${n}`, ver.file)
    if (!fs.existsSync(f)) return send(res, 404, page('Not found', '<p class=empty>Not found</p>'))
    const raw = fs.readFileSync(f, 'utf8')
    if (OPENABLE_TYPES.has(meta.type)) return send(res, 200, raw)
    const pager = meta.versions.length > 1 ? versionPager(meta.id, n, meta.versions.length) : ''
    return send(res, 200, page(meta.title, renderVersionBody(meta, raw) + pager))
  }

  m = url.match(/^\/summaries\/(.+)$/)
  if (m) {
    const f = path.join(ROOT, 'summaries', safe(m[1]))
    if (!fs.existsSync(f)) return send(res, 404, page('Not found', '<p class=empty>Not found</p>'))
    const raw = fs.readFileSync(f, 'utf8')
    const name = path.basename(f)
    if (/\.md$/i.test(f)) return send(res, 200, page(pretty(name), markdownPage(raw)))
    if (/\.svg$/i.test(f)) return send(res, 200, page(pretty(name), svgPage(raw)))
    if (/\.mmd$/i.test(f)) return send(res, 200, page(pretty(name), mermaidPage(raw)))
    const codeMatch = /\.code\.([a-z0-9+#-]+)$/i.exec(f)
    if (codeMatch) return send(res, 200, page(pretty(name), codePage(raw, codeMatch[1])))
    return send(res, 200, raw)
  }
  m = url.match(/^\/apps\/([^/]+)(\/.*)?$/)
  if (m) {
    let rel = safe(m[2] || '/'); if (rel === '/' || rel === '') rel = '/index.html'
    const f = path.join(ROOT, 'apps', m[1], rel)
    if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) return send(res, 404, page('Not found', '<p class=empty>Not found</p>'))
    return send(res, 200, fs.readFileSync(f), TYPES[path.extname(f)] || 'application/octet-stream')
  }
  send(res, 404, page('Not found', '<p class=empty>Not found</p>'))
})
server.listen(PORT, () => console.log(`artifacts board on :${PORT} base='${BASE}' publish=${TOKEN ? 'on' : 'off'}`))
