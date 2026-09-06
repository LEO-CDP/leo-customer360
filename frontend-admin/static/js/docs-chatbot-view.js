/* Customer 360 Admin -- Docs Assistant (RAG chatbot).
 *
 * A floating "Ask the Docs" widget available on every tab. It calls the
 * same-origin /ai/* proxy (app.py), which forwards to tools/docs-vector-search
 * (semantic search + grounded question answering over the docs corpus).
 *
 * UX is two-phase because /ask runs a local LLM on a small box and is slow:
 *   1. POST /ai/search  -> render the top source docs immediately (sub-second).
 *   2. POST /ai/ask     -> replace with the grounded answer + reconciled sources.
 *
 * Only one question is in flight at a time -- a new question aborts the previous
 * request (single-inflight via AbortController).
 *
 * Registered like the other view modules: this file attaches C360.docsChatbot,
 * main.js injects the template into #docs-chatbot-root and calls bindEvents(). */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var controller = null; // AbortController for the in-flight question, or null

  // --- helpers -----------------------------------------------------------------

  // Escape untrusted text (LLM answer, titles, headings, paths) before it touches
  // the DOM. Everything rendered below goes through this -- never raw .html().
  function esc(value) {
    return $("<div>").text(value == null ? "" : String(value)).html();
  }

  // sources[].path is relative to the corpus root (docs/), e.g.
  // "architecture/identity.md". Map it to the public docs-site page URL.
  function sourceUrl(path) {
    var slug = String(path || "")
      .replace(/\.md$/i, "")
      .replace(/\/README$/i, "/")
      .replace(/^README$/i, "");
    var base = String(C360.config.current.docsSiteBase || "").replace(/\/$/, "");
    return base + "/docs/" + slug;
  }

  function dedupeByPath(items) {
    var seen = {};
    var out = [];
    (items || []).forEach(function (item) {
      if (item && item.path && !seen[item.path]) {
        seen[item.path] = true;
        out.push(item);
      }
    });
    return out;
  }

  function scrollToBottom() {
    var log = document.getElementById("docs-chat-log");
    if (log) log.scrollTop = log.scrollHeight;
  }

  // --- rendering ---------------------------------------------------------------

  function appendUser(text) {
    $("#docs-chat-log").append(
      '<div class="docs-chat-msg docs-chat-msg-user">' +
        '<div class="docs-chat-bubble docs-chat-bubble-user">' + esc(text) + "</div>" +
      "</div>"
    );
    scrollToBottom();
  }

  // Returns the assistant message element so the async flow can update its parts.
  function appendAssistant() {
    var $msg = $(
      '<div class="docs-chat-msg docs-chat-msg-bot">' +
        '<div class="docs-chat-bubble docs-chat-bubble-bot">' +
          '<div class="docs-chat-answer"></div>' +
          '<div class="docs-chat-status"></div>' +
          '<div class="docs-chat-sources"></div>' +
        "</div>" +
      "</div>"
    );
    $("#docs-chat-log").append($msg);
    scrollToBottom();
    return $msg;
  }

  function setStatus($msg, text, busy) {
    var $status = $msg.find(".docs-chat-status");
    if (!text) {
      $status.empty().addClass("hidden");
      return;
    }
    $status.removeClass("hidden").html(
      (busy ? '<span class="docs-chat-typing"><span></span><span></span><span></span></span>' : "") +
      '<span class="docs-chat-status-text">' + esc(text) + "</span>"
    );
    scrollToBottom();
  }

  function setAnswer($msg, text) {
    // Escaped first, then \n -> <br> so paragraphs survive without allowing markup.
    $msg.find(".docs-chat-answer").html(esc(text).replace(/\n/g, "<br>"));
    scrollToBottom();
  }

  function renderSources($msg, sources) {
    var list = dedupeByPath(sources);
    var $box = $msg.find(".docs-chat-sources");
    if (!list.length) {
      $box.empty();
      return;
    }
    var html = '<div class="docs-chat-sources-title">Sources</div>';
    list.forEach(function (src) {
      var label = src.title || src.path;
      var sub = src.heading && src.heading !== src.title ? " — " + src.heading : "";
      html +=
        '<a class="docs-chat-source" href="' + esc(sourceUrl(src.path)) + '"' +
        ' target="_blank" rel="noopener noreferrer" title="' + esc(src.path) + '">' +
        '<span class="docs-chat-source-doc">' + esc(label) + "</span>" +
        (sub ? '<span class="docs-chat-source-heading">' + esc(sub) + "</span>" : "") +
        "</a>";
    });
    $box.html(html);
    scrollToBottom();
  }

  function renderError($msg, err) {
    setStatus($msg, "", false);
    var detail = err && err.status
      ? "The documentation service returned an error (HTTP " + err.status + ")."
      : "Could not reach the documentation assistant. It may be starting up or offline.";
    $msg.find(".docs-chat-answer").html(
      '<span class="docs-chat-error">' + esc(detail) + " Please try again.</span>"
    );
    scrollToBottom();
  }

  // --- conversation ------------------------------------------------------------

  function ask(question) {
    question = String(question || "").trim();
    if (!question) return;

    if (controller) controller.abort(); // supersede any in-flight question
    controller = ("AbortController" in window) ? new AbortController() : null;
    var signal = controller ? controller.signal : undefined;

    appendUser(question);
    var $msg = appendAssistant();
    setStatus($msg, "Searching the docs…", true);

    // Phase 1: fast retrieval for an immediate source list.
    C360.config.docsSearch(question, 8, signal)
      .then(function (res) {
        renderSources($msg, res && res.hits);
        setStatus($msg, "Generating answer…", true);
      })
      .catch(function (err) {
        // Abort must stop the whole flow; other search failures are non-fatal --
        // we still try /ask, which returns its own sources.
        if (err && err.name === "AbortError") throw err;
        setStatus($msg, "Generating answer…", true);
      })
      // Phase 2: the grounded answer.
      .then(function () {
        return C360.config.docsAsk(question, signal);
      })
      .then(function (res) {
        setStatus($msg, "", false);
        setAnswer($msg, (res && res.answer) || "I couldn't find an answer in the documentation.");
        if (res && res.sources && res.sources.length) renderSources($msg, res.sources);
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return; // a newer question took over
        renderError($msg, err);
      })
      .then(function () {
        controller = null;
      });
  }

  // --- panel open/close --------------------------------------------------------

  function isOpen() {
    return !$("#docs-chat-panel").hasClass("hidden");
  }

  function open() {
    $("#docs-chat-panel").removeClass("hidden");
    $("#docs-chat-launcher").attr("aria-expanded", "true");
    setTimeout(function () { $("#docs-chat-input").trigger("focus"); }, 50);
  }

  function close() {
    $("#docs-chat-panel").addClass("hidden");
    $("#docs-chat-launcher").attr("aria-expanded", "false");
  }

  function toggle() {
    if (isOpen()) close(); else open();
  }

  function submitFromInput() {
    var $input = $("#docs-chat-input");
    var question = $input.val();
    if (!String(question || "").trim()) return;
    $input.val("");
    ask(question);
  }

  function bindEvents() {
    $("#docs-chat-launcher").on("click", toggle);
    $("#docs-chat-close").on("click", close);

    $("#docs-chat-form").on("submit", function (e) {
      e.preventDefault();
      submitFromInput();
    });

    // Enter sends; Shift+Enter inserts a newline (textarea default).
    $("#docs-chat-input").on("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitFromInput();
      }
    });

    // Suggestion chips (data-q) prime the input and send immediately.
    $(document).on("click", ".docs-chat-suggestion", function () {
      ask($(this).data("q"));
    });

    // Esc closes the panel when it's open and focused.
    $(document).on("keydown", function (e) {
      if (e.key === "Escape" && isOpen()) close();
    });
  }

  C360.docsChatbot = {
    bindEvents: bindEvents,
    ask: ask,
    open: open,
    close: close,
    toggle: toggle
  };
})(window.C360);
