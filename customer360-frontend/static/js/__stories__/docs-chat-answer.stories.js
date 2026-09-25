// Visual stories for the docs chatbot's answer bubble. These render the same
// markup appendAssistant()/setAnswer() build in docs-chatbot-view.js, through
// the SAME renderAnswerHtml() function used in production, with the real
// app.css loaded (see .storybook/preview.js) -- not a mock, not a re-implementation.
import * as marked from "marked";
import DOMPurify from "dompurify";

// docs-chatbot-view.js is a plain browser script that reads these off
// `window` the first time renderAnswerHtml() runs -- same contract as
// index.html loading marked/DOMPurify via <script> before this file.
window.marked = marked;
window.DOMPurify = DOMPurify;

import "../docs-chatbot-view.js";

const { renderAnswerHtml } = window.C360.docsChatbot;

function chatBubble(markdown) {
  const wrap = document.createElement("div");
  wrap.style.maxWidth = "380px";
  wrap.innerHTML = `
    <div class="docs-chat-msg docs-chat-msg-bot">
      <div class="docs-chat-bubble docs-chat-bubble-bot">
        <div class="docs-chat-answer"></div>
      </div>
    </div>
  `;
  wrap.querySelector(".docs-chat-answer").innerHTML = renderAnswerHtml(markdown);
  return wrap;
}

export default {
  title: "Docs Chatbot/Answer bubble",
  render: (args) => chatBubble(args.markdown),
  argTypes: {
    markdown: { control: "text" }
  }
};

export const BasicFormatting = {
  args: {
    markdown:
      "**Row-Level Security (RLS)** is the platform's only enforced tenant boundary. " +
      "It is *always* on for tenant tables, never ~~optional~~."
  }
};

export const HeadingsAndLists = {
  args: {
    markdown:
      "## Tenant isolation\n\n" +
      "Key points:\n\n" +
      "1. Never use a `BYPASSRLS` role for tenant-facing traffic.\n" +
      "2. Multi-tenant batch jobs must reset the tenant context before each operation.\n\n" +
      "Related docs:\n\n" +
      "- Identity resolution\n" +
      "- Segmentation\n" +
      "- Analytics"
  }
};

export const CodeBlocks = {
  args: {
    markdown:
      "Set the tenant context before every query:\n\n" +
      "```sql\nSELECT set_config('app.tenant_id', '<uuid>', true);\n```\n\n" +
      "Then install the DAO locally:\n\n" +
      "```bash\n./customer360-dao/install-local.sh --service customer360-api --requirements\n```"
  }
};

export const Table = {
  args: {
    markdown:
      "| Field | Type | Notes |\n" +
      "|---|---|---|\n" +
      "| tenant_id | uuid | required, RLS key |\n" +
      "| status | text | active / churned |\n" +
      "| created_at | timestamptz | UTC |"
  }
};

export const Blockquote = {
  args: {
    markdown:
      "> Several code paths bypass or misuse RLS, creating potential cross-tenant " +
      "data exposure. [Python Services Code Review — 1. Executive summary]"
  }
};

// Closest to a real grounded RAG answer: mixed heading/bold/code/list/quote
// plus a bracketed source citation, which must survive as plain text.
export const RealisticGroundedAnswer = {
  args: {
    markdown:
      "## Tenant isolation\n\n" +
      "**Row-Level Security (RLS)** is the platform's only enforced tenant boundary. " +
      "Every pooled connection must run:\n\n" +
      "```sql\nSELECT set_config('app.tenant_id', '<uuid>', true);\n```\n\n" +
      "Key points:\n" +
      "1. Never use a `BYPASSRLS` role for tenant-facing traffic.\n" +
      "2. Multi-tenant batch jobs must reset the tenant context before each operation.\n\n" +
      "> Several code paths bypass or misuse RLS, creating potential cross-tenant data " +
      "exposure. [Python Services Code Review — 1. Executive summary]"
  }
};

// The LLM answer is untrusted text -- this demonstrates the sanitize step
// actually strips a script tag / event handler rather than rendering it.
export const MaliciousInputIsSanitized = {
  args: {
    markdown:
      "Here is the answer <script>alert('xss')</script> with a bad image " +
      '<img src="x" onerror="alert(1)"> mixed in.'
  }
};
