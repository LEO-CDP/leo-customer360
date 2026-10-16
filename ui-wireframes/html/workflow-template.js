(function (root) {
  "use strict";

  const Handlebars = root.Handlebars;
  if (!Handlebars) throw new Error("Workflow templates require Handlebars.");

  const icons = {
    segment:
      '<svg viewBox="0 0 16 16"><circle cx="6" cy="5" r="2.5"/><path d="M1.5 13.5c0-2.6 2-4 4.5-4s4.5 1.4 4.5 4"/><circle cx="12" cy="6" r="1.8"/><path d="M12 10c1.7 0 2.8 1 2.8 3"/></svg>',
    condition:
      '<svg viewBox="0 0 16 16"><path d="M8 14.5V8.5M8 8.5L3.5 3M8 8.5L12.5 3"/></svg>',
    email:
      '<svg viewBox="0 0 16 16"><rect x="1.5" y="3" width="13" height="10" rx="1.6"/><path d="M2 4.2l6 4.8 6-4.8"/></svg>',
    push:
      '<svg viewBox="0 0 16 16"><path d="M3.8 11V7.2a4.2 4.2 0 018.4 0V11l1.2 1.5H2.6z"/><path d="M6.5 14.3a1.6 1.6 0 003 0"/></svg>',
  };

  Handlebars.registerHelper("workflowIcon", (type) =>
    new Handlebars.SafeString(icons[type] || ""),
  );

  const source = {
    builder: `
      <div class="cw">
        <header class="cw-top">
          <div class="cw-title"><b data-r="title">Campaign workflow</b><span data-r="chips"></span></div>
          <div class="cw-actions">
            <span class="cw-pill" data-r="valid"></span>
            <span class="cw-dirty" data-r="dirty" hidden>Unsaved changes</span>
            <button class="cw-btn" data-a="preview">Preview audience</button>
            <button class="cw-btn cw-primary" data-a="save">Save workflow</button>
          </div>
        </header>
        <div class="cw-body">
          <aside class="cw-pal">
            <h3>Blocks</h3>
            {{#each palette}}
              <div class="cw-pitem cw-t-{{type}} create-flowy" data-type="{{type}}" role="button" tabindex="0" aria-label="Add {{label}}">
                <input type="hidden" name="blockelemtype" class="blockelemtype" value="{{code}}">
                <span class="cw-pi">{{{workflowIcon type}}}</span>
                <span><b>{{label}}</b><small>{{description}}</small></span>
              </div>
            {{/each}}
            <p class="cw-help">Drag a block onto the canvas, or select a node and click a block to add it below. Each If / else has a Yes and a No branch. Send blocks end a branch.</p>
          </aside>
          <div class="cw-stage">
            <div class="cw-canvas" id="cw-canvas"></div>
            <div class="cw-empty" data-r="empty">Drag <b>Customer segment</b> here to start<br><small>or select it in the left panel</small></div>
            <div class="cw-busy" data-r="busy" hidden><span></span>Building workflow…</div>
          </div>
          <section class="cw-side">
            <nav class="cw-tabs" role="tablist">
              {{#each tabs}}<button role="tab" data-tab="{{id}}">{{label}}</button>{{/each}}
            </nav>
            <div class="cw-pane" data-r="pane"></div>
          </section>
        </div>
        <div class="cw-toast" data-r="toast" role="status"></div>
      </div>`,
    nodeBody: `
      <div class="cw-nh">
        <span class="cw-ni">{{{workflowIcon type}}}</span>
        <span class="cw-nt">{{label}}</span>
        {{#if branch}}<span class="cw-br cw-{{branch}}">{{branchLabel}}</span>{{/if}}
      </div>
      <div class="cw-ntitle">{{title}}</div>
      <div class="cw-nsub">{{subtitle}}</div>
      {{#if hasCounts}}
        <div class="cw-nc">{{reach}} reach{{#if isAction}} · {{deliverable}} deliverable{{/if}}</div>
      {{/if}}`,
    workflowNode: `
      <input type="hidden" name="blockelemtype" class="blockelemtype" value="{{code}}">
      <input type="hidden" name="blockid" class="blockid" value="{{blockId}}">
      <div class="cw-nb"></div>`,
    demoFlowyBlock: `
      <input type="hidden" name="blockelemtype" class="blockelemtype" value="{{code}}">
      <input type="hidden" name="blockid" class="blockid" value="{{blockId}}">
      {{{body}}}`,
    chips: `{{#each chips}}<i class="cw-chip">{{this}}</i>{{/each}}`,
    issues: `<ul class="cw-issues">{{#each issues}}<li class="{{level}}">{{msg}}</li>{{/each}}</ul>`,
    checklistInspector: `
      <h4>Checklist</h4>
      {{#if hasIssues}}{{{issuesHtml}}}{{else}}<p class="cw-ok">No issues found. The workflow is ready to save.</p>{{/if}}
      <p class="cw-mute">Select a node to edit it.</p>`,
    inspector: `
      <div class="cw-ih">
        <span class="cw-ni cw-t-{{type}}">{{{workflowIcon type}}}</span>
        <div><b>{{label}}</b><small>{{id}}</small></div>
      </div>
      {{#if issuesHtml}}{{{issuesHtml}}}{{/if}}
      {{#if isSegment}}
        <label>Segment
          <select data-f="segment_id">
            {{#each segments}}
              <option value="{{segment_id}}" {{#if selected}}selected{{/if}}>{{segment_name}}</option>
            {{/each}}
          </select>
        </label>
        {{#if segment}}
          <dl class="cw-dl">
            <dt>Tag</dt><dd><code>{{segment.segment_tag}}</code></dd>
            <dt>Members</dt><dd>{{memberCount}}</dd>
            <dt>Built by</dt><dd>{{segment.processed_by}}</dd>
            <dt>Description</dt><dd>{{segment.description}}</dd>
          </dl>
        {{/if}}
      {{/if}}
      {{#if isCondition}}
        <label>Field
          <select data-f="field">
            {{#each fields}}<option value="{{code}}" {{#if selected}}selected{{/if}}>{{name}}</option>{{/each}}
          </select>
        </label>
        <label>Operator
          <select data-f="op">
            {{#each operators}}<option value="{{value}}" {{#if selected}}selected{{/if}}>{{label}}</option>{{/each}}
          </select>
        </label>
        {{#if booleanValue}}
          <label>Value
            <select data-f="value">
              <option value="true" {{#if valueIsTrue}}selected{{/if}}>Yes</option>
              <option value="false" {{#if valueIsFalse}}selected{{/if}}>No</option>
            </select>
          </label>
        {{else if optionValues.length}}
          <label>Value
            <select data-f="value">
              {{#each optionValues}}<option value="{{value}}" {{#if selected}}selected{{/if}}>{{label}}</option>{{/each}}
            </select>
          </label>
        {{else}}
          <label>Value {{#if hint}}<small>({{hint}})</small>{{/if}}
            <input data-f="value" {{#if numericValue}}type="number" step="any"{{else}}type="text" placeholder="{{placeholder}}"{{/if}} value="{{value}}">
          </label>
        {{/if}}
        <p class="cw-say"><b>{{conditionSummary}}</b><br><small>Yes goes left, No goes right. Profiles with no value for this field follow No.</small></p>
      {{/if}}
      {{#if isAction}}
        <label>Template
          <select data-f="template_id">
            <option value="">Choose a template</option>
            {{#each templates}}
              <option value="{{template_id}}" {{#if selected}}selected{{/if}} {{#if disabled}}disabled{{/if}}>{{name}}{{#if statusNote}} ({{status}}){{/if}}</option>
            {{/each}}
          </select>
        </label>
        {{#if emailPreview}}
          <div class="cw-prev">
            <div class="cw-subj">{{subject}}</div>
            {{#if htmlEmail}}<iframe sandbox title="Email preview" srcdoc="{{htmlEmail}}"></iframe>{{else}}<pre>{{messageBody}}</pre>{{/if}}
          </div>
        {{/if}}
        {{#if pushPreview}}
          <div class="cw-push"><span class="cw-pi">{{{workflowIcon "push"}}}</span><div><b>{{subject}}</b><br>{{messageBody}}</div></div>
        {{/if}}
        <p class="cw-mute">Preview uses sample values. Real values are merged at send time.</p>
      {{/if}}
      {{#if branchOptions}}
        <label>Branch
          <select data-f="branch">
            <option value="yes" {{#if branchIsYes}}selected{{/if}}>Yes</option>
            <option value="no" {{#if branchIsNo}}selected{{/if}}>No</option>
          </select>
        </label>
      {{/if}}
      <div class="cw-row"><button class="cw-btn cw-danger" data-a="delete">Delete {{deleteLabel}}</button></div>`,
    audienceEmpty: `
      <h4>Audience preview</h4>
      <p class="cw-mute">Shows how many profiles reach each block and how many can actually be messaged after consent and suppression checks.</p>
      <button class="cw-btn cw-primary" data-a="preview">Preview audience</button>`,
    audienceResults: `
      <h4>Audience preview</h4>
      <div class="cw-kpis">
        <div><b>{{total}}</b><small>in segment</small></div>
        <div><b>{{unrouted}}</b><small>not routed</small></div>
      </div>
      <table class="cw-table">
        <tr><th>Send block</th><th>Reach</th><th>Deliverable</th><th>Rate</th></tr>
        {{#each rows}}
          <tr><td>{{name}}<small>{{channel}} · {{id}}</small></td><td>{{reach}}</td><td>{{deliverable}}</td><td>{{rate}}</td></tr>
        {{/each}}
      </table>
      <p class="cw-mute">Deliverable = has an email or push token, opted in, and not on the suppression list.</p>
      <button class="cw-btn" data-a="preview">Refresh</button>`,
    sqlUnavailable: `<h4>Generated SQL</h4><p class="cw-mute">Fix the issues in the checklist to generate SQL.</p>`,
    sqlCode: `<pre class="cw-code">{{sql}}</pre><div class="cw-params">params: {{params}}</div>`,
    sqlResults: `
      <h4>Generated SQL</h4>
      <p class="cw-mute">One parameterised query per send block. Values are bound parameters, never concatenated.</p>
      {{#each statements}}
        <details open>
          <summary>{{nodeId}} · {{channel}} · {{templateName}}</summary>
          <h5>Audience</h5>{{{audienceCode}}}
          <h5>Activation (writes the dispatch ledger)</h5>{{{activationCode}}}
        </details>
      {{/each}}
      <button class="cw-btn" data-a="copysql">Copy all SQL</button>`,
    reportLoading: `<h4>Campaign report</h4><p class="cw-mute">Loading…</p>`,
    workflowJson: `
      <h4>Workflow JSON</h4>
      <p class="cw-mute">Stored in <code>crm_campaign.metadata.workflow</code>. Edit and apply to rebuild the canvas.</p>
      <textarea class="cw-json" spellcheck="false">{{workflowJson}}</textarea>
      <div class="cw-row"><button class="cw-btn" data-a="copyjson">Copy</button><button class="cw-btn cw-primary" data-a="applyjson">Apply JSON</button></div>`,
    sparkChart: `
      <svg viewBox="0 0 {{width}} {{height}}" class="cw-spark" role="img" aria-label="{{label}}">
        {{#each bars}}<rect x="{{x}}" y="{{y}}" width="{{width}}" height="{{height}}" rx="1.5" fill="{{color}}"/>{{/each}}
      </svg>`,
    campaignReport: `
      <h4>Campaign performance</h4>
      <div class="cw-kpis">
        {{#each kpis}}<div><b>{{value}}</b><small>{{label}}{{#if detail}} · {{detail}}{{/if}}</small></div>{{/each}}
      </div>
      <div class="cw-sparks">
        {{#each charts}}<div><small>{{label}} per day</small>{{{chart}}}</div>{{/each}}
      </div>
      <h4>Delivery by send block</h4>
      <table class="cw-table"><tr><th>Send block</th>{{#each statuses}}<th>{{this}}</th>{{/each}}</tr>
        {{#each dispatchRows}}
          <tr><td>{{label}}<small>{{id}}</small></td>{{#each counts}}<td>{{this}}</td>{{/each}}</tr>
        {{/each}}
      </table>
      <p class="cw-mute">Totals come from <code>vw_campaign_performance_metrics</code>; delivery from <code>cdp_campaign_dispatch_logs</code>.</p>
      <button class="cw-btn" data-a="reload">Reload report</button>`,
    flowyError: `<p style="padding:16px;color:#b42318">{{message}}</p>`,
    demoBlockSegment: `
      <div class="bh"><span class="block-title">Customer segment</span><span class="hits"></span></div>
      <div class="d" style="font-size:12px;color:#667090">Cart abandoners, last 14 days<br><b class="seglen">{{profileCount}} profiles</b></div>`,
    demoBlockCondition: `
      <div class="bh"><span class="block-title">If / else</span><span class="hits"></span><span class="branch"></span></div>
      <select class="f-field">{{#each fields}}<option value="{{value}}">{{label}}</option>{{/each}}</select>
      <div class="row2"><select class="f-op">{{#each operators}}<option value="{{value}}">{{label}}</option>{{/each}}</select><input class="f-val" type="number" value="50"></div>`,
    demoBlockEmail: `
      <div class="bh"><span class="block-title">Email</span><span class="hits"></span><span class="branch"></span></div>
      <input class="f-a" placeholder="Subject" value="{{subject}}">
      <textarea class="f-b" placeholder="Body template">{{body}}</textarea>`,
    demoBlockPush: `
      <div class="bh"><span class="block-title">Web push</span><span class="hits"></span><span class="branch"></span></div>
      <input class="f-a" placeholder="Title" value="{{subject}}">
      <textarea class="f-b" placeholder="Body template">{{body}}</textarea>`,
    demoSegmentTable: `
      <h2>Cart abandoners, last 14 days</h2>
      <p class="sub">{{profileCount}} customer profiles enter the workflow.</p>
      <table>
        <tr><th>Name</th><th>Score</th><th>Cart</th><th>Days</th><th>Push</th>{{#if hasOutcome}}<th>Outcome</th>{{/if}}</tr>
        {{#each profiles}}
          <tr><td>{{name}}</td><td>{{score}}</td><td>&dollar;{{cart}}</td><td>{{recency}}</td><td>{{pushLabel}}</td>{{#if hasOutcome}}<td>{{outcome}}</td>{{/if}}</tr>
        {{/each}}
      </table>`,
    demoReportEmpty: `<div class="empty">No profile reached an email or push block. Add a send block to a branch and run again.</div>`,
    demoReport: `
      <h2>Campaign performance</h2>
      <p class="sub">Simulated results for {{profileCount}} profiles. {{noActionCount}} had no action.</p>
      <div class="kpis">
        {{#each kpis}}<div class="kpi"><div class="num">{{value}}</div><small>{{label}}</small></div>{{/each}}
      </div>
      <div class="fun">
        {{#each funnel}}<div><span>{{label}}</span><i style="width:{{width}}%"></i><span>{{value}}</span></div>{{/each}}
      </div>
      <h2 style="font-size:14px;margin-bottom:8px">By message</h2>
      {{#each messages}}
        <div class="ncard t{{type}}">
          <b>{{channel}}: {{subject}}</b>
          <div class="m">
            <span>{{sent}} profiles</span><span>{{delivered}} delivered</span>
            <span>{{openRate}} opened</span><span>{{clickRate}} clicked</span>
            <span>{{conversions}} converted</span><span>&dollar;{{revenue}} revenue</span>
          </div>
          <p>{{body}}</p>
        </div>
      {{/each}}`,
    demoReportIntro: `<div class="empty">Run the campaign to see delivery, engagement and revenue.</div>`,
  };

  const templates = {};
  Object.keys(source).forEach((name) => {
    templates[name] = Handlebars.compile(source[name]);
  });
  root.WorkflowTemplates = templates;
})(typeof window === "undefined" ? globalThis : window);