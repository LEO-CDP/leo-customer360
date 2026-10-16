"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const fsSync = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const vm = require("node:vm");
const { after, before, test } = require("node:test");
const { chromium } = require("playwright");
const Core = require("../html/workflow-core.js");

const htmlDirectory = path.join(__dirname, "../html");
const templateContext = {
  WorkflowCore: Core,
  Handlebars: {
    registerPartial() {},
    compile() {
      return () => "";
    },
  },
};
vm.runInNewContext(
  fsSync.readFileSync(path.join(htmlDirectory, "workflow-template.js"), "utf8"),
  templateContext,
);
const Recipes = JSON.parse(
  JSON.stringify(templateContext.WorkflowTemplates.RECIPES),
);
const RecipeDomains = [
  "Banking & Financial Services",
  "Education",
  "Healthcare",
  "Hospitality & Food Service",
  "Insurance",
  "Manufacturing",
  "Media & Entertainment",
  "Real Estate",
  "Retail & E-Commerce",
  "Telecommunications",
  "Travel & Hospitality",
];
let browser;
let server;
let pageUrl;
const defaultSegment = {
  segment_id: "123456",
  segment_name: "Frequent Travelers",
  description: "Profiles in this audience",
  seed: 42,
};
const defaultProfiles = Core.sampleProfiles(32, defaultSegment.seed);
function withSegment(workflow) {
  const copy = structuredClone(workflow);
  copy.root.config = {
    ...copy.root.config,
    segment_id: copy.root.config.segment_id || defaultSegment.segment_id,
  };
  return copy;
}

before(async () => {
  const contentTypes = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
  };
  const files = new Map(
    await Promise.all(
      (await fs.readdir(htmlDirectory))
        .filter((name) => contentTypes[path.extname(name)])
        .map(async (name) => [
          "/" + name,
          {
            body: await fs.readFile(path.join(htmlDirectory, name)),
            type: contentTypes[path.extname(name)],
          },
        ]),
    ),
  );
  server = http.createServer((request, response) => {
    const file = files.get(new URL(request.url, "http://localhost").pathname);
    if (!file) {
      response.writeHead(404).end();
      return;
    }
    response.setHeader("Content-Type", file.type);
    response.end(file.body);
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  pageUrl =
    "http://127.0.0.1:" + server.address().port + "/campaign-workflow.html";
  browser = await chromium.launch({ channel: "chrome", headless: true });
});

after(async () => {
  if (browser) await browser.close();
  if (server)
    await new Promise((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
});

async function openStudio(t) {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  t.after(() => page.close());
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], "No uncaught browser errors"));
  await page.goto(pageUrl);
  await page.waitForFunction(() => window.studioReady === true);
  const frame = await (
    await page.waitForSelector("iframe.workflow-studio-frame")
  ).contentFrame();
  await frame.waitForFunction(() => Boolean(window.workflowAgent));
  await frame.waitForFunction(() => {
    const status = document.querySelector("#segTable .sub");
    return Boolean(status) && !status.textContent.includes("Loading");
  });
  return frame;
}

