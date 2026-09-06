import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
// @ts-ignore — Quartz bundles the .inline script for the browser and hands us the source string.
import script from "./scripts/docs-chatbot.inline"
// @ts-ignore — Quartz's esbuild sass loader turns this into the compiled CSS string.
import style from "./styles/docs-chatbot.scss"

// Baked at build time (SSR, Node). The browser inline script can't read process.env, so we
// pass the public API base + site base through data-* attributes on the marker element.
// One knob per env: set DOCS_AI_PUBLIC_URL in CI to point at the right docs API.
const API_BASE = process.env.DOCS_AI_PUBLIC_URL || "https://beta.leocdp.com/docs-ai"
const SITE_BASE = process.env.DOCS_SITE_BASE || "https://leo-cdp.github.io/leo-customer360"

const DocsChatbot: QuartzComponentConstructor = () => {
  const Chatbot: QuartzComponent = ({ displayClass }: QuartzComponentProps) => {
    // Just a config marker — the inline script builds the launcher + panel once and
    // appends them to <body> (so they survive SPA navigation).
    return (
      <div
        id="docs-chatbot-root"
        class={`docs-chatbot ${displayClass ?? ""}`}
        data-api={API_BASE}
        data-site={SITE_BASE}
      ></div>
    )
  }
  Chatbot.afterDOMLoaded = script
  Chatbot.css = style
  return Chatbot
}

export default DocsChatbot
