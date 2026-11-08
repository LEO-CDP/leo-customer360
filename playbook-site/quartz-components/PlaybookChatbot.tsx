import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
// The widget is shared with the docs site (docs-site/quartz-components); build.sh copies both files in.
// @ts-ignore — Quartz bundles the .inline script for the browser and hands us the source string.
import script from "./scripts/docs-chatbot.inline"
// @ts-ignore — Quartz's esbuild sass loader turns this into the compiled CSS string.
import style from "./styles/docs-chatbot.scss"

// Same-origin: Caddy maps POST /ai/ask on playbook.leocdp.com to the playbook-scoped /playbook/ask,
// so there is no CORS and no host baked in. Source paths carry "playbooks/" that is not in the URL.
const SUGGESTIONS = [
  { q: "Playbook này giúp doanh nghiệp thương mại điện tử làm gì?", label: "Thương mại điện tử" },
  { q: "Phân khúc khách hàng (segment) dùng để làm gì?", label: "Phân khúc khách hàng" },
]

const PlaybookChatbot: QuartzComponentConstructor = () => {
  const Chatbot: QuartzComponent = ({ displayClass }: QuartzComponentProps) => (
    <div
      id="docs-chatbot-root"
      class={`docs-chatbot ${displayClass ?? ""}`}
      data-api="/ai"
      data-lang="vi"
      data-page-base=""
      data-strip="playbooks/"
      data-suggestions={JSON.stringify(SUGGESTIONS)}
    ></div>
  )
  Chatbot.afterDOMLoaded = script
  Chatbot.css = style
  return Chatbot
}

export default PlaybookChatbot
