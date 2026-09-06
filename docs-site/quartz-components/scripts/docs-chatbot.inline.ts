// Docs Assistant (RAG chatbot) for the Quartz docs site.
//
// Runs in the browser (Quartz afterDOMLoaded). Reads the public API base + site base
// baked into #docs-chatbot-root's data-* attributes (see DocsChatbot.tsx), builds a
// floating launcher + panel appended to <body> ONCE, and wires the two-phase flow:
//   POST {api}/search -> instant source docs, then POST {api}/ask -> grounded answer.
// Only one question in flight at a time (AbortController). All rendered text is escaped.

interface Source {
  path: string
  title?: string
  heading?: string
}

const FLAG = "__docsChatbotReady"

function init() {
  // Build the widget once; it lives in <body> so it survives Quartz SPA navigation.
  if ((window as any)[FLAG]) return
  const marker = document.getElementById("docs-chatbot-root")
  const apiBase = (marker?.dataset.api || "").replace(/\/$/, "")
  const siteBase = (marker?.dataset.site || "").replace(/\/$/, "")
  if (!apiBase) return // no config -> do nothing
  ;(window as any)[FLAG] = true

  let controller: AbortController | null = null

  // ---- helpers -------------------------------------------------------------
  const esc = (v: unknown): string => {
    const d = document.createElement("div")
    d.textContent = v == null ? "" : String(v)
    return d.innerHTML
  }

  const sourceUrl = (path: string): string => {
    const slug = String(path || "")
      .replace(/\.md$/i, "")
      .replace(/\/README$/i, "/")
      .replace(/^README$/i, "")
    return `${siteBase}/docs/${slug}`
  }

  const dedupe = (items: Source[]): Source[] => {
    const seen = new Set<string>()
    const out: Source[] = []
    for (const s of items || []) {
      if (s && s.path && !seen.has(s.path)) {
        seen.add(s.path)
        out.push(s)
      }
    }
    return out
  }

  // ---- DOM -----------------------------------------------------------------
  const launcher = document.createElement("button")
  launcher.id = "docs-chat-launcher"
  launcher.type = "button"
  launcher.setAttribute("aria-expanded", "false")
  launcher.title = "Ask the Docs"
  launcher.innerHTML = `<span class="docs-chat-launcher-icon">💬</span><span>Ask the Docs</span>`

  const panel = document.createElement("div")
  panel.id = "docs-chat-panel"
  panel.className = "hidden"
  panel.setAttribute("role", "dialog")
  panel.setAttribute("aria-label", "Documentation assistant")
  panel.innerHTML = `
    <div id="docs-chat-header">
      <div>
        <p class="docs-chat-title">Docs Assistant</p>
        <p class="docs-chat-subtitle">Grounded in the LEO Customer 360 docs</p>
      </div>
      <button id="docs-chat-close" type="button" title="Close" aria-label="Close">&times;</button>
    </div>
    <div id="docs-chat-log" aria-live="polite">
      <div class="docs-chat-msg docs-chat-bot">
        <div class="docs-chat-bubble">
          Hi! Ask a question about the documentation and I'll answer with links to the source pages.
          <div class="docs-chat-suggestions">
            <button type="button" class="docs-chat-suggestion" data-q="How does identity resolution merge two profiles?">Identity resolution</button>
            <button type="button" class="docs-chat-suggestion" data-q="How is tenant isolation enforced?">Tenant isolation</button>
          </div>
        </div>
      </div>
    </div>
    <form id="docs-chat-form">
      <textarea id="docs-chat-input" rows="1" placeholder="Ask a question…  (Enter to send)"></textarea>
      <button id="docs-chat-send" type="submit" title="Send" aria-label="Send">➤</button>
    </form>
    <p class="docs-chat-disclaimer">AI-generated from the docs — verify with the linked sources.</p>`

  document.body.appendChild(launcher)
  document.body.appendChild(panel)

  const log = panel.querySelector("#docs-chat-log") as HTMLElement
  const input = panel.querySelector("#docs-chat-input") as HTMLTextAreaElement

  const scroll = () => (log.scrollTop = log.scrollHeight)

  // ---- rendering -----------------------------------------------------------
  const addUser = (text: string) => {
    log.insertAdjacentHTML(
      "beforeend",
      `<div class="docs-chat-msg docs-chat-user"><div class="docs-chat-bubble">${esc(text)}</div></div>`,
    )
    scroll()
  }

  const addBot = (): HTMLElement => {
    const el = document.createElement("div")
    el.className = "docs-chat-msg docs-chat-bot"
    el.innerHTML = `<div class="docs-chat-bubble"><div class="docs-chat-answer"></div><div class="docs-chat-status"></div><div class="docs-chat-sources"></div></div>`
    log.appendChild(el)
    scroll()
    return el
  }

  const setStatus = (bot: HTMLElement, text: string, busy: boolean) => {
    const s = bot.querySelector(".docs-chat-status") as HTMLElement
    s.innerHTML = text
      ? `${busy ? '<span class="docs-chat-typing"><span></span><span></span><span></span></span>' : ""}<span>${esc(text)}</span>`
      : ""
    scroll()
  }

  const setAnswer = (bot: HTMLElement, text: string) => {
    ;(bot.querySelector(".docs-chat-answer") as HTMLElement).innerHTML = esc(text).replace(/\n/g, "<br>")
    scroll()
  }

  const setSources = (bot: HTMLElement, sources: Source[]) => {
    const list = dedupe(sources)
    const box = bot.querySelector(".docs-chat-sources") as HTMLElement
    if (!list.length) {
      box.innerHTML = ""
      return
    }
    box.innerHTML =
      `<div class="docs-chat-sources-title">Sources</div>` +
      list
        .map((s) => {
          const label = s.title || s.path
          const sub = s.heading && s.heading !== s.title ? ` — ${s.heading}` : ""
          return `<a class="docs-chat-source" href="${esc(sourceUrl(s.path))}" title="${esc(s.path)}"><span class="docs-chat-source-doc">${esc(label)}</span>${sub ? `<span class="docs-chat-source-heading">${esc(sub)}</span>` : ""}</a>`
        })
        .join("")
    scroll()
  }

  const post = async (path: string, body: unknown, signal: AbortSignal) => {
    const r = await fetch(apiBase + path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal,
    })
    if (!r.ok) {
      const e: any = new Error("HTTP " + r.status)
      e.status = r.status
      throw e
    }
    return r.json()
  }

  // ---- conversation --------------------------------------------------------
  const ask = async (question: string) => {
    question = String(question || "").trim()
    if (!question) return
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    addUser(question)
    const bot = addBot()
    setStatus(bot, "Searching the docs…", true)

    try {
      try {
        const s = await post("/search", { query: question, top_n: 6 }, signal)
        setSources(bot, s?.hits ?? [])
        setStatus(bot, "Generating answer…", true)
      } catch (err: any) {
        if (err?.name === "AbortError") throw err
        setStatus(bot, "Generating answer…", true) // search failed -> still try /ask
      }
      const a = await post("/ask", { question }, signal)
      setStatus(bot, "", false)
      setAnswer(bot, a?.answer || "I couldn't find an answer in the documentation.")
      if (a?.sources?.length) setSources(bot, a.sources)
    } catch (err: any) {
      if (err?.name === "AbortError") return
      setStatus(bot, "", false)
      const detail = err?.status
        ? `The documentation service returned an error (HTTP ${err.status}).`
        : "Could not reach the documentation assistant. It may be starting up or offline."
      ;(bot.querySelector(".docs-chat-answer") as HTMLElement).innerHTML =
        `<span class="docs-chat-error">${esc(detail)} Please try again.</span>`
    } finally {
      controller = null
    }
  }

  // ---- events --------------------------------------------------------------
  const open = () => {
    panel.classList.remove("hidden")
    launcher.setAttribute("aria-expanded", "true")
    setTimeout(() => input.focus(), 50)
  }
  const close = () => {
    panel.classList.add("hidden")
    launcher.setAttribute("aria-expanded", "false")
  }
  const submit = () => {
    const q = input.value
    if (!q.trim()) return
    input.value = ""
    ask(q)
  }

  launcher.addEventListener("click", () => (panel.classList.contains("hidden") ? open() : close()))
  panel.querySelector("#docs-chat-close")!.addEventListener("click", close)
  panel.querySelector("#docs-chat-form")!.addEventListener("submit", (e) => {
    e.preventDefault()
    submit()
  })
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  })
  panel.addEventListener("click", (e) => {
    const t = e.target as HTMLElement
    if (t.classList.contains("docs-chat-suggestion")) ask(t.dataset.q || "")
  })
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.classList.contains("hidden")) close()
  })
}

init()