test("campaign host mounts the studio as a jQuery plugin and forwards its methods", async (t) => {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  t.after(() => page.close());
  await page.goto(pageUrl);
  await page.waitForFunction(() => window.studioReady === true);
  assert.equal(
    await page.evaluate(() => window.studioSegmentId),
    defaultSegment.segment_id,
  );
  const frame = await (
    await page.waitForSelector("iframe.workflow-studio-frame")
  ).contentFrame();
  await frame.waitForFunction(() => Boolean(window.workflowAgent));
  await frame.waitForFunction(
    () =>
      !document.querySelector("#segTable .sub").textContent.includes("Loading"),
  );
  assert.equal(
    await frame.locator("h1").textContent(),
    "Customer Engagement Workflow",
  );
  assert.deepEqual(
    await page.evaluate(() => $("#studio").workflowStudio("getWorkflow")),
    withSegment({
      schema_version: 2,
      root: {
        id: "n1",
        type: "trigger",
        config: {
          segment_id: defaultSegment.segment_id,
          event: "cart_abandoned",
        },
        children: [],
      },
    }),
  );
  await page.evaluate(
    (workflow) => $("#studio").workflowStudio("setWorkflow", workflow),
    Recipes.welcome.workflow,
  );
  assert.deepEqual(
    await page.evaluate(() => window.lastWorkflow),
    withSegment(Recipes.welcome.workflow),
  );
  await page.evaluate(() => $("#studio").workflowStudio("run"));
  assert.equal(await frame.locator("#pill").textContent(), "Ready to run");
  assert.deepEqual(
    await page.evaluate(
      () =>
        window.lastRun && {
          segmentId: window.lastRun.segmentId,
          rootType: window.lastRun.workflow.root.type,
          entered: window.lastRun.simulation.totals.entered,
        },
    ),
    { segmentId: defaultSegment.segment_id, rootType: "trigger", entered: 32 },
  );
  await frame.locator("#recipe").selectOption("winback");
  assert.equal(
    await page.evaluate(() => window.lastRecipeChange.recipeKey),
    "winback",
  );
  assert.equal(
    await page.evaluate(() => window.lastRecipeChange.segmentId),
    defaultSegment.segment_id,
  );
  await page.evaluate(() => $("#studio").workflowStudio("clear"));
  assert.equal(await frame.locator("#canvas .block").count(), 1);
  assert.equal(
    await frame.locator("#canvas .blockelemtype").inputValue(),
    "trigger",
  );
  assert.equal(
    await page.evaluate(() => window.lastWorkflow.root.type),
    "trigger",
  );
  assert.equal(
    await page.evaluate(() => window.clearedSegmentId),
    defaultSegment.segment_id,
  );
  assert.equal(
    await page.evaluate(() => $("#studio").workflowStudio("getSegmentId")),
    defaultSegment.segment_id,
  );
  await assert.rejects(
    page.evaluate(() => $("#studio").workflowStudio("nope")),
    /Unknown workflowStudio method/,
  );
});

test("registry populates the palette, recipes, profiles and initial checks", async (t) => {
  const page = await openStudio(t);
  assert.equal(
    await page.locator("#pal .create-flowy").count(),
    Object.keys(Core.NODES).length,
  );
  assert.deepEqual(await page.locator("#pal h2").allTextContents(), [
    "Input",
    "Logic",
    "Activate",
  ]);
  assert.match(
    await page.locator("#pal p").textContent(),
    /\{\{first_name\}\}/,
  );
  assert.match(
    await page.locator("#pal p").textContent(),
    /\{\{cart_value\}\}/,
  );
  assert.equal(
    await page.locator("#recipe option").count(),
    Object.keys(Recipes).length + 1,
  );
  const groupLabels = await page
    .locator("#recipe optgroup")
    .evaluateAll((groups) => groups.map((group) => group.label));
  assert.deepEqual([...groupLabels].sort(), [...RecipeDomains].sort());
  const manifest = await page.evaluate(() => workflowAgent.manifest());
  assert.deepEqual(Object.keys(manifest.recipes), Object.keys(Recipes));
  assert.equal(
    manifest.recipes.travel_preparation.industry,
    "Travel & Hospitality",
  );
  assert.equal(await page.locator("#segTable tr").count(), 33);
  assert.equal(await page.locator("#pill").textContent(), "Add a message");
  assert.match(await page.locator("#paneSeg").textContent(), /AUDIENCE/);
  assert.match(
    await page.locator("#paneSeg").textContent(),
    /Add your first message/,
  );
  assert.equal(await page.locator("#paneChk [data-add-action]").count(), 3);
  assert.match(
    await page.locator("#paneRep").textContent(),
    /Run the campaign/,
  );
});

