"use strict";
// Unit tests for the pure markdown->HTML conversion used by the docs chatbot
// answer bubble (static/js/docs-chatbot-view.js -> renderAnswerHtml). This is
// logic-only: no jQuery DOM mounting, no live LLM/API calls. Run with:
//   npm install && npm test
const test = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const marked = require("marked");
const createDOMPurify = require("dompurify");

// Minimal jQuery stand-in for esc()'s `$("<div>").text(value).html()` pattern
// (only exercised by the marked/DOMPurify-unavailable fallback branch).
function fakeJQuery() {
  return {
    _text: "",
    text: function (v) {
      this._text = String(v);
      return this;
    },
    html: function () {
      return this._text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }
  };
}

// (Re)loads docs-chatbot-view.js against fresh global.window/marked/DOMPurify/$
// stubs, mirroring how index.html loads it as a plain <script> against real
// browser globals, and returns the C360.docsChatbot export.
function loadDocsChatbot(options) {
  var withMarkdownLibs = !options || options.withMarkdownLibs !== false;

  global.window = {};
  global.$ = fakeJQuery;

  if (withMarkdownLibs) {
    var jsdomWindow = new JSDOM("").window;
    var DOMPurify = createDOMPurify(jsdomWindow);
    global.marked = marked;
    global.DOMPurify = DOMPurify;
    global.window.marked = marked;
    global.window.DOMPurify = DOMPurify;
  } else {
    delete global.marked;
    delete global.DOMPurify;
  }

  delete require.cache[require.resolve("../docs-chatbot-view.js")];
  require("../docs-chatbot-view.js");
  return global.window.C360.docsChatbot;
}

test("renders **bold** markdown to <strong>", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("This is **bold** text.");
  assert.match(html, /<strong>bold<\/strong>/);
});

test("renders headings, lists, inline code, and links", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml(
    "# Title\n\n- one\n- two\n\nSee `code` and [a link](https://example.com)."
  );
  assert.match(html, /<h1[^>]*>Title<\/h1>/);
  assert.match(html, /<li>one<\/li>/);
  assert.match(html, /<li>two<\/li>/);
  assert.match(html, /<code>code<\/code>/);
  assert.match(html, /<a href="https:\/\/example\.com"/);
});

test("renders fenced code blocks as <pre><code>", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("```\nconst x = 1;\n```");
  assert.match(html, /<pre>[\s\S]*<code>[\s\S]*const x = 1;[\s\S]*<\/code>[\s\S]*<\/pre>/);
});

test("keeps the language tag on fenced code blocks (```bash, ```sql)", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml(
    "```bash\nnpm install\nnpm test\n```\n\n```sql\nSELECT * FROM cdp_master_profiles WHERE tenant_id = $1;\n```"
  );
  assert.match(html, /<pre><code class="language-bash">npm install\nnpm test\n<\/code><\/pre>/);
  assert.match(
    html,
    /<pre><code class="language-sql">SELECT \* FROM cdp_master_profiles WHERE tenant_id = \$1;\n<\/code><\/pre>/
  );
});

test("sanitizes a <script> tag out of the LLM answer", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("Hello <script>alert(1)</script> world");
  assert.doesNotMatch(html, /<script/i);
  assert.match(html, /Hello/);
});

test("strips an inline event-handler attribute (XSS)", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml('<img src="x" onerror="alert(1)">');
  assert.doesNotMatch(html, /onerror/i);
});

test("falls back to escaped text + <br> when marked/DOMPurify are unavailable", () => {
  const { renderAnswerHtml } = loadDocsChatbot({ withMarkdownLibs: false });
  const html = renderAnswerHtml("line one\nline two <b>not bold</b>");
  assert.equal(html, "line one<br>line two &lt;b&gt;not bold&lt;/b&gt;");
});

test("treats null/undefined input as an empty string", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  assert.equal(renderAnswerHtml(null), "");
  assert.equal(renderAnswerHtml(undefined), "");
});

test("renders *italic* and _italic_ as <em>", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("This is *italic* and _also italic_.");
  assert.match(html, /This is <em>italic<\/em> and <em>also italic<\/em>\./);
});

test("renders ~~strikethrough~~ as <del> (GFM)", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("~~deprecated field~~");
  assert.match(html, /<del>deprecated field<\/del>/);
});

test("renders an ordered list as <ol><li>", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("1. first\n2. second\n3. third");
  assert.match(html, /<ol>\s*<li>first<\/li>\s*<li>second<\/li>\s*<li>third<\/li>\s*<\/ol>/);
});

test("renders a blockquote as <blockquote>", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("> RLS is the platform's only enforced tenant boundary.");
  assert.match(html, /<blockquote>\s*<p>RLS is the platform's only enforced tenant boundary\.<\/p>\s*<\/blockquote>/);
});

test("renders a GFM table as <table><thead>/<tbody>", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml(
    "| Field | Type |\n|---|---|\n| tenant_id | uuid |\n| status | text |"
  );
  assert.match(html, /<table>/);
  assert.match(html, /<th>Field<\/th>\s*<th>Type<\/th>/);
  assert.match(html, /<td>tenant_id<\/td>\s*<td>uuid<\/td>/);
  assert.match(html, /<td>status<\/td>\s*<td>text<\/td>/);
});

test("renders multiple paragraphs as separate <p> tags", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("First paragraph.\n\nSecond paragraph.");
  assert.match(html, /<p>First paragraph\.<\/p>\s*<p>Second paragraph\.<\/p>/);
});

test("renders h2/h3 sub-headings", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("## Section\n### Subsection");
  assert.match(html, /<h2>Section<\/h2>/);
  assert.match(html, /<h3>Subsection<\/h3>/);
});

test("autolinks a bare URL", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const html = renderAnswerHtml("See https://leo-cdp.github.io/leo-customer360/ for details.");
  assert.match(html, /<a href="https:\/\/leo-cdp\.github\.io\/leo-customer360\/">/);
});

test("renders a realistic grounded RAG answer with mixed markdown and bracketed citations", () => {
  const { renderAnswerHtml } = loadDocsChatbot();
  const answer =
    "## Tenant isolation\n\n" +
    "**Row-Level Security (RLS)** is the platform's only enforced tenant boundary. " +
    "Every pooled connection must run:\n\n" +
    "```sql\nSELECT set_config('app.tenant_id', '<uuid>', true);\n```\n\n" +
    "Key points:\n" +
    "1. Never use a `BYPASSRLS` role for tenant-facing traffic.\n" +
    "2. Multi-tenant batch jobs must reset the tenant context before each operation.\n\n" +
    "> Several code paths bypass or misuse RLS, creating potential cross-tenant data exposure. " +
    "[Python Services Code Review — 1. Executive summary]";
  const html = renderAnswerHtml(answer);

  assert.match(html, /<h2>Tenant isolation<\/h2>/);
  assert.match(html, /<strong>Row-Level Security \(RLS\)<\/strong>/);
  assert.match(html, /<pre><code class="language-sql">SELECT set_config/);
  assert.match(html, /<li>Never use a <code>BYPASSRLS<\/code> role/);
  assert.match(html, /<blockquote>/);
  // The bracketed source citation is plain text, not markdown -- must survive untouched.
  assert.match(html, /\[Python Services Code Review — 1\. Executive summary\]/);
  assert.doesNotMatch(html, /<script/i);
});
