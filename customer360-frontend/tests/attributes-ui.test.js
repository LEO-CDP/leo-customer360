"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const FRONTEND_ROOT = path.resolve(__dirname, "..");

test("attributes view has exactly two primary catalog tabs and section-specific controls", () => {
  const template = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/templates/metadata/attributes-list.html"),
    "utf8"
  );
  const script = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/js/attributes-view.js"),
    "utf8"
  );

  const primaryTabs = Array.from(template.matchAll(/data-attributes-tab=/g));
  assert.equal(primaryTabs.length, 2);
  assert.match(template, /data-attributes-tab="profile"/);
  assert.match(template, /data-attributes-tab="behavior"/);
  assert.match(template, /id="attributes-profile-filters"/);
  assert.match(template, /id="attributes-behavior-filters"/);
  assert.match(template, /id="behavior-add-category"/);
  assert.match(template, /id="attribute-delete-modal"/);
  assert.match(template, /id="attribute-delete-confirmation"/);
  assert.match(template, /Type <span[^>]*>yes<\/span> to confirm/);
  assert.match(template, /cdp_profile_attributes/);
  assert.match(script, /cdp_event_catalog/);
  assert.match(script, /api\("\/profile-attributes\/"/);
  assert.match(script, /api\("\/event-catalog\/"/);
  assert.match(script, /activeSection === "behavior" \? behaviorTable : profileTable/);
  assert.match(script, /onSecondaryAction: function \(id\) \{ deleteBehaviorEvent\(id\); \}/);
  assert.match(script, /function canManageAttributes\(\)/);
  assert.match(script, /allowedRoles = \["root", "platform_admin", "super_admin", "admin"\]/);
  assert.match(script, /actionsVisible: function \(\) \{ return canManageAttributes\(\); \}/);
  assert.match(script, /function openDeleteModal\(item\)/);
  assert.match(script, /toLowerCase\(\) === "yes"/);
  assert.match(script, /avatarIconField: "attributeIcon"/);
  assert.match(script, /avatarIconField: "behaviorIcon"/);
  assert.match(script, /subField: "codeLabel", subStyle: "tag", descriptionField: "description"/);
  assert.doesNotMatch(script, /\{ label: "Code", icon: "bi-code-slash"/);
  assert.match(script, /compactTable: true/);
  assert.match(script, /compactActions: true/);
  const rowsTemplate = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/templates/common/data-table-rows.html"),
    "utf8"
  );
  assert.match(rowsTemplate, /h-9 min-w-9 items-center justify-center rounded-lg/);
  assert.match(rowsTemplate, /aria-label="\{\{editLabel\}\}"/);
  assert.match(rowsTemplate, /title="\{\{secondaryActionLabel\}\}"/);
  const dataTableJs = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/js/common/data-table-view.js"),
    "utf8"
  );
  assert.match(dataTableJs, /rowClickable: true,\s*compactActions: true/);
  assert.doesNotMatch(script, /window\.confirm/);
  new vm.Script(script, { filename: "attributes-view.js" });
});
