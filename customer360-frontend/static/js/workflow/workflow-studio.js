/*!
 * workflow-studio.js : Flowy canvas UI generated from WorkflowCore.NODES.
 * Also exposes window.workflowAgent, the tool surface an AI agent uses to build and check flows.
 *
 * Load workflow-template.js after Handlebars and before this script.
 * Styles live in flowy.css; named Handlebars templates own the generated markup.
 */
(function ($) {
  "use strict";

  function groupRecipes(recipes) {
    const groups = new Map();
    Object.entries(recipes).forEach(([key, recipe]) => {
      const industry = recipe.industry || "Other";
      if (!groups.has(industry)) groups.set(industry, []);
      groups.get(industry).push({ key, name: recipe.name });
    });
    return Array.from(groups, ([industry, entries]) => ({
      industry,
      recipes: entries,
    }));
  }

  /**
   * Builds one studio inside `root`. Selector strings are scoped to `root`;
   * element and document arguments pass straight through to jQuery.
   */
  function createStudio(root, options) {
    const $ = (selector) =>
      typeof selector === "string" ? root.find(selector) : jQuery(selector);
    const C = window.WorkflowCore,
      N = C.NODES;
    const templates = window.WorkflowTemplates;
    if (!templates)
      throw new Error("Load workflow-template.js before workflow-studio.js.");
    if (
      options.segments != null &&
      (!Array.isArray(options.segments) ||
        options.segments.some(
          (segment) =>
            !segment ||
            segment.segment_id == null ||
            !String(segment.segment_name || "").trim(),
        ))
    )
      throw new TypeError(
        "segments must contain objects with segment_id and segment_name.",
      );
    if (
      options.loadProfiles != null &&
      typeof options.loadProfiles !== "function"
    )
      throw new TypeError("loadProfiles must be a function.");
    [
      "onClearCanvas",
      "onRunCampaign",
      "onRecipeChange",
      "onReady",
      "onChange",
    ].forEach((callback) => {
      if (options[callback] != null && typeof options[callback] !== "function")
        throw new TypeError(callback + " must be a function.");
    });
    const $canvas = $("#canvas"),
      canvas = $canvas[0];
    const sampleSegment = C.SAMPLE_SEGMENTS[0];
    const providedSegments = options.segments || [];
    let configuredSegmentId =
      options.segmentId != null
        ? String(options.segmentId)
        : providedSegments.length
          ? String(providedSegments[0].segment_id)
          : sampleSegment.segment_id;
    if (!configuredSegmentId.trim())
      throw new TypeError("segmentId must be a non-empty string.");
    const matchingSegment = providedSegments.find(
      (candidate) => String(candidate.segment_id) === configuredSegmentId,
    );
    let segment = {
      ...(matchingSegment || sampleSegment),
      segment_id: configuredSegmentId,
      segment_name:
        options.segmentName ||
        (matchingSegment && matchingSegment.segment_name) ||
        (options.segmentId != null
          ? "Selected audience"
          : sampleSegment.segment_name),
    };
    const segments = [segment];
    const loadProfiles =
      options.loadProfiles ||
      ((segmentId) => {
        const selected = segmentById(segmentId);
        return C.sampleProfiles(
          options.profileCount,
          selected ? selected.seed : sampleSegment.seed,
        );
      });
    let profiles = [],
      loadedSegmentId = null,
      loadingSegment = false,
      loadToken = 0,
      lastSim = null,
      timer;
    const segmentById = (id) =>
      segments.find((segment) => segment.segment_id === id) || null;
    const defaultSegmentId = () =>
      (segments[0] && segments[0].segment_id) || null;

    function toast(m) {
      const t = $("#toast");
      t.text(m).addClass("on");
      clearTimeout(toast.h);
      toast.h = setTimeout(() => t.removeClass("on"), 2800);
    }
    function notifyHost(callbackName, ...args) {
      const callback = options[callbackName];
      if (!callback) return;
      try {
        Promise.resolve(callback(...args)).catch((error) =>
          toast(
            callbackName +
              " callback failed: " +
              (error && error.message ? error.message : String(error)),
          ),
        );
      } catch (error) {
        toast(
          callbackName +
            " callback failed: " +
            (error && error.message ? error.message : String(error)),
        );
      }
    }
    const getEl = (id) => $canvas.find('.blockid[value="' + id + '"]').parent();
    const typeOf = (id) => getEl(id).find(".blockelemtype").val();
    const blocks = () => flowy.output().blockarr;
    const kidsOf = (arr, id) =>
      arr
        .filter((b) => b.parent === id)
        .sort((a, b) => getEl(a.id).offset().left - getEl(b.id).offset().left);

    /* ---------- palette + block markup, all from the registry ---------- */
    function renderPalette() {
      const categories = ["Input", "Logic", "Activate"].map((category) => ({
        label: category,
        nodes: Object.entries(N)
          .filter(([, definition]) => definition.category === category)
          .map(([type, definition]) => ({
            type,
            label: definition.label,
            description: definition.description,
          })),
      }));
      $("#pal").html(
        templates.workflowPalette({
          categories,
          templateVariables: C.TEMPLATE_VARS.map(
            (variable) => "{{" + variable + "}}",
          ).join(" and "),
        }),
      );
    }
    function configurationField(key, definition, value) {
      const isSegment = definition.type === "segment";
      const choices = isSegment
        ? segments.map((segment) => ({
            value: segment.segment_id,
            label: segment.segment_name,
          }))
        : (definition.options || []).map((option) => ({
            value: option,
            label: (definition.labels && definition.labels[option]) || option,
          }));
      return {
        key,
        label: definition.label || key,
        value,
        segmentName: isSegment
          ? segment
            ? segment.segment_name
            : "No customer segment"
          : "",
        isSegment,
        isEnum: definition.type === "enum" || isSegment,
        isLongText: definition.type === "longtext",
        inputType: definition.type === "number" ? "number" : "text",
        options: choices.map((choice) => ({
          ...choice,
          selected: String(value) === choice.value,
        })),
      };
    }
    function blockInner(type, cfg, blockId) {
      const definition = N[type];
      return templates.workflowBlock({
        type,
        blockId,
        label: definition.label,
        fields: Object.entries(definition.config).map(([key, field]) =>
          configurationField(key, field, cfg[key]),
        ),
      });
    }
    const nextNid = () =>
      "n" +
      (1 +
        Math.max(
          0,
          ...$canvas
            .find(".block")
            .map(function () {
              return (
                parseInt(
                  ($(this).attr("data-nid") || "").replace(/\D/g, ""),
                  10,
                ) || 0
              );
            })
            .get(),
        ));

    /* ---------- Flowy ---------- */
    function onSnap(drag, first, parentEl) {
      const $d = $(drag),
        type = $d.find(".blockelemtype").val(),
        def = N[type],
        fid = Number($d.find(".blockid").val());
      if (first && !def.entry) {
        toast("Start the flow with an Event trigger");
        return false;
      }
      if (!first && def.entry) {
        toast("A flow has one Event trigger");
        return false;
      }
      if (!first) {
        const pid = Number($(parentEl).find(".blockid").val()),
          pd = N[typeOf(pid)];
        if (
          blocks().filter((b) => b.parent === pid).length >= C.maxChildren(pd)
        ) {
          toast(
            pd.terminal
              ? "Goal / Exit ends its branch"
              : pd.branches
                ? pd.label + " has two branches"
                : pd.label + " continues to one next step",
          );
          return false;
        }
      }
      const cfg = C.defaults(type);
      if (def.entry) cfg.segment_id = defaultSegmentId();
      $d.attr("data-nid", nextNid()).html(blockInner(type, cfg, fid));
      schedule();
      return true;
    }
    flowy(
      canvas,
      () => {},
      () => {},
      onSnap,
      () => {
        schedule();
        return false;
      },
      24,
      70,
    );
    $canvas.on("mousedown touchstart", "input, select, textarea", (e) =>
      e.stopPropagation(),
    );
    $canvas.on("input change", "[data-k]", schedule);

    /* ---------- zoom + pan (ported from the previous demo) ---------- */
    let panModeEnabled = false;
    let canvasZoom = 1;
    let activeCanvasPan = null;
    const $canvasNavigation = $("#wrap");
    const minimumCanvasZoom = 0.5;
    const maximumCanvasZoom = 1.5;
    const canvasZoomStep = 0.1;
    function endCanvasPan(event) {
      if (!activeCanvasPan) return;
      const pointerEvent = event.originalEvent || event;
      if (
        event.type !== "pointercancel" &&
        pointerEvent.pointerId !== activeCanvasPan.pointerId
      )
        return;
      activeCanvasPan = null;
      $canvasNavigation.removeClass("pan-active");
    }
    $("#bPanCanvas").on("click", function () {
      panModeEnabled = !panModeEnabled;
      $(this).attr("aria-pressed", String(panModeEnabled));
      $(this).text(panModeEnabled ? "Pan mode on" : "Pan canvas");
      $canvas.toggleClass("pan-mode", panModeEnabled);
    });
    function setCanvasZoom(nextZoom) {
      const newZoom = Math.max(
        minimumCanvasZoom,
        Math.min(maximumCanvasZoom, Math.round(nextZoom * 10) / 10),
      );
      const viewportWidth = $canvasNavigation.innerWidth();
      const viewportHeight = $canvasNavigation.innerHeight();
      const centerX = $canvas.scrollLeft() + viewportWidth / (2 * canvasZoom);
      const centerY = $canvas.scrollTop() + viewportHeight / (2 * canvasZoom);
      canvasZoom = newZoom;
      $canvas.css({
        width: viewportWidth / canvasZoom + "px",
        height: viewportHeight / canvasZoom + "px",
        transform: "scale(" + canvasZoom + ")",
      });
      flowy.setZoom(canvasZoom);
      $canvas.scrollLeft(
        Math.max(0, centerX - viewportWidth / (2 * canvasZoom)),
      );
      $canvas.scrollTop(
        Math.max(0, centerY - viewportHeight / (2 * canvasZoom)),
      );
      $("#canvasZoomLevel").text(Math.round(canvasZoom * 100) + "%");
      $("#bZoomOut").prop("disabled", canvasZoom <= minimumCanvasZoom);
      $("#bZoomIn").prop("disabled", canvasZoom >= maximumCanvasZoom);
    }
    $("#bZoomOut").on("click", () =>
      setCanvasZoom(canvasZoom - canvasZoomStep),
    );
    $("#bZoomIn").on("click", () => setCanvasZoom(canvasZoom + canvasZoomStep));
    $("#bResetCanvasView").on("click", () => {
      endCanvasPan({ type: "pointercancel" });
      setCanvasZoom(1);
      $canvas.scrollLeft(0).scrollTop(0);
    });
    $canvas.on("pointerdown", (event) => {
      const pointerEvent = event.originalEvent || event;
      if (!panModeEnabled || pointerEvent.button !== 0) return;
      if (
        $(pointerEvent.target).closest(".block, .create-flowy, .indicator")
          .length
      )
        return;

      activeCanvasPan = {
        pointerId: pointerEvent.pointerId,
        startX: pointerEvent.clientX,
        startY: pointerEvent.clientY,
        startScrollLeft: $canvas.scrollLeft(),
        startScrollTop: $canvas.scrollTop(),
      };
      if ($canvas[0].setPointerCapture && pointerEvent.pointerId != null) {
        $canvas[0].setPointerCapture(pointerEvent.pointerId);
      }
      $canvasNavigation.addClass("pan-active");
      event.preventDefault();
    });
    $(document).on("pointermove.workflowCanvasPan", (event) => {
      const pointerEvent = event.originalEvent || event;
      if (
        !activeCanvasPan ||
        pointerEvent.pointerId !== activeCanvasPan.pointerId
      )
        return;
      $canvas.scrollLeft(
        activeCanvasPan.startScrollLeft +
          (activeCanvasPan.startX - pointerEvent.clientX) / canvasZoom,
      );
      $canvas.scrollTop(
        activeCanvasPan.startScrollTop +
          (activeCanvasPan.startY - pointerEvent.clientY) / canvasZoom,
      );
      event.preventDefault();
    });
    $(document).on(
      "pointerup.workflowCanvasPan pointercancel.workflowCanvasPan",
      endCanvasPan,
    );
    $canvas.on("lostpointercapture", endCanvasPan);

    /* ---------- canvas <-> workflow JSON ---------- */
    function readCfg(id, def) {
      const cfg = {};
      getEl(id)
        .find("[data-k]")
        .each(function () {
          const $field = $(this);
          const k = $field.attr("data-k");
          const v = $field.is("[data-value]")
            ? $field.attr("data-value")
            : $field.val();
          cfg[k] = def.config[k].type === "number" && v !== "" ? Number(v) : v;
        });
      return cfg;
    }
    function getWorkflow() {
      const arr = blocks();
      if (!arr.length) return null;
      const build = (b) => {
        const el = getEl(b.id),
          type = el.find(".blockelemtype").val(),
          def = N[type];
        const kids = kidsOf(arr, b.id).map(build);
        if (def.branches)
          kids.forEach((c, i) => {
            if (def.branches[i]) c.branch = def.branches[i];
          });
        return {
          id: el.attr("data-nid"),
          type,
          config: readCfg(b.id, def),
          children: kids,
        };
      };
      return {
        schema_version: 2,
        root: build(arr.find((b) => b.parent === -1)),
      };
    }
    function relabel() {
      $canvas.find(".branch").text("").attr("class", "branch");
      const arr = blocks();
      arr.forEach((b) => {
        const def = N[typeOf(b.id)];
        if (!def.branches) return;
        kidsOf(arr, b.id).forEach((k, i) => {
          if (def.branches[i])
            getEl(k.id)
              .find(".branch")
              .text(C.BRANCH_LABELS[def.branches[i]])
              .attr("class", "branch " + (i === 0 ? "y" : "n"));
        });
      });
    }
    function clearCanvas() {
      flowy.deleteBlocks();
      lastSim = null;
      $(".hits").text("");
    }
    function addBlock(type, parent) {
      const $item = $('.create-flowy[data-type="' + type + '"]');
      if (!$item.length) throw new Error("Unknown node type " + type);
      return flowy.addBlock($item[0], parent);
    }
    function restoreDefaultTrigger() {
      addBlock("trigger", null);
      flowy.relayout();
    }
    function triggerSegmentId() {
      const root = blocks().find((block) => block.parent === -1);
      return root
        ? getEl(root.id).find('[data-k="segment_id"]').attr("data-value") ||
            null
        : null;
    }
    /* Profiles always belong to the segment selected on the trigger. */
    async function loadSegment(segmentId) {
      const token = ++loadToken;
      loadedSegmentId = segmentId;
      profiles = [];
      lastSim = null;
      $(".hits").text("");
      const segment = segmentById(segmentId);
      loadingSegment = !!segment;
      renderProfiles();
      renderReport();
      if (!segment) return;
      try {
        const loaded = await loadProfiles(segmentId);
        if (!Array.isArray(loaded))
          throw new Error("loadProfiles must return an array of profiles.");
        if (token !== loadToken) return;
        profiles = loaded;
      } catch (error) {
        if (token === loadToken)
          toast("Could not load segment: " + error.message);
      } finally {
        if (token === loadToken) {
          loadingSegment = false;
          renderProfiles();
          renderReport();
        }
      }
    }
    function syncSegment() {
      const segmentId = triggerSegmentId();
      if (segmentId !== loadedSegmentId) loadSegment(segmentId);
    }
    function setSegment(nextSegment) {
      const nextSegmentId =
        nextSegment == null ? null : String(nextSegment.segment_id || "");
      if (
        nextSegment != null &&
        (!nextSegmentId.trim() ||
          !String(nextSegment.segment_name || "").trim())
      )
        throw new TypeError(
          "segment must contain a non-empty segment_id and segment_name.",
        );

      configuredSegmentId = nextSegmentId;
      segment = nextSegment
        ? {
            ...nextSegment,
            segment_id: nextSegmentId,
            segment_name: String(nextSegment.segment_name).trim(),
          }
        : null;
      segments.splice(0, segments.length);
      if (segment) segments.push(segment);

      const rootBlock = blocks().find((block) => block.parent === -1);
      if (rootBlock) {
        const $field = getEl(rootBlock.id).find('[data-k="segment_id"]');
        $field
          .attr("data-value", configuredSegmentId || "")
          .attr(
            "aria-label",
            "Customer segment: " +
              (segment ? segment.segment_name : "No customer segment"),
          )
          .find(".segment-readonly-copy strong")
          .text(segment ? segment.segment_name : "No customer segment");
      }

      loadSegment(configuredSegmentId);
      refresh();
      return { ok: true, segmentId: configuredSegmentId };
    }
    function setWorkflow(input) {
      let wf = input;
      if (wf && wf.root && wf.root.type === "trigger")
        wf = {
          ...wf,
          root: {
            ...wf.root,
            config: { ...wf.root.config, segment_id: configuredSegmentId },
          },
        };
      const issues = C.validate(wf);
      const fatal = issues.filter((i) => C.FATAL.indexOf(i.code) >= 0);
      if (fatal.length) return { ok: false, issues };
      clearCanvas();
      try {
        const q = [[wf.root, null]];
        while (q.length) {
          const [n, parent] = q.shift(),
            id = addBlock(n.type, parent),
            el = getEl(id),
            def = N[n.type];
          el.attr("data-nid", n.id);
          Object.entries(n.config || {}).forEach(([k, v]) => {
            const $field = el.find('[data-k="' + k + '"]');
            if ($field.is("[data-value]"))
              $field.attr("data-value", configuredSegmentId || "");
            else $field.val(v);
          });
          (n.children || [])
            .slice()
            .sort(
              (a, b) =>
                (def.branches || []).indexOf(a.branch) -
                (def.branches || []).indexOf(b.branch),
            )
            .forEach((c) => q.push([c, id]));
        }
      } catch (e) {
        clearCanvas();
        restoreDefaultTrigger();
        refresh();
        return {
          ok: false,
          issues: [
            {
              level: "error",
              code: "RENDER_FAILED",
              msg: e.message,
              fix: "Check node types and child counts.",
            },
          ],
        };
      }
      relabel();
      flowy.relayout();
      refresh();
      return { ok: true, issues: C.validate(getWorkflow()) };
    }

    /* ---------- side panes ---------- */
    const TABS = ["Seg", "Chk", "Rep", "Json"];
    function setTab(t) {
      TABS.forEach((x) => {
        $("#tab" + x).attr("aria-selected", x === t);
        $("#pane" + x).prop("hidden", x !== t);
      });
    }
    TABS.forEach((t) => $("#tab" + t).on("click", () => setTab(t)));
    const pct = (a, b) => (b ? Math.round((a / b) * 100) + "%" : "–");

    function actionParent() {
      const arr = blocks();
      return arr.find((block) => {
        const definition = N[typeOf(block.id)];
        const childCount = arr.filter(
          (child) => child.parent === block.id,
        ).length;
        return (
          definition &&
          !definition.terminal &&
          childCount < C.maxChildren(definition)
        );
      });
    }
    function buildGuideContext() {
      const workflow = getWorkflow();
      const segment = segmentById(triggerSegmentId());
      const showBuildGuide =
        workflow &&
        C.validate(workflow).some((issue) => issue.code === "NO_ACTION");
      const canAdd = !!actionParent();
      return {
        showBuildGuide,
        hasSegment: !!segment,
        hasAudience: !!segment && profiles.length > 0 && !loadingSegment,
        canChooseAction: !!segment,
        canAdd,
        actions: [
          { type: "email", label: "Email", icon: "✉", className: "email" },
          { type: "sms", label: "SMS", icon: "↗", className: "sms" },
          { type: "push", label: "Push", icon: "◉", className: "push" },
        ].map((action) => ({ ...action, canAdd })),
        segmentName: segment ? segment.segment_name : "",
        profileCount: profiles.length,
        loading: loadingSegment,
      };
    }

    function renderProfiles() {
      const segment = segmentById(loadedSegmentId);
      $("#segTable").html(
        templates.workflowProfiles({
          ...buildGuideContext(),
          segmentName: segment ? segment.segment_name : "No customer segment",
          segmentHint: segment
            ? segment.description
            : "Choose a customer segment on the Event trigger.",
          loading: loadingSegment,
          profileCount: profiles.length,
          hasSimulation: !!lastSim,
          profiles: profiles.map((profile, index) => ({
            ...profile,
            pushLabel: profile.push ? "yes" : "no",
            outcome: lastSim ? lastSim.profiles[index].outcome : "",
            days: lastSim ? lastSim.profiles[index].days : "",
          })),
        }),
      );
    }
    function renderChecks(issues) {
      const guide = buildGuideContext();
      const missingAction = issues.some((issue) => issue.code === "NO_ACTION");
      const shown = issues.filter(
        (issue) =>
          issue.level !== "info" &&
          issue.code !== "NO_ACTION" &&
          !(missingAction && issue.code === "NO_GOAL"),
      );
      const errs = shown.filter((issue) => issue.level === "error").length,
        warns = shown.length - errs;
      $("#pill")
        .attr(
          "class",
          "pill " +
            (errs ? "bad" : missingAction ? "info" : warns ? "warn" : "ok"),
        )
        .text(
          errs
            ? errs + (errs > 1 ? " issues to fix" : " issue to fix")
            : missingAction
              ? "Add a message"
              : warns
                ? warns + (warns > 1 ? " warnings" : " warning")
                : "Ready to run",
        );
      $canvas.find(".block").removeClass("is-error is-warn");
      shown.forEach((i) => {
        if (i.node) {
          const el = $canvas.find('.block[data-nid="' + i.node + '"]');
          el.addClass(i.level === "error" ? "is-error" : "is-warn");
        }
      });
      $("#paneChk").html(
        templates.workflowChecks({
          issues: shown,
          ...guide,
        }),
      );
    }
    function addSuggestedAction(type) {
      const parent = actionParent();
      if (!parent) {
        toast("Choose a node that can accept another step.");
        return;
      }
      addBlock(type, parent.id);
      flowy.relayout();
      lastSim = null;
      refresh();
      renderProfiles();
      renderReport();
      toast(N[type].label + " added. Configure it on the canvas.");
    }
    function renderReport() {
      if (!lastSim) return $("#paneRep").html(templates.workflowReportEmpty());
      const totals = lastSim.totals;
      const messages = [];
      C.walk(getWorkflow().root, (node) => {
        const definition = N[node.type];
        if (!definition.channel) return;
        const stats = lastSim.nodes[node.id] || {
          reach: 0,
          delivered: 0,
          opened: 0,
          clicked: 0,
          conversions: 0,
          revenue: 0,
        };
        const config = node.config;
        messages.push({
          ...stats,
          type: node.type,
          label: definition.label,
          title: C.fill(
            config.subject || config.title || config.body,
            profiles[0],
          ),
          body: C.fill(config.body, profiles[0]),
          isSms: node.type === "sms",
          openRate: pct(stats.opened, stats.delivered),
          clickRate: pct(stats.clicked, stats.delivered),
        });
      });
      const kpis = [
        { label: "Entered", value: totals.entered },
        { label: "Open rate", value: pct(totals.opened, totals.received) },
        { label: "Click rate", value: pct(totals.clicked, totals.received) },
        { label: "Converted", value: totals.converted },
        { label: "Goal reached", value: pct(totals.goal, totals.entered) },
        { label: "Revenue", value: "$" + totals.revenue.toLocaleString() },
      ];
      const funnel = [
        { label: "Entered", value: totals.entered },
        { label: "Received", value: totals.received },
        { label: "Opened", value: totals.opened },
        { label: "Clicked", value: totals.clicked },
        { label: "Converted", value: totals.converted },
      ].map((stage) => ({
        ...stage,
        width: totals.entered ? (stage.value / totals.entered) * 100 : 0,
      }));
      $("#paneRep").html(
        templates.workflowReport({ totals, kpis, funnel, messages }),
      );
    }
    function renderJson() {
      const ta = $("#jsonData");
      if (!ta.is(":focus")) ta.val(JSON.stringify(getWorkflow(), null, 1));
    }
    function refresh() {
      const wf = getWorkflow();
      relabel();
      syncSegment();
      renderChecks(C.validate(wf));
      renderJson();
      notifyHost("onChange", wf);
    }
    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(refresh, 150);
    }

    /* ---------- actions ---------- */
    function run() {
      const wf = getWorkflow();
      if (!wf) return toast("Add an Event trigger and some blocks first");
      if (C.validate(wf).some((i) => i.level === "error")) {
        setTab("Chk");
        return toast("Fix the errors first");
      }
      if (loadingSegment) return toast("Segment profiles are still loading");
      if (!profiles.length)
        return toast("The selected segment has no profiles");
      lastSim = C.simulate(wf, profiles);
      $(".hits").text("");
      Object.entries(lastSim.nodes).forEach(([id, s]) =>
        $canvas
          .find('.block[data-nid="' + id + '"] .hits')
          .text(s.reach + " profiles"),
      );
      renderProfiles();
      renderReport();
      setTab("Rep");
      notifyHost("onRunCampaign", wf, configuredSegmentId, lastSim);
      return lastSim;
    }
    $("#bRun").on("click", run);
    $("#paneChk, #paneSeg").on("click", "[data-add-action]", function () {
      addSuggestedAction($(this).attr("data-add-action"));
    });
    function clearAll() {
      clearCanvas();
      restoreDefaultTrigger();
      renderReport();
      refresh();
      notifyHost("onClearCanvas", configuredSegmentId);
    }
    $("#bClear").on("click", clearAll);
    $("#recipe").on("change", function () {
      const r = templates.RECIPES[this.value];
      if (!r) return;
      const res = setWorkflow(JSON.parse(JSON.stringify(r.workflow)));
      toast(res.ok ? r.name + " loaded. " + r.intent : "Could not load recipe");
      renderProfiles();
      renderReport();
      if (res.ok)
        notifyHost(
          "onRecipeChange",
          this.value,
          getWorkflow(),
          configuredSegmentId,
        );
      this.value = "";
    });
    $("#bApply").on("click", () => {
      try {
        const res = setWorkflow(JSON.parse($("#jsonData").val()));
        toast(
          res.ok
            ? "Workflow applied"
            : "Not applied: " + (res.issues[0] || {}).msg,
        );
        if (!res.ok) {
          renderChecks(res.issues);
          setTab("Chk");
        }
      } catch (e) {
        toast("Invalid JSON: " + e.message);
      }
    });
    const copy = (t, msg) =>
      navigator.clipboard.writeText(t).then(
        () => toast(msg),
        () => toast("Copy failed"),
      );
    $("#bCopyJson").on("click", () =>
      copy(JSON.stringify(getWorkflow(), null, 2), "Workflow JSON copied"),
    );
    $("#bCopyManifest").on("click", () =>
      copy(JSON.stringify(api.manifest(), null, 2), "Agent manifest copied"),
    );

    /* ---------- public API (window.workflowAgent in the frame) ---------- */
    function buildManifest() {
      return {
        ...C.manifest(),
        recipes: Object.fromEntries(
          Object.entries(templates.RECIPES).map(([key, recipe]) => [
            key,
            {
              industry: recipe.industry,
              name: recipe.name,
              intent: recipe.intent,
              workflow: recipe.workflow,
            },
          ]),
        ),
      };
    }
    const api = {
      manifest: buildManifest,
      getWorkflow,
      setWorkflow,
      setSegment,
      validate: () => C.validate(getWorkflow()),
      simulate: () => {
        const wf = getWorkflow();
        return wf ? C.simulate(wf, profiles) : null;
      },
      applyOps(ops) {
        const r = C.applyOps(getWorkflow(), ops);
        if (r.errors.length)
          return { ok: false, errors: r.errors, issues: r.issues };
        const s = setWorkflow(r.workflow);
        return { ok: s.ok, issues: s.issues, workflow: getWorkflow() };
      },
      run,
      clear: clearAll,
      getSegments: () => segments.slice(),
      getSegmentId: () => configuredSegmentId,
    };

    renderPalette();
    renderProfiles();
    renderReport();
    setTab("Seg");
    if (options.workflow) {
      if (!setWorkflow(options.workflow).ok)
        throw new Error("The initial workflow is invalid.");
    } else {
      restoreDefaultTrigger();
      refresh();
    }
    return api;
  }

  /* ---------- jQuery plugin ----------
   * $(host).workflowStudio(options) mounts the studio in a same-origin iframe, so its CSS and
   * Flowy instance stay isolated from the host page. Method names such as "getWorkflow" are
   * forwarded to the mounted studio. `setSegment(segmentOrNull)` changes or clears the audience.
   * `inline: true` mounts directly in the current document and is used by the frame itself.
   */
  const DATA_KEY = "workflowStudio";
   const STUDIO_SCRIPT =
     document.currentScript ||
     document.querySelector("script[data-workflow-frame]");
   const FRAME_PAGE = STUDIO_SCRIPT
     ? new URL(
         STUDIO_SCRIPT.dataset.workflowFrame || "./workflow-studio-frame.html",
         STUDIO_SCRIPT.src || document.baseURI,
       ).href
     : null;
  const METHODS = [
    "getWorkflow",
    "setWorkflow",
    "setSegment",
    "applyOps",
    "validate",
    "simulate",
    "run",
    "clear",
    "manifest",
    "getSegments",
    "getSegmentId",
  ];
  let inlineMounted = false;

  function mountInline($host, options) {
    const C = window.WorkflowCore,
      templates = window.WorkflowTemplates;
    if (inlineMounted)
      throw new Error(
        "Only one inline studio can be mounted per document; Flowy is a page-level singleton.",
      );
    if (!window.flowy || !C || !templates)
      throw new Error(
        "Load flowy.js, workflow-core.js and workflow-template.js before workflow-studio.js.",
      );
    inlineMounted = true;
    $host.html(
      templates.workflowShell({
        title: options.title,
        recipeGroups: groupRecipes(templates.RECIPES),
      }),
    );
    const api = createStudio($host, options);
    $host.data(DATA_KEY, api);
    window.workflowAgent = api;
    if (options.onReady) {
      const reportError = (error) =>
        $host
          .find("#toast")
          .text(
            "onReady callback failed: " +
              (error && error.message ? error.message : String(error)),
          )
          .addClass("on");
      try {
        Promise.resolve(options.onReady(api.getSegmentId(), api)).catch(
          reportError,
        );
      } catch (error) {
        reportError(error);
      }
    }
  }

  function mountInFrame($host, options) {
    if (!FRAME_PAGE)
      throw new Error(
        "Unable to locate the workflow studio frame URL. Load workflow-studio.js from its script element.",
      );
    let frameBody = null;
    const current = () => {
      const api = frameBody && frameBody.data(DATA_KEY);
      if (!api)
        throw new Error(
          "workflowStudio is still loading. Call it from the onReady callback.",
        );
      return api;
    };
    const proxy = {};
    METHODS.forEach((name) => {
      proxy[name] = (...args) => current()[name](...args);
    });
    const showFrameError = (message) => {
      frameBody = null;
      $host
        .empty()
        .removeData(DATA_KEY)
        .append(
          $("<div>")
            .attr("role", "alert")
            .addClass(
              "m-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700",
            )
            .text(message),
        );
      if (window.workflowAgent === proxy) delete window.workflowAgent;
    };
    const $frame = $("<iframe>", {
      class: "workflow-studio-frame",
      title: options.title,
      src: FRAME_PAGE,
    }).css({
      display: "block",
      width: "100%",
      height: "clamp(720px, 88vh, 1200px)",
      minHeight: "720px",
      border: 0,
    });
    $frame.on("load", function () {
      const frameDocument = this.contentDocument;
      if (!frameDocument) {
        showFrameError(
          "The workflow studio frame is cross-origin or blocked. It must load from this app's origin: " +
            FRAME_PAGE,
        );
        return;
      }
      if (frameDocument.URL === "about:blank") return;
      const frameWindow = this.contentWindow;
      if (
        !frameWindow.jQuery ||
        typeof frameWindow.jQuery.fn.workflowStudio !== "function"
      ) {
        showFrameError(
          "The workflow studio frame loaded, but its required scripts did not initialize. Check the frame's static asset responses.",
        );
        return;
      }
      frameBody = frameWindow.jQuery(frameWindow.document.body);
      frameBody.workflowStudio($.extend({}, options, { inline: true }));
    });
    $host.empty().append($frame).data(DATA_KEY, proxy);
    window.workflowAgent = proxy;
  }

  $.fn.workflowStudio = function (methodOrOptions, ...args) {
    if (typeof methodOrOptions === "string") {
      if (METHODS.indexOf(methodOrOptions) < 0)
        throw new Error(
          'Unknown workflowStudio method "' + methodOrOptions + '".',
        );
      const api = this.first().data(DATA_KEY);
      if (!api)
        throw new Error("workflowStudio is not mounted on this element.");
      return api[methodOrOptions](...args);
    }
    const options = $.extend({}, $.fn.workflowStudio.defaults, methodOrOptions);
    return this.each(function () {
      const $host = $(this);
      if ($host.data(DATA_KEY))
        throw new Error("workflowStudio is already mounted on this element.");
      if (options.inline) mountInline($host, options);
      else mountInFrame($host, options);
    });
  };

  /**
   * `segmentId` and `segmentName` configure the read-only audience.
   * `loadProfiles(segmentId)` returns profiles or a promise of profiles.
   * Lifecycle callbacks: onReady(segmentId, api), onChange(workflow),
   * onClearCanvas(segmentId), onRunCampaign(workflow, segmentId, simulation),
   * and onRecipeChange(recipeKey, workflow, segmentId).
   */
  $.fn.workflowStudio.defaults = {
    title: "Customer Engagement Workflow",
    workflow: null,
    segmentName: null,
    segmentId: null,
    segments: null,
    loadProfiles: null,
    profileCount: 32,
    inline: false,
    onClearCanvas: null,
    onRunCampaign: null,
    onRecipeChange: null,
    onReady: null,
    onChange: null,
  };
})(jQuery);