test("first-message guidance creates a valid action and keeps validation strict", async (t) => {
  const page = await openStudio(t);
  assert.equal(await page.locator("#pill").textContent(), "Add a message");
  assert.equal(
    await page.locator('#paneSeg [data-add-action="email"]').isVisible(),
    true,
  );
  await page.locator('#paneSeg [data-add-action="email"]').click();
  const workflow = await page.evaluate(() => workflowAgent.getWorkflow());
  assert.equal(workflow.root.children.length, 1);
  assert.equal(workflow.root.children[0].type, "email");
  assert.equal(await page.locator("#pill").textContent(), "1 warning");
  assert.doesNotMatch(
    await page.locator("#paneChk").textContent(),
    /No message is sent/,
  );
  assert.match(await page.locator("#paneChk").textContent(), /No Goal \/ Exit/);
  await page.locator("#bRun").click();
  assert.equal(
    await page.locator("#paneRep .kpi .num").first().textContent(),
    "32",
  );
});

test("extracted styles preserve desktop and responsive layout", async (t) => {
  const page = await openStudio(t);
  assert.equal(await page.locator("style").count(), 0);
  assert.equal(
    await page.locator("[style]:not(.block)").count(),
    0,
    await page.locator('link[rel="stylesheet"][href="flowy.css"]').count(),
    1,
  );
  assert.equal(
    await page.locator('link[rel="stylesheet"][href="workflow.css"]').count(),
    0,
  );
  assert.equal(
    await page
      .locator("main")
      .evaluate((element) => getComputedStyle(element).gridTemplateColumns),
    "230px 800px 410px",
  );
  await page.selectOption("#recipe", "welcome");
  assert.equal(
    await page
      .locator("#canvas .block")
      .first()
      .evaluate((element) => getComputedStyle(element).width),
    "270px",
  );
  await page.locator("#bRun").click();
  assert.deepEqual(
    await page
      .locator("#paneRep h2")
      .last()
      .evaluate((element) => {
        const style = getComputedStyle(element);
        return [style.fontSize, style.marginBottom];
      }),
    ["14px", "8px"],
  );
  assert.equal(
    await page.locator("#paneRep .fun i").first().getAttribute("style"),
    "width:100%",
  );
  await page.locator("#tabJson").click();
  assert.deepEqual(
    await page.locator(".json-actions").evaluate((element) => {
      const style = getComputedStyle(element);
      return [style.display, style.gap, style.marginTop];
    }),
    ["flex", "8px", "8px"],
  );
  await page.page().setViewportSize({ width: 960, height: 700 });
  assert.equal(
    await page
      .locator("main")
      .evaluate((element) => getComputedStyle(element).gridTemplateColumns),
    "200px 760px",
  );
  assert.equal(
    await page
      .locator(".side")
      .evaluate((element) => getComputedStyle(element).minHeight),
    "420px",
  );
});

test("loaded recipes keep every single-step connection centred", async (t) => {
  const page = await openStudio(t);
  for (const key of Object.keys(Recipes)) {
    await page.selectOption("#recipe", key);
    const offsets = await page.evaluate(() => {
      const canvas = document.querySelector("#canvas");
      const canvasLeft = canvas.getBoundingClientRect().left;
      const centre = (id) => {
        const element = document.querySelector(
          '#canvas .blockid[value="' + id + '"]',
        ).parentElement;
        const rect = element.getBoundingClientRect();
        return rect.left + rect.width / 2 - canvasLeft;
      };
      const blocks = flowy.output().blockarr;
      return blocks
        .filter((block) => block.parent !== -1)
        .filter(
          (block) =>
            blocks.filter((other) => other.parent === block.parent).length ===
            1,
        )
        .map((block) => Math.abs(centre(block.id) - centre(block.parent)));
    });
    offsets.forEach((offset) =>
      assert.ok(offset <= 1, key + " has a child offset of " + offset + "px"),
    );
  }
});

