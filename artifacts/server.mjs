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
const PUBLIC_BASE = process.env.PUBLIC_BASE || 'https://apps.kingdomofluna.com'
// Pangolin resource for this hostname has SSO off (other systems need to push
// without an interactive login) -- so this process must enforce on its own
// that the push-only hostname can ONLY reach the token-gated write routes.
// Everything else (viewing the board, listing content) stays exclusively on
// the SSO-protected hostname. Mirrors the register.dispatch.* pattern.
const PUSH_ONLY_HOST = process.env.PUSH_ONLY_HOST || 'push.artifacts.kingdomofluna.com'
const PUSH_ONLY_ROUTES = [
  ['POST', '/api/publish'],
  ['POST', '/api/artifacts'],
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

.chips{display:flex;gap:6px;margin-bottom:20px;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.chips::-webkit-scrollbar{display:none}
.chip{flex-shrink:0;font-family:var(--mono);background:var(--surface2);border:1px solid var(--border);color:var(--muted);
  border-radius:999px;padding:.4rem .9rem;font-size:.72rem;font-weight:600;cursor:pointer;white-space:nowrap;transition:.15s}
.chip:hover{border-color:var(--border-med);color:var(--text)}
.chip.active{background:rgba(88,166,255,.12);border-color:rgba(88,166,255,.4);color:var(--accent)}

.card{display:flex;align-items:center;gap:14px;padding:14px 16px;margin:8px 0;background:var(--surface);
  border:1px solid var(--border);border-radius:10px;transition:border-color .15s,background .15s,transform .15s}
.card:hover{border-color:rgba(88,166,255,.5);background:var(--surface2);transform:translateX(2px);text-decoration:none}
.badge{font-family:var(--mono);font-size:.62rem;font-weight:700;letter-spacing:.03em;padding:.22rem .6rem;
  border-radius:999px;border:1px solid;white-space:nowrap;flex-shrink:0}
.grow{flex:1;min-width:0}
.card .t{font-weight:600;color:var(--text);font-size:.92rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .d{font-size:.75rem;color:var(--muted);margin-top:2px;font-family:var(--mono)}
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
  .card{padding:12px 14px;gap:10px}
  .card .t{font-size:.88rem}
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
const when = ms => new Date(ms).toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'short' })
const safe = p => path.normalize(p).replace(/^(\.\.[/\\])+/, '')
const slug = s => (s || 'untitled').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60)
const langSlug = s => (s || 'text').toLowerCase().replace(/[^a-z0-9+#-]/g, '') || 'text'

// ── per-type badge (color + label), shared by legacy filename-sniffed items and new
// meta.json-backed artifacts ─────────────────────────────────────────────────────────
const TYPE_COLOR = { markdown: '#8b949e', html: '#58a6ff', svg: '#bc8cff', mermaid: '#d29922', code: '#3fb950', react: '#56d4dd', app: '#8b949e' }
const TYPE_LABEL = { markdown: '📄 summary', html: '🌐 html', svg: '🎨 svg', mermaid: '🔀 mermaid', code: '&lt;/&gt; code', react: '⚛️ react', app: '🧩 app' }
const badgeHtml = type => {
  const c = TYPE_COLOR[type] || TYPE_COLOR.markdown, l = TYPE_LABEL[type] || TYPE_LABEL.markdown
  return `<span class=badge style="background:${c}1f;color:${c};border-color:${c}40">${l}</span>`
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
      if (name.startsWith('.')) continue
      const st = fs.statSync(path.join(dir, name))
      const href = kind === 'app' ? `${BASE}/apps/${encodeURIComponent(name)}/` : `${BASE}/summaries/${encodeURIComponent(name)}`
      out.push({ kind, name: pretty(name), href, type: legacyItemType(kind, name), versions: 1, t: st.birthtimeMs || st.mtimeMs })
    }
  }
  for (const id of fs.existsSync(ARTIFACTS_DIR) ? fs.readdirSync(ARTIFACTS_DIR) : []) {
    if (id.startsWith('.')) continue
    const meta = readMeta(id)
    if (!meta || !meta.versions?.length) continue
    const latest = meta.versions[meta.versions.length - 1]
    out.push({ kind: 'artifact', id, name: meta.title, href: `${BASE}/a/${encodeURIComponent(id)}`, type: meta.type, versions: meta.versions.length, t: latest.created })
  }
  return out.sort((a, b) => b.t - a.t)   // most-recent first (an update bumps its artifact back to the top)
}
function indexPage() {
  const items = allItems()
  const types = [...new Set(items.map(i => i.type))]
  const rows = items.map(i =>
    `<a class=card data-type="${i.type}" data-q="${esc(i.name.toLowerCase())}" href="${i.href}">${badgeHtml(i.type)}` +
    `<span class="grow"><div class=t>${esc(i.name)}</div><div class=d>${esc(when(i.t))}${i.versions > 1 ? ` · v${i.versions}` : ''}</div></span></a>`
  ).join('')
  const chips = ['all', ...types].map(t =>
    `<button class="chip${t === 'all' ? ' active' : ''}" data-filter="${t}">${t === 'all' ? 'All' : TYPE_LABEL[t].replace(/^[^ ]+ /, '')}</button>`
  ).join('')
  return page('Artifacts', `
<div class=board-hdr><div><h1>Artifacts</h1><p class=sub>${items.length} published, newest first.</p></div>
<input id=search class=search placeholder="Filter by name…" oninput="filterBoard()"></div>
<div class=chips id=chips>${chips}</div>
<div id=list>${rows || `<p class=empty>Nothing published yet.</p>`}</div>
<script>
function filterBoard(){
  var q=(document.getElementById('search').value||'').toLowerCase();
  var active=document.querySelector('.chip.active').dataset.filter;
  document.querySelectorAll('#list .card').forEach(function(c){
    var okType = active==='all' || c.dataset.type===active;
    var okQuery = !q || c.dataset.q.indexOf(q)>-1;
    c.style.display = (okType&&okQuery) ? '' : 'none';
  });
}
document.getElementById('chips').addEventListener('click', function(e){
  var b=e.target.closest('.chip'); if(!b) return;
  document.querySelectorAll('.chip').forEach(function(x){x.classList.remove('active')});
  b.classList.add('active');
  filterBoard();
});
</script>`, { back: false })
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.md': 'text/markdown' }

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
  // POST {title, content, type: markdown|html|svg|mermaid|code|react|app, language?}
  // `type` is the preferred field. `kind`/`format` (the original md|html|app pair) still work
  // unchanged for back-compat — nothing already calling this API needs to change, forever.
  if (req.method === 'POST' && url === '/api/publish') {
    if (!requireToken(req, res)) return
    try {
      const body = JSON.parse(await readBody(req))
      const { title, content = '', language = '' } = body
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
      notifyPublish(title || pretty(path.basename(rel)), BASE + rel)   // fire-and-forget push
      return send(res, 200, JSON.stringify({ ok: true, url: BASE + rel }), 'application/json')
    } catch (e) { return send(res, 400, JSON.stringify({ error: String(e.message || e) }), 'application/json') }
  }

  // ── versioned artifacts API (token-gated) ──
  // POST /api/artifacts            {title, content, type?, language?} → {id, url, version:1}
  // PUT  /api/artifacts/:id        {content, language?}               → {id, url, version:N}
  if (req.method === 'POST' && url === '/api/artifacts') {
    if (!requireToken(req, res)) return
    try {
      const body = JSON.parse(await readBody(req))
      const { title, content = '', language = '' } = body
      const type = body.type || 'markdown'
      if (!title) return send(res, 400, JSON.stringify({ error: 'title required' }), 'application/json')
      if (!content) return send(res, 400, JSON.stringify({ error: 'content required' }), 'application/json')
      const id = newArtifactId(title)
      const now = Date.now()
      const fname = versionFile(type, language)
      fs.mkdirSync(path.join(artifactDir(id), 'v1'), { recursive: true })
      fs.writeFileSync(path.join(artifactDir(id), 'v1', fname), type === 'react' ? reactRuntimeHtml(title, content) : content)
      writeMeta(id, { id, title, type, language: type === 'code' ? langSlug(language) : '', created: now, versions: [{ n: 1, created: now, file: fname }] })
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
      meta.versions.push({ n, created: now, file: fname })
      writeMeta(id, meta)
      const rel = `/a/${id}`
      notifyPublish(`${meta.title} (v${n})`, BASE + rel)
      return send(res, 200, JSON.stringify({ ok: true, id, url: BASE + rel, version: n }), 'application/json')
    } catch (e) { return send(res, 400, JSON.stringify({ error: String(e.message || e) }), 'application/json') }
  }

  if (req.method !== 'GET') return send(res, 405, 'method not allowed')

  // ── JSON list API ── GET /api/list → { items:[{kind, name, href, type, versions, created}] }
  // (newest first). Powers the OWUI native Artifacts panel + list_artifacts MCP tool.
  if (url === '/api/list') {
    const items = allItems().map(i => ({ kind: i.kind, id: i.id, name: i.name, href: i.href, type: i.type, versions: i.versions, created: i.t }))
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
