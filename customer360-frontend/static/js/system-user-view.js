/* Customer 360 Admin -- System Users view (sys_user).
 *
 * Modeled directly on scoring-model-view.js: a real consumer of the shared
 * C360.DataTableView component (static/js/common/data-table-view.js) and the
 * same Add/Edit modal pattern. Unlike scoring models, GET /users returns a
 * paginated envelope ({ total, skip, limit, items }) rather than a bare
 * array, so `fetch` unwraps `.items` before handing rows to the table (see
 * campaign-view.js for the same unwrap idiom). Filtering/searching happens
 * client-side since the API only supports skip/limit/status_filter.
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var STATUS_BADGE_CLASSES = {
    ACTIVE: "bg-green-100 text-green-700",
    INACTIVE: "bg-slate-100 text-slate-500"
  };
  function statusBadgeClass(v) { return STATUS_BADGE_CLASSES[v] || "bg-slate-100 text-slate-500"; }

  function rowVm(u) {
    return $.extend({}, u, {
      initials: fmt.initials(u.full_name || u.username),
      subLabel: u.email || "\u2014",
      statusLabel: fmt.titleCase(u.status),
      statusBadgeClass: statusBadgeClass(u.status),
      jobLabel: u.job_title || "\u2014",
      departmentLabel: u.department || "\u2014",
      lastLoginLabel: fmt.dateTime(u.last_login_at),
      createdLabel: fmt.dateTime(u.created_at)
    });
  }

  var dtv = C360.DataTableView.create({
    columns: [
      {
        label: "User", type: "identity", nameField: "full_name", subField: "username", subStyle: "tag",
        avatarField: "initials", avatarBg: "bg-violet-100", avatarColor: "text-violet-700"
      },
      { label: "Email", field: "subLabel" },
      { label: "Job Title", field: "jobLabel" },
      { label: "Department", field: "departmentLabel" },
      { label: "Status", type: "badge", field: "statusLabel", classField: "statusBadgeClass" },
      { label: "Last Login", field: "lastLoginLabel" }
    ],
    rowVm: rowVm,
    rowId: function (vm) { return vm.user_id; },
    rowClickable: false, // no dedicated detail page -- Edit modal covers view+edit+deactivate
    onEdit: function (id) { openEditSystemUserModal(id); },
    editLabel: "Edit",
    resourceLabel: "system user",
    clientSide: true,
    clientSideLimit: 500,
    fetch: function (params) {
      return api("/users", params).then(function (data) { return (data && data.items) ? data.items : []; });
    },
    clientFilters: {
      q: function (vm, value) {
        var needle = value.toLowerCase();
        return (vm.username || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.email || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.full_name || "").toLowerCase().indexOf(needle) !== -1;
      },
      status: function (vm, value) { return vm.status === value; }
    },
    onFetched: function (items) { systemUsersById = {}; items.forEach(function (u) { systemUsersById[u.user_id] = u; }); },
    onError: function (xhr) { showApiError("loading system users", xhr); },
    el: {
      thead: "#system-users-thead",
      tbody: "#system-users-tbody",
      loading: "#system-users-loading",
      empty: "#system-users-empty",
      countLabel: "#system-users-count-label"
    }
  });

  var systemUsersById = {}; // last-fetched rows, keyed by user_id -- backs the Edit modal

  function load() { return dtv.load(false); }

  // Non-null while the modal is editing an existing row (its user_id) --
  // null means "creating a new row".
  var editingUserId = null;

  function openAddSystemUserModal() {
    editingUserId = null;
    $("#system-user-form-title").text("Add System User");
    $("#system-user-form-subtitle").text("Creates a new row in the sys_user table");
    $("#system-user-form-save-label").text("Save User");
    $("#btn-system-user-delete").addClass("hidden");
    $("#system-user-add-error").addClass("hidden").text("");
    $("#system-user-add-username").val("").prop("disabled", false);
    $("#system-user-add-full-name").val("");
    $("#system-user-add-email").val("");
    $("#system-user-add-phone").val("");
    $("#system-user-add-job-title").val("");
    $("#system-user-add-department").val("");
    $("#system-user-add-status").val("ACTIVE");
    $("#system-user-password-fields").removeClass("hidden");
    $("#system-user-add-password").val("");
    $("#system-user-add-password-confirm").val("");
    $("#system-user-form-modal").removeClass("hidden");
  }

  function openEditSystemUserModal(userId) {
    var u = systemUsersById[userId];
    if (!u) return;
    editingUserId = userId;
    $("#system-user-form-title").text("Edit System User");
    $("#system-user-form-subtitle").text("Updates this sys_user row");
    $("#system-user-form-save-label").text("Save Changes");
    $("#btn-system-user-delete").removeClass("hidden");
    $("#system-user-add-error").addClass("hidden").text("");
    // username is immutable once created -- shown for context but not editable.
    $("#system-user-add-username").val(u.username).prop("disabled", true);
    $("#system-user-add-full-name").val(u.full_name || "");
    $("#system-user-add-email").val(u.email || "");
    $("#system-user-add-phone").val(u.phone || "");
    $("#system-user-add-job-title").val(u.job_title || "");
    $("#system-user-add-department").val(u.department || "");
    $("#system-user-add-status").val(u.status);
    // Password is only settable on create -- UserUpdate has no such field.
    $("#system-user-password-fields").addClass("hidden");
    $("#system-user-add-password").val("");
    $("#system-user-add-password-confirm").val("");
    $("#system-user-form-modal").removeClass("hidden");
  }

  function closeSystemUserModal() {
    $("#system-user-form-modal").addClass("hidden");
  }

  function submitSystemUserForm() {
    var $error = $("#system-user-add-error");
    $error.addClass("hidden").text("");

    var isEdit = editingUserId !== null;
    var username = $.trim($("#system-user-add-username").val());
    if (!isEdit && !username) {
      $error.removeClass("hidden").text("Username is required.");
      return;
    }

    if (!isEdit) {
      var password = $("#system-user-add-password").val();
      var passwordConfirm = $("#system-user-add-password-confirm").val();
      if (!password || password.length < 8) {
        $error.removeClass("hidden").text("Password must be at least 8 characters.");
        return;
      }
      if (password !== passwordConfirm) {
        $error.removeClass("hidden").text("Password and confirmation do not match.");
        return;
      }
    }

    var payload = {
      full_name: $.trim($("#system-user-add-full-name").val()) || null,
      email: $.trim($("#system-user-add-email").val()) || null,
      phone: $.trim($("#system-user-add-phone").val()) || null,
      job_title: $.trim($("#system-user-add-job-title").val()) || null,
      department: $.trim($("#system-user-add-department").val()) || null,
      status: $("#system-user-add-status").val()
    };

    var request;
    if (isEdit) {
      request = api("/users/" + encodeURIComponent(editingUserId), payload, "PATCH");
    } else {
      payload.username = username;
      payload.password = $("#system-user-add-password").val();
      request = api("/users", payload, "POST");
    }

    request
      .done(function () {
        closeSystemUserModal();
        load();
      })
      .fail(function (xhr) {
        var detail = (xhr.responseJSON && xhr.responseJSON.detail) || ("Could not " + (isEdit ? "update" : "create") + " system user.");
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : JSON.stringify(detail));
      });
  }

  function deactivateSystemUser() {
    if (!editingUserId) return;
    var u = systemUsersById[editingUserId];
    var label = u && (u.full_name || u.username) ? (u.full_name || u.username) : editingUserId;
    if (!window.confirm("Deactivate system user '" + label + "'?")) return;

    api("/users/" + encodeURIComponent(editingUserId), {}, "DELETE")
      .done(function () {
        closeSystemUserModal();
        load();
      })
      .fail(function (xhr) { showApiError("deactivating system user", xhr); });
  }

  function bindEvents() {
    dtv.bindSearch("#system-users-search-input", "q", 300);
    dtv.bindSelect("#system-users-status-filter", "status");
    dtv.bindRowEdit();

    $(document).on("click", "#btn-system-user-add", openAddSystemUserModal);
    $(document).on("click", "#btn-system-user-add-cancel", closeSystemUserModal);
    $(document).on("click", "#btn-system-user-add-save", submitSystemUserForm);
    $(document).on("click", "#btn-system-user-delete", deactivateSystemUser);
    $(document).on("click", "#system-user-form-modal", function (e) {
      if (e.target === this) closeSystemUserModal();
    });
  }

  // Owns the "/admin/users" tab/route (see router.js / placeholder-view.js).
  C360.router.define("/admin/users", {
    section: "view-admin",
    tab: "admin",
    mount: function () { load(); }
  });

  C360.systemUserView = { load: load, bindEvents: bindEvents };
})(window.C360);
