"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function currentUser(payload) {
  const source = fs.readFileSync(path.join(__dirname, "../static/js/common/config.js"), "utf8");
  const token = "header." + Buffer.from(JSON.stringify(payload)).toString("base64url") + ".signature";
  const context = {
    window: { C360: {}, C360_SERVER_CONFIG: { accessToken: token } },
    localStorage: { getItem(key) { return key === "c360.accessToken" ? token : null; } },
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    setTimeout() {}, clearTimeout() {},
  };
  vm.runInNewContext(source, context);
  return context.window.C360.config.currentUser();
}

test("platform_admin is root-equivalent in every supported Keycloak claim", () => {
  for (const payload of [
    { roles: ["platform_admin"] },
    { realm_access: { roles: ["platform_admin"] } },
    { resource_access: { c360: { roles: ["platform_admin"] } } },
    { roles: [null, 12, " PLATFORM_ADMIN "] },
  ]) {
    assert.equal(currentUser(payload).isRoot, true);
  }
});

test("other admin roles keep their identity without root promotion", () => {
  for (const role of ["admin", "tenant_admin", "super_admin", "system_admin", "viewer"]) {
    assert.equal(currentUser({ realm_access: { roles: [role] } }).isRoot, false);
  }
  assert.equal(currentUser({ roles: ["root"] }).isRoot, true);
  assert.equal(currentUser({ roles: "platform_admin" }).isRoot, false);
});
