// Renders the REAL floating chat widget (static/templates/common/docs-chatbot.html
// markup: launcher + header + conversation log + input bar) with the panel open,
// so the markdown-rendered answer can be checked for fit inside its actual
// container -- not an isolated bubble on a blank canvas.
import * as marked from "marked";
import DOMPurify from "dompurify";

window.marked = marked;
window.DOMPurify = DOMPurify;

import "../docs-chatbot-view.js";

const { renderAnswerHtml } = window.C360.docsChatbot;

// Mirrors renderSources() in docs-chatbot-view.js (source link chips under an answer).
function sourcesHtml(sources) {
  const items = sources
    .map(
      (s) =>
        `<a class="docs-chat-source" href="#" target="_blank" rel="noopener noreferrer" title="${s.path}">` +
        `<span class="docs-chat-source-doc">${s.title}</span>` +
        (s.heading ? `<span class="docs-chat-source-heading"> — ${s.heading}</span>` : "") +
        `</a>`
    )
    .join("");
  return `<div class="docs-chat-sources-title">Sources</div>${items}`;
}

function fullPanel({ userQuestion, answerMarkdown, sources }) {
  const answerHtml = renderAnswerHtml(answerMarkdown);
  const wrap = document.createElement("div");
  // Storybook's canvas isn't position:fixed-relative to the viewport corner
  // the way the real app page is, so the panel is laid out inline here
  // instead of via its "fixed bottom-5 right-5" production classes -- same
  // width/height/scroll behavior, just positioned for the story canvas.
  wrap.innerHTML = `
    <div id="docs-chat-panel" role="dialog" aria-modal="false" aria-label="Documentation assistant"
      class="w-[min(26rem,calc(100vw-2.5rem))] max-h-[min(38rem,calc(100vh-2.5rem))] flex flex-col rounded-2xl border shadow-2xl overflow-hidden">
      <div class="docs-chat-header-bar flex items-center justify-between gap-3 px-4 py-3 border-b bg-gradient-to-br from-indigo-600 to-indigo-700 text-white">
        <div class="flex items-center gap-2.5 min-w-0">
          <div class="p-1.5 bg-white/15 rounded-lg shrink-0">
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
            </svg>
          </div>
          <div class="min-w-0">
            <p class="text-sm font-bold leading-tight">LEO Assistant</p>
            <p class="text-[11px] text-indigo-100 leading-tight">Grounded in the Customer 360 docs</p>
          </div>
        </div>
        <button type="button" title="Close" class="text-indigo-100 hover:text-white text-xl leading-none shrink-0">&times;</button>
      </div>

      <div id="docs-chat-log" aria-live="polite" class="flex-1 overflow-y-auto px-4 py-4 space-y-4 text-sm">
        <div class="docs-chat-msg docs-chat-msg-bot">
          <div class="docs-chat-bubble docs-chat-bubble-bot">
            <div class="docs-chat-answer">Hi! Ask me anything about the LEO Customer 360 documentation and I'll answer with links to the source pages.</div>
            <div class="mt-3 flex flex-wrap gap-2">
              <button type="button" class="docs-chat-suggestion">Identity resolution</button>
              <button type="button" class="docs-chat-suggestion">Tenant isolation</button>
              <button type="button" class="docs-chat-suggestion">Segments</button>
            </div>
          </div>
        </div>

        <div class="docs-chat-msg docs-chat-msg-user">
          <div class="docs-chat-bubble docs-chat-bubble-user"></div>
        </div>

        <div class="docs-chat-msg docs-chat-msg-bot">
          <div class="docs-chat-bubble docs-chat-bubble-bot">
            <div class="docs-chat-answer"></div>
            <div class="docs-chat-sources"></div>
          </div>
        </div>
      </div>

      <form class="border-t p-3">
        <div class="flex items-end gap-2">
          <textarea rows="1" autocomplete="off" placeholder="Ask a question…  (Enter to send)"
            class="flex-1 resize-none max-h-28 rounded-xl border text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
          <button type="submit" title="Send" class="shrink-0 inline-flex items-center justify-center rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white w-10 h-10 transition-colors">
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
            </svg>
          </button>
        </div>
        <p class="docs-chat-disclaimer mt-2 text-[10px] text-center">Answers are AI-generated from the docs and may be imperfect — verify with the linked sources.</p>
      </form>
    </div>
  `;

  // Text (user question, sources) goes through jQuery's .text() the same way
  // esc() does in docs-chatbot-view.js -- prevents HTML injection from the
  // question itself. The answer is the one spot real HTML is intentionally
  // allowed, via renderAnswerHtml()'s sanitize step.
  $(wrap).find(".docs-chat-bubble-user").text(userQuestion);
  wrap.querySelectorAll(".docs-chat-answer")[1].innerHTML = answerHtml;
  if (sources && sources.length) {
    wrap.querySelector(".docs-chat-sources").innerHTML = sourcesHtml(sources);
  }
  return wrap;
}

export default {
  title: "Docs Chatbot/Full panel (in context)",
  render: (args) => fullPanel(args)
};

export const RealisticConversation = {
  args: {
    userQuestion: "How is tenant isolation enforced?",
    answerMarkdown:
      "## Tenant isolation\n\n" +
      "**Row-Level Security (RLS)** is the platform's only enforced tenant boundary. " +
      "Every pooled connection must run:\n\n" +
      "```sql\nSELECT set_config('app.tenant_id', '<uuid>', true);\n```\n\n" +
      "Key points:\n" +
      "1. Never use a `BYPASSRLS` role for tenant-facing traffic.\n" +
      "2. Multi-tenant batch jobs must reset the tenant context before each operation.\n\n" +
      "> Several code paths bypass or misuse RLS, creating potential cross-tenant data exposure.",
    sources: [
      { title: "Customer 360 Data Source Types Specification", path: "architecture/data-sources.md", heading: "Multi-Tenant Security & Isolation Model" },
      { title: "Python Services Code Review", path: "code-review/python-services.md", heading: "1. Executive summary" }
    ]
  }
};

export const LongTableAnswer = {
  args: {
    userQuestion: "What fields are on a master profile?",
    answerMarkdown:
      "The `cdp_master_profiles` table has these key fields:\n\n" +
      "| Field | Type | Notes |\n" +
      "|---|---|---|\n" +
      "| tenant_id | uuid | required, RLS key |\n" +
      "| status | text | active / churned |\n" +
      "| lifecycle_stage | text | prospect, lead, customer, VIP |\n" +
      "| created_at | timestamptz | UTC |",
    sources: [{ title: "Customer 360 Database Schema", path: "database/schema.md", heading: "" }]
  }
};

// Stress test: a table wide enough to overflow the 26rem panel, to check
// whether it breaks the panel layout or scrolls contained within its box.
export const WideTableOverflowCheck = {
  args: {
    userQuestion: "Compare the data source connector fields across providers.",
    answerMarkdown:
      "| Source | Connector Type | Auth Method | Rate Limit | Sync Interval | Bucket/Endpoint | Status |\n" +
      "|---|---|---|---|---|---|---|\n" +
      "| Adjust Mobile Attribution | mobile/sdk | API key | 1000/min | 15 minutes | automate.adjust.com/reports-service | active |\n" +
      "| Google Analytics 4 | web/sdk | OAuth2 service account | 500/min | 30 minutes | analyticsdata.googleapis.com | active |\n" +
      "| OneSignal Journey Events | webhook | HMAC signature | 2000/min | real-time | data-tracking-25bcc577.example | active |",
    sources: []
  }
};
