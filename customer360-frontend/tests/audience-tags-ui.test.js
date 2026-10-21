"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const FRONTEND_ROOT = path.resolve(__dirname, "..");

test("profile and segment templates include IAB audience_tags detail and editor controls", () => {
  const segmentsList = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/templates/segment/segments-list.html"),
    "utf8"
  );
  const segmentModal = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/templates/segment/segment-form-modal.html"),
    "utf8"
  );
  const segmentDetails = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/templates/segment/segment-details.html"),
    "utf8"
  );
  const profileDetails = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/templates/profile/profile-details.html"),
    "utf8"
  );
  const profileSegmentsCard = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/templates/profile/segments.html"),
    "utf8"
  );

  assert.match(segmentsList, /id="segments-sort-filter"/);
  assert.match(segmentsList, /<option value="created_desc" selected>Sort: Created On \(Newest\)<\/option>/);

  assert.match(segmentModal, /id="segment-form-audience-tags-section"/);
  assert.match(segmentModal, /id="segment-form-selected-audience-tags"/);
  assert.match(segmentModal, /id="segment-form-audience-tag-options"/);
  assert.match(segmentModal, /id="btn-segment-apply-audience-tags-rule"/);

  assert.match(segmentDetails, /id="segment-detail-audience-tags-list"/);
  assert.match(segmentDetails, /id="btn-segment-detail-manage-tags"/);
  assert.match(segmentDetails, /btn-segment-detail-remove-audience-tag/);

  assert.match(profileDetails, /id="master-profile-edit-audience-tags-section"/);
  assert.match(profileDetails, /id="profile-edit-selected-audience-tags"/);
  assert.match(profileDetails, /id="profile-edit-audience-tag-options"/);
  assert.match(profileDetails, /id="profile-edit-audience-tag-search"/);

  assert.match(profileSegmentsCard, /id="profile-detail-audience-tags-list"/);
  assert.match(profileSegmentsCard, /btn-edit-master-profile-audience-tags/);
  assert.match(profileSegmentsCard, /btn-profile-detail-remove-audience-tag/);
});

test("segments-view.js and profile-detail-view.js parse cleanly and wire audience_tags SQL and payloads", () => {
  const segmentsJs = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/js/segments-view.js"),
    "utf8"
  );
  const profileDetailJs = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/js/profile-detail-view.js"),
    "utf8"
  );

  // Verify both scripts compile without syntax errors
  new vm.Script(segmentsJs, { filename: "segments-view.js" });
  new vm.Script(profileDetailJs, { filename: "profile-detail-view.js" });

  // Verify segment builder loads audience_tags from /audience-tags/ with has_parent=true and generates array SQL
  assert.match(segmentsJs, /api\("\/audience-tags\/",\s*\{\s*limit:\s*2000,\s*seed_if_empty:\s*true,\s*has_parent:\s*true\s*\}\)/);
  assert.match(segmentsJs, /ARRAY\[" \+ quotedTags \+ "\]::text\[\]/);
  assert.match(segmentsJs, /audience_tags: segmentFormAudienceTags\.slice\(\)/);

  // Verify profile detail view loads /audience-tags/ with has_parent=true and saves audience_tags in PATCH /master-profiles/
  assert.match(profileDetailJs, /api\("\/audience-tags\/",\s*\{\s*limit:\s*2000,\s*seed_if_empty:\s*true,\s*has_parent:\s*true\s*\}\)/);
  assert.match(profileDetailJs, /payload\.audience_tags = profileEditAudienceTags\.slice\(\)/);
  assert.match(profileDetailJs, /closeMasterProfileEditor\(\);\s*reload\(\);/);
});
