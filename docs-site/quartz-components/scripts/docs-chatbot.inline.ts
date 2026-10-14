// Docs Assistant (RAG chatbot) for the Quartz docs site.
//
// Runs in the browser (Quartz afterDOMLoaded). Reads the public API base + site base
// baked into #docs-chatbot-root's data-* attributes (see DocsChatbot.tsx), builds a
// floating launcher + panel appended to <body> ONCE, and wires one call per question:
//   POST {api}/ask -> grounded answer, shown together with only the sources it used.
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

  // Page URL of a source, with its section as the anchor so the link lands on the cited part.
  // Same slug rule as the site's heading ids: lower case, punctuation dropped, each space a hyphen.
  const sourceUrl = (path: string, heading?: string, title?: string): string => {
    // README.md is its own page on the site (docs/<dir>/README); docs/<dir>/ is only a folder listing.
    const slug = String(path || "")
      .replace(/\.md$/i, "")
      .split("/")
      .map(encodeURIComponent)
      .join("/")
    const last = String(heading || "").split(" > ").pop()!.trim()
    const id = !last || last === title ? "" : last.toLowerCase().replace(/[^\p{L}\p{M}\p{N} _-]/gu, "").replace(/ /g, "-")
    return `${siteBase}/docs/${slug}${id ? "#" + encodeURIComponent(id) : ""}`
  }

  // The answer is Markdown. Escape first, then turn the few constructs the model uses into HTML
  // (bold, inline code, bullet and numbered lists, paragraphs), so no model text becomes markup.
  const mdToHtml = (text: string): string => {
    const inline = (t: string) =>
      esc(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>")
    const out: string[] = []
    let list: "ul" | "ol" | null = null
    const close = () => {
      if (list) out.push(`</${list}>`)
      list = null
    }
    for (const line of String(text ?? "").split("\n")) {
      const m = line.match(/^\s*(?:([-*])|(\d+)\.)\s+(.*)$/)
      if (m) {
        const kind = m[1] ? "ul" : "ol"
        if (list !== kind) {
          close()
          out.push(`<${kind}>`)
          list = kind
        }
        out.push(`<li>${inline(m[3])}</li>`)
      } else {
        close()
        if (line.trim()) out.push(`<p>${inline(line)}</p>`)
      }
    }
    close()
    return out.join("")
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

  // A long answer should open at its first line: scroll to the top of the message when it is taller
  // than the log, else to the bottom.
  const reveal = (el: HTMLElement) => {
    if (el.offsetHeight <= log.clientHeight) return scroll()
    log.scrollTop += el.getBoundingClientRect().top - log.getBoundingClientRect().top - 8
  }

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
    ;(bot.querySelector(".docs-chat-answer") as HTMLElement).innerHTML = mdToHtml(text)
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
          return `<a class="docs-chat-source" href="${esc(sourceUrl(s.path, s.heading, s.title))}" title="${esc(s.path)}"><span class="docs-chat-source-doc">${esc(label)}</span>${sub ? `<span class="docs-chat-source-heading">${esc(sub)}</span>` : ""}</a>`
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
  const SLOW_HINT_MS = 8000

  // retry: the question is already in the log (the failed answer was removed), so do not add it again.
  const ask = async (question: string, retry = false) => {
    question = String(question || "").trim()
    if (!question) return
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    if (!retry) addUser(question)
    const bot = addBot()
    setStatus(bot, "Searching the docs and writing the answer…", true)
    // The answer can take several seconds: say so instead of looking frozen.
    const slow = setTimeout(() => setStatus(bot, "Still working… this is taking longer than usual.", true), SLOW_HINT_MS)

    try {
      // One call. Sources are shown only with the answer, and only the ones it used: listing the
      // search candidates first showed unrelated pages, and kept them when the answer was "I don't know".
      const a = await post("/ask", { question }, signal)
      setStatus(bot, "", false)
      setAnswer(bot, a?.answer || "I couldn't find an answer in the documentation.")
      setSources(bot, a?.found === false ? [] : (a?.sources ?? []))
      reveal(bot)
      input.focus()
    } catch (err: any) {
      if (err?.name === "AbortError") return
      setStatus(bot, "", false)
      const detail = err?.status
        ? `The documentation service returned an error (HTTP ${err.status}).`
        : "Could not reach the documentation assistant. It may be starting up or offline."
      ;(bot.querySelector(".docs-chat-answer") as HTMLElement).innerHTML =
        `<span class="docs-chat-error">${esc(detail)}</span><div><button type="button" class="docs-chat-retry" data-q="${esc(question)}">Try again</button></div>`
    } finally {
      clearTimeout(slow)
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
    input.style.height = ""
    ask(q)
  }

  launcher.addEventListener("click", () => (panel.classList.contains("hidden") ? open() : close()))
  panel.querySelector("#docs-chat-close")!.addEventListener("click", close)
  panel.querySelector("#docs-chat-form")!.addEventListener("submit", (e) => {
    e.preventDefault()
    submit()
  })
  // The textarea grows with what is typed (up to its max-height).
  input.addEventListener("input", () => {
    input.style.height = "auto"
    input.style.height = input.scrollHeight + "px"
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
    if (t.classList.contains("docs-chat-retry")) {
      t.closest(".docs-chat-msg")?.remove() // drop the failed answer, then ask the same question again
      ask(t.dataset.q || "", true)
    }
  })
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.classList.contains("hidden")) close()
  })
}

init()