test("every recipe round-trips and renders its simulation report", async (t) => {
  const page = await openStudio(t);
  for (const [key, recipe] of Object.entries(Recipes)) {
    await page.selectOption("#recipe", key);
    const workflow = await page.evaluate(() => workflowAgent.getWorkflow());
    assert.deepEqual(workflow, withSegment(recipe.workflow));
    assert.equal(await page.locator("#pill").textContent(), "Ready to run");
    assert.match(await page.locator("#paneChk").textContent(), /No issues/);
    const result = Core.simulate(workflow, defaultProfiles);
    assert.deepEqual(
      await page.evaluate(() => workflowAgent.simulate()),
      result,
    );
    await page.locator("#bRun").click();
    const totals = result.totals;
    const percent = (a, b) => (b ? Math.round((a / b) * 100) + "%" : "–");
    assert.deepEqual(
      await page.locator("#paneRep .kpi .num").allTextContents(),
      [
        String(totals.entered),
        percent(totals.opened, totals.received),
        percent(totals.clicked, totals.received),
        String(totals.converted),
        percent(totals.goal, totals.entered),
        "$" + totals.revenue.toLocaleString(),
      ],
    );
    assert.equal(await page.locator("#paneRep .fun i").count(), 5);
    assert.equal(await page.locator("#segTable th").count(), 7);
    const expectedMessages = [];
    Core.walk(workflow.root, (node) => {
      if (Core.NODES[node.type].channel) expectedMessages.push(node);
    });
    assert.equal(
      await page.locator("#paneRep .ncard").count(),
      expectedMessages.length,
    );
    assert.equal(await page.locator("#paneRep").isVisible(), true);
    assert.equal(await page.locator("#recipe").inputValue(), "");
  }
});

test("all configuration controls preserve values and escape message markup", async (t) => {
  const page = await openStudio(t);
  const workflow = {
    schema_version: 2,
    root: {
      id: "n1",
      type: "trigger",
      config: {
        ...Core.defaults("trigger"),
        segment_id: defaultSegment.segment_id,
      },
      children: [],
    },
  };
  let parent = workflow.root;
  for (const type of [
    "wait",
    "condition",
    "wait_event",
    "email",
    "sms",
    "push",
    "update_profile",
    "exit",
  ]) {
    const node = {
      id: "n" + (Number(parent.id.slice(1)) + 1),
      type,
      config: Core.defaults(type),
      children: [],
    };
    const branches = Core.NODES[parent.type].branches;
    if (branches) node.branch = branches[0];
    parent.children.push(node);
    parent = node;
  }
  const email = workflow.root.children[0].children[0].children[0].children[0];
  email.config.subject =
    '<img src=x onerror="throw new Error(1)"> {{first_name}} & "offer"';
  email.config.body =
    "</textarea><script>throw new Error(1)</script> {{cart_value}}";
  assert.equal(
    await page.evaluate(
      (value) => workflowAgent.setWorkflow(value).ok,
      workflow,
    ),
    true,
  );
  assert.deepEqual(
    await page.evaluate(() => workflowAgent.getWorkflow()),
    workflow,
  );
  assert.equal(await page.locator("#canvas img, #canvas script").count(), 0);
  assert.equal(
    await page.locator('#canvas .t-email [data-k="subject"]').inputValue(),
    email.config.subject,
  );
  await page.locator('#canvas .t-wait [data-k="amount"]').fill("3");
  await page.locator('#canvas .t-wait [data-k="unit"]').selectOption("hours");
  const edited = await page.evaluate(() => workflowAgent.getWorkflow());
  assert.deepEqual(edited.root.children[0].config, {
    amount: 3,
    unit: "hours",
  });
  await page.locator("#bRun").click();
  assert.equal(await page.locator("#paneRep img, #paneRep script").count(), 0);
  assert.match(
    await page.locator("#paneRep .t-email b").textContent(),
    /<img src=x/,
  );
});

