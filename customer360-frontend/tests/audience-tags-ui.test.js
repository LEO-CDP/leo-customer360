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
  const profileChannels = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/templates/profile/channels.html"),
    "utf8"
  );
  const formattersJs = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/js/common/formatters.js"),
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
  assert.match(profileDetails, /id="master-profile-consent-section"/);
  assert.match(
    fs.readFileSync(
      path.join(FRONTEND_ROOT, "static/templates/metadata/attributes-list.html"),
      "utf8"
    ),
    /value="hospitality"[\s\S]*value="insurance"[\s\S]*value="manufacturing"[\s\S]*value="telecom"/
  );
  [
    ["_", "Unclassified / Not set"],
    ["banking", "Banking & Financial Services"],
    ["education", "Education"],
    ["healthcare", "Healthcare"],
    ["hospitality", "Hospitality & Food Service"],
    ["insurance", "Insurance"],
    ["manufacturing", "Manufacturing"],
    ["media", "Media & Entertainment"],
    ["real_estate", "Real Estate"],
    ["retail", "Retail & E-Commerce"],
    ["telecom", "Telecommunications"],
    ["travel", "Travel & Hospitality"],
  ].forEach(([code, label]) => {
    assert.match(profileDetails, new RegExp(`value="${code}"`));
    assert.match(formattersJs, new RegExp(`${code}: "${label.replace(/[&]/g, "\\&")}"`));
  });
  [
    "email_opt_in",
    "sms_opt_in",
    "push_opt_in",
    "zalo_opt_in",
    "whatsapp_opt_in",
    "voice_opt_in",
    "meta_ads_opt_in",
    "google_ads_opt_in",
    "tiktok_ads_opt_in",
  ].forEach((field) => assert.match(profileDetails, new RegExp(`data-consent-key="${field}"`)));
  assert.match(profileChannels, /{{#each eligibilityPolicyChannels}}/);
  assert.match(profileChannels, /Channel readiness has not been evaluated/);
  assert.doesNotMatch(profileChannels, /{{#if this}}/);

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
  const templateLoaderJs = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/js/common/templates.js"),
    "utf8"
  );
  const configJs = fs.readFileSync(
    path.join(FRONTEND_ROOT, "static/js/common/config.js"),
    "utf8"
  );
  const indexTemplate = fs.readFileSync(
    path.join(FRONTEND_ROOT, "base-templates/index.html"),
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
  assert.match(profileDetailJs, /payload\.communication_preferences = communicationPreferences/);
  assert.match(profileDetailJs, /delete communicationPreferences\.eligibility_policy/);
  assert.match(profileDetailJs, /communicationPreferences\[consentKey\] === true/);
  assert.match(profileDetailJs, /value === true/);
  assert.match(profileDetailJs, /value === false/);
  assert.match(profileDetailJs, /eligibilityPolicyChannels/);
  assert.match(profileDetailJs, /closeMasterProfileEditor\(\);\s*reload\(\);/);
  assert.match(indexTemplate, /cacheBust:\s*"{{ cache_bust }}"/);
  assert.match(configJs, /templateCacheBust:\s*serverConfig\.cacheBust/);
  assert.match(templateLoaderJs, /url \+= "\?cb=" \+ encodeURIComponent\(cacheBust\)/);
  assert.match(configJs, /function formatApiErrorDetail\(detail\)/);
  assert.match(configJs, /detail\.field && message/);
  assert.match(profileDetailJs, /formatApiErrorDetail\(detail\)/);
});

test("web SDK demo preserves signup tracking and includes opted-in marketing consent", () => {
  const demo = fs.readFileSync(
    path.join(FRONTEND_ROOT, "../docs/data-sources/c360-web-sdk-demo.html"),
    "utf8"
  );
  const executableInlineScripts = Array.from(
    demo.matchAll(/<script(?![^>]*\bsrc=)(?![^>]*\btype=)[^>]*>([\s\S]*?)<\/script>/gi),
    (match) => match[1]
  );
  assert.ok(executableInlineScripts.length > 0);
  executableInlineScripts.forEach((script) => new vm.Script(script));
  assert.match(demo, /id="alert-email-marketing-consent"[^>]*required/);
  assert.match(demo, /dataSourceId:\s*"15dc39d4-ae42-5c60-9c77-66f05dcae448"/);
  assert.match(demo, /legacyDefaultDataSourceId/);
  assert.match(demo, /const marketingConsent = \$\('#alert-email-marketing-consent'\)\.is\(':checked'\)/);
  assert.match(demo, /if \(!marketingConsent\)/);
  assert.match(demo, /conversion_type: 'newsletter_signup'/);
  assert.match(demo, /Đã đăng ký thành công \(demo\)/);
  const signupHandler = demo.split("function initLotteryAlertForm() {")[1]
    .split("function initProductTracking() {")[0];
  assert.match(signupHandler, /C360DemoSDK\.trackFormSubmit\('lottery-alert'/);
  assert.match(signupHandler, /email_opt_in:\s*true/);
  assert.match(signupHandler, /C360DemoSDK\.identifyUser\(subscriber,\s*'signup'\)/);
  assert.match(signupHandler, /C360DemoSDK\.trackConversion/);
  assert.doesNotMatch(signupHandler, /window\.leoTrackingConsent === true/);
  assert.match(demo, /Demo chỉ ghi event, không gửi email hoặc cập nhật hồ sơ/);
});
