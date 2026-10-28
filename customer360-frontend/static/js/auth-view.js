/* Customer 360 Admin -- authentication gate.
 *
 * Owns the full-screen login overlay (#auth-screen, see
 * static/templates/common/login-screen.html) and both login flows:
 *  - SSO_LOGIN=false: a small credential form -> POST /auth/login (root
 *    DEFAULT_ROOT_USERNAME/PASSWORD, or any active sys_user with a password
 *    set -- see customer360-api/core/routers/auth_api.py).
 *  - SSO_LOGIN=true: a "Sign in with Keycloak" button that redirects to the
 *    Authorization Code endpoint; the resulting `?code=...` on reload is
 *    exchanged for tokens via POST /auth/callback.
 *
 * main.js calls `C360.authView.init(startApp)` once templates are loaded;
 * `startApp` only runs after a session (dev or SSO) actually exists, so no
 * API calls that require auth fire before sign-in.
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var config = C360.config;
  var SSO_STATE_KEY = "c360.ssoState";
  var cachedSsoConfig = null;

  function redirectUri() {
    // Keycloak validates this against the client's registered redirect URIs
    // -- must include this admin's own origin (see .env KEYCLOAK_CALLBACK_URL
    // for the reference value; add this origin too if it differs).
    return window.location.origin + window.location.pathname;
  }

  function showScreen() { $("#auth-screen").removeClass("hidden"); }
  function hideScreen() { $("#auth-screen").addClass("hidden"); }
  function showError(message) { $("#login-error").removeClass("hidden").text(message); }
  function clearError() { $("#login-error").addClass("hidden").text(""); }
  function setBusy(busy) {
    $("#login-loading").toggleClass("hidden", !busy);
    if (busy) {
      $("#login-dev-panel, #login-sso-panel").addClass("hidden");
    }
  }

  function showDevPanel() {
    $("#login-sso-panel").addClass("hidden");
    $("#login-dev-panel").removeClass("hidden");
  }

  function showSsoPanel() {
    $("#login-dev-panel").addClass("hidden");
    $("#login-sso-panel").removeClass("hidden");
  }

  function submitDevLogin() {
    clearError();
    var username = $.trim($("#login-username").val());
    var password = $("#login-password").val();
    if (!username || !password) {
      showError("Username and password are required.");
      return;
    }
    $("#btn-login-submit").prop("disabled", true);
    $("#btn-login-submit-label").text("Signing in…");
    config.login(username, password)
      .done(function () {
        hideScreen();
        onAuthenticatedCallback();
      })
      .fail(function (xhr) {
        var detail = (xhr.responseJSON && xhr.responseJSON.detail) || "Invalid username or password.";
        showError(typeof detail === "string" ? detail : JSON.stringify(detail));
      })
      .always(function () {
        $("#btn-login-submit").prop("disabled", false);
        $("#btn-login-submit-label").text("Sign In");
      });
  }

  function startSsoRedirect() {
    if (!cachedSsoConfig) return;
    var state = Math.random().toString(36).slice(2) + Date.now().toString(36);
    try { sessionStorage.setItem(SSO_STATE_KEY, state); } catch (e) { /* ignore */ }
    window.location.href = config.buildSsoAuthorizeUrl(cachedSsoConfig, redirectUri(), state);
  }

  // Handles the `?code=&state=` Keycloak sent back to our own origin.
  function tryHandleSsoCallback() {
    var params = new URLSearchParams(window.location.search);
    var code = params.get("code");
    if (!code) return null;

    var expectedState = null;
    try { expectedState = sessionStorage.getItem(SSO_STATE_KEY); } catch (e) { expectedState = null; }
    var state = params.get("state");

    // Strip the auth params from the URL regardless of outcome so a reload
    // never tries to redeem the same (single-use) code twice.
    var cleanUrl = window.location.origin + window.location.pathname + window.location.hash;
    window.history.replaceState({}, document.title, cleanUrl);

    if (expectedState && state && expectedState !== state) {
      showScreen();
      showSsoPanel();
      showError("Login state mismatch -- please try signing in again.");
      return $.Deferred().reject().promise();
    }

    return config.exchangeSsoCode(code, redirectUri());
  }

  function bindEvents() {
    $(document).on("submit", "#login-dev-panel", function (e) {
      e.preventDefault();
      submitDevLogin();
    });
    $(document).on("click", "#btn-login-sso", startSsoRedirect);
  }

  var onAuthenticatedCallback = function () {};

  // Entry point: main.js calls this once templates are loaded, passing the
  // function that actually mounts/starts the rest of the SPA.
  function init(onAuthenticated) {
    onAuthenticatedCallback = onAuthenticated || function () {};
    bindEvents();

    var callbackPromise = tryHandleSsoCallback();
    if (callbackPromise) {
      setBusy(true);
      callbackPromise
        .done(function () {
          hideScreen();
          onAuthenticatedCallback();
        })
        .fail(function () {
          setBusy(false);
          showScreen();
          config.loadSystemMetadata().done(function (metadata) {
            cachedSsoConfig = metadata && metadata.sso_config;
            metadata && metadata.sso_login ? showSsoPanel() : showDevPanel();
          });
          showError("Could not complete sign-in with the identity provider. Please try again.");
        });
      return;
    }

    if (config.isAuthenticated()) {
      onAuthenticatedCallback();
      return;
    }

    showScreen();
    config.loadSystemMetadata().done(function (metadata) {
      if (metadata && metadata.sso_login) {
        cachedSsoConfig = metadata.sso_config;
        showSsoPanel();
      } else {
        showDevPanel();
      }
    }).fail(function () {
      // API unreachable -- default to the dev panel so local/offline dev still works.
      showDevPanel();
    });
  }

  // Logs the current session out. Dev mode now also carries a bearer token
  // (the local dev JWT -- see config.setDevSession) but has no server-side
  // session to destroy, so it just clears local state; SSO mode redirects
  // the browser through Keycloak's end-session endpoint so the identity
  // provider's own session is invalidated too. Distinguish the two by
  // `devUser` (only ever set by the dev credential flow), not by the mere
  // presence of an access token.
  function logout() {
    var idToken = config.current.idToken;
    var wasSso = !!config.current.accessToken && !config.current.devUser;
    config.logout();

    if (!wasSso) {
      location.reload();
      return;
    }

    config.api("/auth/logout", {
      id_token_hint: idToken || undefined,
      post_logout_redirect_uri: window.location.origin + window.location.pathname
    }, "POST")
      .done(function (resp) {
        window.location.href = (resp && resp.logout_url) || (window.location.origin + window.location.pathname);
      })
      .fail(function () {
        location.reload();
      });
  }

  C360.authView = { init: init, logout: logout };
})(window.C360);
