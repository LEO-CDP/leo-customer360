/* Customer 360 Admin -- Docs Assistant (RAG chatbot).
 *
 * A floating "Ask the Docs" widget available on every tab. It calls the
 * same-origin /ai/* proxy (app.py), which forwards to tools/docs-vector-search
 * (semantic search + grounded question answering over the docs corpus).
 *
 * UX is two-phase for the first question of a chat because the answer is slow:
 *   1. POST /ai/search        -> render the top source docs immediately (sub-second).
 *   2. POST /assistant/ask    -> replace with the grounded answer + the sources it used.
 * Later questions of a chat go straight to step 2 (a follow-up means nothing searched alone).
 *
 * Memory has two tiers. Short-term: the chat of this browser tab (sessionStorage, per page and
 * object: the conversation id, and whether "New chat" was pressed), which the server turns into
 * the recent messages the model sees. Long-term: the server keeps the chat (masked, 30 days,
 * author only) and GET /assistant/conversation brings the latest one back when a new session
 * opens the panel on the same page and object. A different page or object starts a fresh chat.
 *
 * Only one question is in flight at a time: Send is disabled until the answer arrives.
 *
 * Registered like the other view modules: this file attaches C360.docsChatbot,
 * main.js injects the template into #docs-chatbot-root and calls bindEvents(). */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var STORAGE_KEY = "c360.assistant.chat";
  var SESSION_MAX_CHATS = 20;
  var greetingHtml = null; // the initial log (greeting + chips), restored by "New chat" and on a page change
  // chat: which page/object the log shows (key), the server's conversation id, whether "New chat"
  // was pressed in this tab (so a reload stays empty), a counter that invalidates late answers.
  var chat = { key: null, id: null, fresh: false, gen: 0 };
  var busy = false; // a question is waiting for its answer

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
          '<div class="docs-chat-asked docs-chat-basis hidden"></div>' +
          '<div class="docs-chat-missing docs-chat-basis hidden"></div>' +
          '<div class="docs-chat-basis docs-chat-basis-line hidden"></div>' +
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

  // Pure text->HTML conversion, factored out of setAnswer so it can be unit
  // tested without jQuery/DOM (see static/js/__tests__/docs-chatbot-view.test.js).
  function renderAnswerHtml(text) {
    var raw = text == null ? "" : String(text);
    if (window.marked && window.DOMPurify) {
      // The LLM answer is markdown; render it, then sanitize before it touches
      // the DOM (this is the one place we allow real HTML, via DOMPurify).
      return DOMPurify.sanitize(marked.parse(raw));
    }
    // marked/DOMPurify failed to load -- fall back to the old escape+<br> behavior
    // rather than risk unsanitized markup.
    return esc(raw).replace(/\n/g, "<br>");
  }

  function setAnswer($msg, text) {
    $msg.find(".docs-chat-answer").html(renderAnswerHtml(text));
    scrollToBottom();
  }

  // "Based on: this segment's details · this page's guide": what screen context the answer used.
  // Level 2: on-screen facts arrive as screen_data with the loader's screen_label; the profile
  // page (screen_data without a label, or the older profile_data only) still reads
  // "this customer's profile". Empty when the service did not say (sources only).
  function basisText(basis) {
    var parts = [];
    if (basis && (basis.screen_data || basis.profile_data)) {
      parts.push((basis.screen_label && String(basis.screen_label)) || "this customer's profile");
    }
    if (basis && basis.page_guide) parts.push("this page's guide");
    return parts.length ? "Based on: " + parts.join(" · ") : "";
  }

  function renderBasis($msg, basis) {
    var text = basisText(basis);
    $msg.find(".docs-chat-basis-line").text(text).toggleClass("hidden", !text);
  }

  // A partly answered question (status "partial") names what the docs do not cover, so the user can
  // see why the answer stops short.
  function missingText(res) {
    var missing = res && res.status === "partial" && res.missing ? res.missing.filter(Boolean) : [];
    return missing.length ? "Not covered in the docs: " + missing.join("; ") : "";
  }

  function renderMissing($msg, res) {
    var text = missingText(res);
    $msg.find(".docs-chat-missing").text(text).toggleClass("hidden", !text);
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

  // --- page context ------------------------------------------------------------

  // Suggestions per page, keyed by the router's route pattern (the page key the API and the docs
  // page cards use too). Only questions the page card can answer belong here.
  var PAGE_CHIPS = {
    "/overview": [
      "What can I do on this page?",
      "Summarise these numbers",
      "How many customers do we have?",
      "Is data still being processed?",
      "Where does our data come from?"
    ],
    "/analytics": [
      "What can I do on this page?",
      "Summarise these numbers",
      "How many events happened?",
      "Which device do customers use most?",
      "Why is Conversions empty?"
    ],
    "/profiles": [
      "What can I do on this page?",
      "How do I find one customer?",
      'What does "Linked Profiles" mean?',
      "How do I narrow to VIPs or at-risk customers?"
    ],
    "/segments": [
      "What can I do on this page?",
      "How do I create a segment?",
      "Why is Matched Profiles 0?",
      "Can I write the rules in plain language?"
    ],
    "/segments/:id": [
      "What can I do on this page?",
      "Why is this segment's audience size what it is?",
      "Explain this segment's rules",
      "Why would a segment's audience size be 0?",
      "What is the Agent Workflow for?"
    ],
    "/personas": [
      "What can I do on this page?",
      "What is a persona archetype?",
      "How do I see who matches a persona?",
      "Why is Avg Confidence 0%?"
    ],
    "/personas/:archetypeId/matched-profiles": [
      "What can I do on this page?",
      "Summarise this persona",
      "What are the centroid component scores?",
      "Why would a persona have no matched profiles?",
      "How do I change a persona?"
    ],
    "/campaigns": [
      "What can I do on this page?",
      "What do the campaign statuses mean?",
      "What does ROAS mean?",
      "How do I change a campaign?"
    ],
    "/campaigns/:id": [
      "What can I do on this page?",
      "How is this campaign doing?",
      "Why can't I approve or reject?",
      "What do the approval statuses mean?",
      "Which variant is winning?"
    ],
    "/campaigns/:id/edit": [
      "What can I do on this page?",
      "How is this campaign doing?",
      "How do I create a new campaign?",
      "Why can't I save a campaign?",
      "What happens to an approved campaign when I edit it?"
    ],
    "/datasources": [
      "What can I do on this page?",
      "How do I add a website tracking source?",
      "What do the source types mean?",
      "Where do I get the tracking code or webhook example?"
    ],
    "/attributes": [
      "What can I do on this page?",
      "What does the PII flag do?",
      "What does Segmentable mean?",
      "What are CIR and Priority?"
    ],
    "/agent": [
      "What can I do on this page?",
      "How do I add an agent?",
      "What is the difference between Active and Training?",
      "Where do I set the run schedule?"
    ],
    "/admin/users": [
      "What can I do on this page?",
      "How do I add a staff user?",
      "How do I remove someone's access?",
      "Can I reset a password here?"
    ],
    "/profiles/:id": [
      "How valuable is this customer?",
      "What is this customer's churn risk?",
      "What should I do next with this customer?",
      "What can I do on this page?",
      "Where do I change a customer's lifecycle stage or tier?"
    ]
  };
  // Shown as "You are on: <label>"; same names as the page cards in docs/pages/.
  var PAGE_LABELS = {
    "/overview": "Overview dashboard",
    "/analytics": "Analytics dashboard",
    "/profiles": "Master profiles",
    "/profiles/:id": "Customer profile",
    "/segments": "Segments",
    "/segments/:id": "Segment detail",
    "/personas": "Personas",
    "/personas/:archetypeId/matched-profiles": "Persona matched profiles",
    "/campaigns": "Campaigns",
    "/campaigns/:id": "Campaign detail",
    "/campaigns/:id/edit": "Campaign editor",
    "/datasources": "Data sources",
    "/attributes": "Attribute catalog",
    "/agent": "AI agents",
    "/admin/users": "System users"
  };
  var defaultChipsHtml = null;

  // Whitespace-collapse a label (the tab/dialog text can contain nested markup and newlines).
  function collapse(value) {
    return String(value == null ? "" : value).replace(/\s+/g, " ").trim();
  }

  // The visible selected tab. Its data-assistant-label is the title only (added by the page
  // templates); until that lands, fall back to the tab's own text.
  function viewLabel() {
    var $tab = $('[role="tab"][aria-selected="true"]:visible').first();
    if (!$tab.length) return "";
    var label = collapse($tab.attr("data-assistant-label")) || collapse($tab.text());
    return label.slice(0, 60);
  }

  // Title of an open dialog: the text of the element(s) it points at with aria-labelledby
  // (space-separated ids), else its aria-label.
  function dialogTitle($dialog) {
    var ids = collapse($dialog.attr("aria-labelledby"));
    if (!ids) return "";
    return ids.split(" ").map(function (id) {
      var el = document.getElementById(id);
      return el ? $(el).text() : "";
    }).join(" ");
  }

  // The dialog in front of the user, if any: visible [role="dialog"] elements that are not the
  // assistant panel itself. Several visible at once -> the last in DOM order (the topmost).
  function dialogLabel() {
    var $dialog = $('[role="dialog"]:visible').filter(function () {
      return !$(this).closest("#docs-chatbot-root").length;
    }).last();
    if (!$dialog.length) return "";
    var label = collapse(dialogTitle($dialog)) || collapse($dialog.attr("aria-label"));
    return label.slice(0, 80);
  }

  // The route's id parameter identifies the object on screen (Level 2 contract). The campaign
  // editor's id is "new" when creating, which is not an entity. Keys come from the pattern's
  // ":name" parts (see common/router.js compilePattern/matchPath).
  function entityIdFor(pattern, params) {
    if (!params) return "";
    if (pattern === "/segments/:id") return params.id || "";
    if (pattern === "/campaigns/:id" || pattern === "/campaigns/:id/edit") {
      return params.id === "new" ? "" : (params.id || "");
    }
    if (pattern === "/personas/:archetypeId/matched-profiles") return params.archetypeId || "";
    if (pattern === "/profiles/:id") return params.id || "";
    return "";
  }

  var UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  // The selected period on the two dashboards; the views read the same selects (analytics.js,
  // overview-view.js). getDataPeriodDays returns an integer (90 when nothing parses), so the
  // range check is what keeps a stray value out of the request.
  var PERIOD_SELECTORS = {
    "/overview": "#overview-period-select",
    "/analytics": "#analytics-period-select"
  };

  // Where the user is: the route pattern, the profile id on a profile page, the id of the
  // object on screen, the selected dashboard period, the visible sub-tab, and the open dialog.
  // Read when the question is sent, so it is never stale.
  function pageContext() {
    var loc = C360.router && C360.router.current && C360.router.current();
    var pattern = loc && loc.route && loc.route.pattern;
    if (!pattern) return {};
    var ctx = { page: pattern };
    var entityId = entityIdFor(pattern, loc.params);
    if (entityId && UUID_RE.test(entityId)) ctx.entity_id = entityId;
    // Only a well-formed id is sent: a malformed one (hand-edited URL) would be a 422 and no answer.
    if (pattern === "/profiles/:id" && loc.params && UUID_RE.test(loc.params.id || "")) ctx.master_profile_id = loc.params.id;
    var periodSelector = PERIOD_SELECTORS[pattern];
    if (periodSelector && C360.config && C360.config.getDataPeriodDays) {
      var days = C360.config.getDataPeriodDays(periodSelector);
      if (days >= 1 && days <= 400) ctx.period_days = days;
    }
    var view = viewLabel();
    if (view) ctx.view = view;
    var dialog = dialogLabel();
    if (dialog) ctx.dialog = dialog;
    return ctx;
  }

  // "Segment detail · Agent Workflow · Create segment": the page, then the part in front of the
  // user (selected tab, open dialog). Kept short; shown as "You are on: <line>".
  function contextLine(ctx) {
    ctx = ctx || pageContext();
    var parts = [];
    var base = PAGE_LABELS[ctx.page] || ctx.page;
    if (base) parts.push(base);
    if (ctx.view && ctx.view !== base && parts.indexOf(ctx.view) === -1) parts.push(ctx.view);
    if (ctx.dialog && parts.indexOf(ctx.dialog) === -1) parts.push(ctx.dialog);
    return parts.join(" · ");
  }

  // Same page and same object? An answer that arrives after the user has moved on is still shown (it
  // answers the question above it) but says where it was asked, so it is not mistaken for the new page.
  function contextKey(ctx) {
    return [ctx.page || "", ctx.entity_id || "", ctx.master_profile_id || ""].join("|");
  }

  function renderAskedOn($msg, askedKey, askedLabel) {
    var moved = contextKey(pageContext()) !== askedKey;
    $msg.find(".docs-chat-asked").text(moved && askedLabel ? "Asked on: " + askedLabel : "").toggleClass("hidden", !(moved && askedLabel));
  }

  function renderContext(ctx) {
    var $label = $("#docs-chat-context");
    var text = contextLine(ctx);
    if (!text) {
      $label.addClass("hidden").empty();
      return;
    }
    $label.removeClass("hidden").text("You are on: " + text);
  }

  // Swap the greeting's chips for the ones that fit the current page (or restore the generic ones).
  function renderChips() {
    var $first = $("#docs-chat-log .docs-chat-suggestion").first();
    if (!$first.length) return; // the greeting is gone
    var $box = $first.parent();
    if (defaultChipsHtml === null) defaultChipsHtml = $box.html();
    var ctx = pageContext();
    var chips = PAGE_CHIPS[ctx.page];
    if (!chips) {
      $box.html(defaultChipsHtml);
      $("#docs-chat-context").addClass("hidden").empty();
      return;
    }
    $box.html(chips.map(function (q) {
      return '<button type="button" class="docs-chat-suggestion" data-q="' + esc(q) + '">' + esc(q) + "</button>";
    }).join(""));
    renderContext(ctx);
  }

  // --- conversation ------------------------------------------------------------

  // The key the server uses for a chat: the page plus the object on screen (master_profile_id on
  // the profile page, entity_id on any other id page). pageContext sets both on the profile page,
  // so master_profile_id || entity_id mirrors the API's "master_profile_id or entity_id".
  function chatKey(ctx) {
    return (ctx.page || "") + "|" + (ctx.master_profile_id || ctx.entity_id || "");
  }

  // Short-term memory: this tab's state per page/object, {key: {id, fresh}}. Session storage may be
  // blocked or throw (private windows); the chat is then simply not remembered across a reload.
  function readSessionMap() {
    try {
      return JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || "null") || {};
    } catch (e) {
      return {};
    }
  }

  function saveSession() {
    try {
      var map = readSessionMap();
      delete map[chat.key]; // re-insert last so the oldest entries are the ones dropped
      map[chat.key] = { id: chat.id, fresh: chat.fresh };
      Object.keys(map).slice(0, -SESSION_MAX_CHATS).forEach(function (k) { delete map[k]; });
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(map));
    } catch (e) { /* not remembered */ }
  }

  // Empty the log back to the greeting (with the chips of the current page) and drop late answers.
  function resetLog() {
    chat.gen += 1;
    setBusy(false);
    if (greetingHtml === null) greetingHtml = $("#docs-chat-log").html();
    else $("#docs-chat-log").html(greetingHtml);
    renderChips();
  }

  function renderSaved(messages) {
    messages.forEach(function (m) {
      if (m.role === "user") {
        appendUser(m.text);
        return;
      }
      var $msg = appendAssistant();
      setAnswer($msg, m.text);
      if (m.sources && m.sources.length) renderSources($msg, m.sources);
    });
  }

  // Make the log match the page and object the user is on: a different one starts a fresh chat; a
  // chat opened in a new session is brought back from the server unless "New chat" was pressed here.
  function syncChat() {
    var ctx = pageContext();
    var key = chatKey(ctx);
    if (key === chat.key) return;
    var stored = readSessionMap()[key] || {};
    chat.key = key;
    chat.id = stored.id || null;
    chat.fresh = stored.fresh === true;
    resetLog();
    saveSession();
    if (chat.fresh) return;
    var gen = chat.gen;
    C360.config.assistantConversation(ctx)
      .then(function (res) {
        if (gen !== chat.gen || busy || !res || !res.messages || !res.messages.length) return;
        chat.id = res.conversation_id || null;
        saveSession();
        renderSaved(res.messages);
      })
      .catch(function () { /* nothing to restore; start empty */ });
  }

  function newChat() {
    var ctx = pageContext();
    chat.key = chatKey(ctx);
    chat.id = null;
    chat.fresh = true;
    resetLog();
    saveSession();
    $("#docs-chat-input").trigger("focus");
  }

  // --- asking ------------------------------------------------------------------

  function setBusy(value) {
    busy = value;
    $("#docs-chat-send").prop("disabled", value).toggleClass("opacity-50 cursor-not-allowed", value);
  }

  function ask(question) {
    question = String(question || "").trim();
    if (!question || busy) return;

    syncChat(); // the user may have changed page since the panel was opened
    var gen = chat.gen;
    var context = pageContext();
    renderContext(context); // keep the visible "You are on" line in step with what we send
    var askedKey = contextKey(context);
    var askedLabel = contextLine(context);
    var firstOfChat = !chat.id;

    setBusy(true);
    appendUser(question);
    var $msg = appendAssistant();
    setStatus($msg, firstOfChat ? "Searching the docs…" : "Generating answer…", true);

    // Phase 1 (first question of a chat only): fast retrieval for an immediate source list.
    var early = firstOfChat
      ? C360.config.docsSearch(question, 8)
          .then(function (res) {
            if (gen === chat.gen) renderSources($msg, res && res.hits);
          })
          .catch(function () { /* non-fatal: the answer brings its own sources */ })
      : $.Deferred().resolve().promise();

    // Phase 2: the grounded answer, with the conversation the server keeps.
    early
      .then(function () {
        if (gen === chat.gen) setStatus($msg, "Generating answer…", true);
        return C360.config.assistantAsk(question, context, chat.id);
      })
      .then(function (res) {
        if (gen !== chat.gen) return; // the page or object changed: this answer belongs to another chat
        setBusy(false);
        setStatus($msg, "", false);
        if (res && res.conversation_id) {
          chat.id = res.conversation_id;
          chat.fresh = false;
          saveSession();
        }
        setAnswer($msg, (res && res.answer) || "I couldn't find an answer in the documentation.");
        // The final list is only the documents the answer used (possibly none, e.g. an answer from
        // the customer's profile alone), so it replaces the early candidates either way. A refusal or
        // a clarifying question is shown without sources.
        if (res && res.found === false) renderSources($msg, []);
        else if (res && res.sources) renderSources($msg, res.sources);
        renderBasis($msg, res && res.found !== false ? res.basis : null);
        renderMissing($msg, res);
        renderAskedOn($msg, askedKey, askedLabel);
      })
      .catch(function (err) {
        if (gen !== chat.gen) return;
        setBusy(false);
        renderError($msg, err);
      });
  }

  // --- panel open/close --------------------------------------------------------

  function isOpen() {
    return !$("#docs-chat-panel").hasClass("hidden");
  }

  function open() {
    syncChat();
    renderChips();
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
    if (busy || !String(question || "").trim()) return; // keep what was typed while an answer is pending
    $input.val("");
    ask(question);
  }

  function bindEvents() {
    $("#docs-chat-launcher").on("click", toggle);
    $("#docs-chat-close").on("click", close);
    $("#docs-chat-new").on("click", newChat);
    greetingHtml = $("#docs-chat-log").html();

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

    // Follow the user from page to page while the panel is open. Deferred so the router's own
    // hashchange handler (which sets the new route) has run before the context is read.
    $(window).on("hashchange", function () {
      if (isOpen()) setTimeout(function () { syncChat(); renderChips(); }, 0);
    });

    // Esc closes the panel. Bound on the panel first so a keypress inside it stops before the
    // document-level Escape handlers views use to close their dialogs -- otherwise Esc in the
    // panel input would close the dialog underneath as well.
    $("#docs-chat-panel").on("keydown", function (e) {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      close();
    });

    // Esc elsewhere on the page still closes the panel, but not while the focus is inside a
    // modal dialog: that dialog owns the keypress, so the panel stays open for the user.
    $(document).on("keydown", function (e) {
      if (e.key !== "Escape" || !isOpen()) return;
      if ($(e.target).closest('[role="dialog"]').not("#docs-chat-panel").length) return;
      close();
    });
  }

  C360.docsChatbot = {
    bindEvents: bindEvents,
    ask: ask,
    open: open,
    close: close,
    toggle: toggle,
    // Exposed for unit testing (static/js/__tests__/docs-chatbot-view.test.js).
    renderAnswerHtml: renderAnswerHtml,
    basisText: basisText,
    contextKey: contextKey,
    missingText: missingText,
    pageContext: pageContext,
    PAGE_CHIPS: PAGE_CHIPS
  };
})(window.C360);