test("JSON apply, validation highlighting, zoom and clear behavior remain intact", async (t) => {
  const page = await openStudio(t);
  const workflow = structuredClone(Recipes.welcome.workflow);
  workflow.root.children[0].config.subject = "";
  await page.locator("#tabJson").click();
  await page.locator("#jsonData").fill(JSON.stringify(workflow));
  await page.locator("#bApply").click();
  assert.match(await page.locator("#pill").textContent(), /1 issue to fix/);
  assert.equal(await page.locator("#canvas .is-error").count(), 1);
  assert.match(
    await page.locator("#paneChk").textContent(),
    /Subject.*required/,
  );
  await page.locator("#bRun").click();
  assert.equal(await page.locator("#paneChk").isVisible(), true);
  assert.match(
    await page.locator("#toast").textContent(),
    /Fix the errors first/,
  );
  await page.locator("#bZoomOut").click();
  assert.equal(await page.locator("#canvasZoomLevel").textContent(), "90%");
  await page.locator("#bPanCanvas").click();
  assert.equal(
    await page.locator("#bPanCanvas").getAttribute("aria-pressed"),
    "true",
  );
  await page.locator("#bResetCanvasView").click();
  assert.equal(await page.locator("#canvasZoomLevel").textContent(), "100%");
  await page.locator("#bClear").click();
  assert.equal(await page.locator("#canvas .block").count(), 1);
  assert.equal(
    await page.locator("#canvas .blockelemtype").inputValue(),
    "trigger",
  );
  assert.equal(await page.locator("#segTable th").count(), 5);
  assert.match(
    await page.locator("#paneRep").textContent(),
    /Run the campaign/,
  );
});

test("a new studio starts with the Event trigger and its segment drives the profiles", async (t) => {
  const page = await openStudio(t);
  assert.equal(await page.locator("#canvas .block").count(), 1);
  assert.equal(
    await page.locator("#canvas select[data-k='segment_id']").count(),
    0,
  );
  assert.equal(
    await page
      .locator("#canvas [data-k='segment_id']")
      .getAttribute("data-value"),
    defaultSegment.segment_id,
  );
  assert.match(
    await page.locator("#canvas [data-k='segment_id']").textContent(),
    new RegExp(defaultSegment.segment_name),
  );
  const segmentCopy = await page
    .locator("#canvas .segment-readonly-copy")
    .boundingBox();
  const segmentStatus = await page
    .locator("#canvas .segment-readonly-status")
    .boundingBox();
  assert.ok(segmentStatus.y >= segmentCopy.y + segmentCopy.height);
  assert.equal(
    await page.page().evaluate(() => window.loadedProfileSegmentId),
    defaultSegment.segment_id,
  );
  assert.equal(
    await page.evaluate(() => workflowAgent.getSegmentId()),
    defaultSegment.segment_id,
  );
  assert.equal(
    await page.locator("#segTable h2").textContent(),
    "Audience profiles",
  );
  assert.match(
    await page.locator("#paneSeg").textContent(),
    new RegExp(defaultSegment.segment_name),
  );
  assert.equal(await page.locator("#segTable tr").count(), 33);
  await page.selectOption("#recipe", "welcome");
  const editedWorkflow = await page.evaluate(() => {
    const workflow = workflowAgent.getWorkflow();
    workflow.root.config.segment_id = "another-segment";
    workflowAgent.setWorkflow(workflow);
    return workflowAgent.getWorkflow();
  });
  assert.equal(
    editedWorkflow.root.config.segment_id,
    defaultSegment.segment_id,
  );
  assert.equal(await page.locator("#segTable tr").count(), 33);
  await page.locator("#bRun").click();
  assert.equal(
    await page.locator("#paneRep .kpi .num").first().textContent(),
    "32",
  );
  assert.equal(
    await page.evaluate(
      () => workflowAgent.getWorkflow().root.config.segment_id,
    ),
    defaultSegment.segment_id,
  );
});
