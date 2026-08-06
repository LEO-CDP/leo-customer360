/* Customer 360 Admin -- generic, reusable "data table" list component.
 *
 * This is the shared building block behind every entity listing (Master
 * Profiles, Segments, the Attribute Catalog, and any future one): a small
 * factory -- `C360.DataTableView.create(options)` -- that behaves like a
 * React list/table component. You describe *what* to render (columns,
 * how to fetch/transform rows, which DOM hooks to mount into) and this
 * file owns all of the *how* (paging state, loading/empty toggling,
 * search/select filter wiring, "load more", row-click navigation) that
 * used to be hand-duplicated in every view module.
 *
 * `columns` is the "flexible fields" part: each column is a small
 * declarative descriptor (not a hardcoded template), e.g.
 *   { label: "Domain", field: "domainLabel", capitalize: true }
 *   { label: "Status", type: "badge", field: "statusLabel", classField: "statusBadgeClass" }
 *   { label: "Profile", type: "identity", nameField: "displayName", subField: "shortId" }
 * New views (or new columns on an existing view) are just config changes
 * here -- no new Handlebars template or DOM wiring required. Cell values
 * are still rendered through Handlebars (static/templates/data-table-*.html)
 * so plain-text fields stay auto-escaped, same as before this refactor.
 *
 * Two fetch modes:
 *  - server-paginated (default): skip/limit + filter values are sent to
 *    the API on every load (mirrors the old profiles/segments behavior).
 *  - clientSide: fetches one (larger) page once and re-filters/searches
 *    it in the browser -- for APIs that don't support query filters yet
 *    (e.g. the generic CRUD router behind /profile-attributes/).
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  function buildCellVm(col, vm) {
    var cell = {
      class: [col.cellClass || "", col.muted ? "text-slate-500" : "", col.capitalize ? "capitalize" : ""].join(" ").trim(),
      isBadge: col.type === "badge",
      isIdentity: col.type === "identity"
    };

    if (col.type === "identity") {
      cell.name = vm[col.nameField];
      cell.sub = col.subField ? vm[col.subField] : null;
      cell.isTagSub = col.subStyle === "tag";
      cell.avatarContent = (col.avatarField ? vm[col.avatarField] : null) || "";
      cell.avatarBg = (col.avatarBgField ? vm[col.avatarBgField] : null) || col.avatarBg || "bg-indigo-100";
      cell.avatarColor = (col.avatarColorField ? vm[col.avatarColorField] : null) || col.avatarColor || "text-indigo-700";
      cell.avatarTextClass = col.avatarTextClass || "font-semibold text-xs";
    } else {
      cell.value = col.field ? vm[col.field] : "";
      if (col.type === "badge") cell.badgeClass = (col.classField ? vm[col.classField] : null) || col.badgeClass || "";
    }
    return cell;
  }

  function createDataTableView(options) {
    var o = $.extend({
      limit: 20,
      pagination: false,
      pageParam: "page",
      pageSizeParam: "page_size",
      itemsField: "items",
      paginationField: "pagination",
      clientSide: false,
      clientSideLimit: 500,
      rowClickable: true,
      actionLabel: "View",
      resourceLabel: "item",
      rowClass: "border-t border-slate-100 hover:bg-slate-50 transition-colors",
      extraParams: function () { return {}; }
    }, options);

    var state = {
      skip: 0,
      page: 1,
      totalPages: 1,
      totalItems: 0,
      hasNext: false,
      hasPrev: false,
      filters: {},
      allItems: null
    };
    var headRendered = false;

    function renderHead() {
      if (!o.el.thead) return;
      $(o.el.thead).html(C360.templates.render("data-table-head", {
        columns: o.columns,
        showActionColumn: o.rowClickable || !!o.onEdit
      }));
    }

    function ensureHeadRendered() {
      if (headRendered) return;
      headRendered = true;
      renderHead();
    }

    function rowVm(item) { return o.rowVm ? o.rowVm(item) : item; }

    function rowsHtml(items) {
      var rowSelectorClass = o.rowClickable && o.rowSelectorClass ? " cursor-pointer " + o.rowSelectorClass : "";
      var rows = items.map(function (item) {
        var vm = rowVm(item);
        return {
          id: o.rowId ? o.rowId(vm) : vm.id,
          rowClass: o.rowClass + rowSelectorClass,
          clickable: o.rowClickable,
          actionLabel: o.actionLabel,
          editable: !o.rowClickable && !!o.onEdit,
          editLabel: o.editLabel || "Edit",
          cells: o.columns.map(function (col) { return buildCellVm(col, vm); })
        };
      });
      return C360.templates.render("data-table-rows", { rows: rows });
    }

    function setLoading(loading) { if (o.el.loading) $(o.el.loading).toggleClass("hidden", !loading); }
    function setEmpty(empty) { if (o.el.empty) $(o.el.empty).toggleClass("hidden", !empty); }

    function updateCountLabel(count) {
      if (!o.el.countLabel) return;
      if (o.pagination) {
        var shownFrom = count > 0 ? ((state.page - 1) * o.limit + 1) : 0;
        var shownTo = count > 0 ? shownFrom + count - 1 : 0;
        $(o.el.countLabel).text(
          "Showing " + shownFrom + "-" + shownTo + " of " + state.totalItems + " " + o.resourceLabel + (state.totalItems === 1 ? "" : "s")
        );
        return;
      }
      $(o.el.countLabel).text(count + " " + o.resourceLabel + (count === 1 ? "" : "s") + " shown");
    }

    function renderPaginationUi() {
      if (!o.pagination) return;

      if (o.el.pageLabel) {
        $(o.el.pageLabel).text("Page " + state.page + " of " + state.totalPages);
      }

      if (o.el.prevBtn) {
        $(o.el.prevBtn)
          .prop("disabled", !state.hasPrev)
          .toggleClass("opacity-50 cursor-not-allowed", !state.hasPrev);
      }

      if (o.el.nextBtn) {
        $(o.el.nextBtn)
          .prop("disabled", !state.hasNext)
          .toggleClass("opacity-50 cursor-not-allowed", !state.hasNext);
      }

      if (o.el.loadMoreBtn) {
        $(o.el.loadMoreBtn).addClass("hidden");
      }
    }

    function appendItems(items, append) {
      var shouldAppend = append && !o.pagination;
      if (!shouldAppend && o.el.tbody) $(o.el.tbody).empty();
      if (o.el.tbody) $(o.el.tbody).append(rowsHtml(items));
      var total = o.el.tbody ? $(o.el.tbody).children().length : items.length;
      var countForLabel = o.pagination ? items.length : total;
      updateCountLabel(countForLabel);
      setEmpty(total === 0);
      return total;
    }

    function applyClientFilters(items) {
      var activeKeys = Object.keys(state.filters).filter(function (k) { return !!state.filters[k]; });
      if (!activeKeys.length) return items;
      return items.filter(function (item) {
        var vm = rowVm(item);
        return activeKeys.every(function (key) {
          var matcher = o.clientFilters && o.clientFilters[key];
          return matcher ? matcher(vm, state.filters[key], item) : true;
        });
      });
    }

    function loadClientSide() {
      setLoading(true);
      setEmpty(false);
      return o.fetch($.extend({ skip: 0, limit: o.clientSideLimit }, o.extraParams()))
        .done(function (items) {
          state.allItems = items;
          setLoading(false);
          if (o.onFetched) o.onFetched(items);
          appendItems(applyClientFilters(items), false);
        })
        .fail(function (xhr) { setLoading(false); if (o.onError) o.onError(xhr); });
    }

    function load(append) {
      ensureHeadRendered();

      if (o.clientSide) {
        if (state.allItems) {
          appendItems(applyClientFilters(state.allItems), false);
          return $.Deferred().resolve().promise();
        }
        return loadClientSide();
      }

      if (!append) {
        if (o.pagination) {
          state.page = 1;
          state.totalPages = 1;
          state.totalItems = 0;
          state.hasPrev = false;
          state.hasNext = false;
        } else {
          state.skip = 0;
        }
        if (o.el.tbody) $(o.el.tbody).empty();
      }
      setLoading(true);
      setEmpty(false);

      var params = $.extend({}, o.extraParams());
      if (o.pagination) {
        params[o.pageParam] = state.page;
        params[o.pageSizeParam] = o.limit;
      } else {
        params.skip = state.skip;
        params.limit = o.limit;
      }
      Object.keys(state.filters).forEach(function (key) {
        if (state.filters[key]) params[key] = state.filters[key];
      });

      return o.fetch(params)
        .done(function (response) {
          var items = response;
          setLoading(false);

          if (o.pagination) {
            var pagination = response && response[o.paginationField] ? response[o.paginationField] : null;
            items = response && response[o.itemsField] ? response[o.itemsField] : [];

            if (!pagination) {
              pagination = {
                page: state.page,
                page_size: o.limit,
                total: items.length,
                total_pages: 1,
                has_prev: state.page > 1,
                has_next: false
              };
            }

            state.page = pagination.page || state.page;
            state.totalPages = Math.max(1, pagination.total_pages || 1);
            state.totalItems = pagination.total || 0;
            state.hasPrev = !!pagination.has_prev;
            state.hasNext = !!pagination.has_next;
          }

          appendItems(items, append);
          if (o.pagination) {
            renderPaginationUi();
          } else {
            if (o.el.loadMoreBtn) $(o.el.loadMoreBtn).toggleClass("hidden", items.length < o.limit);
            state.skip += items.length;
          }
        })
        .fail(function (xhr) { setLoading(false); if (o.onError) o.onError(xhr); });
    }

    function setFilter(name, value) {
      state.filters[name] = value;
      if (o.pagination) state.page = 1;
      load(false);
    }

    // Search/select filter controls are static markup already present in the
    // DOM (unlike rows), so plain direct binding is enough -- no delegation.
    function bindSearch(selector, filterName, debounceMs) {
      var timer = null;
      $(selector).on("input", function () {
        var val = $(this).val();
        clearTimeout(timer);
        timer = setTimeout(function () { setFilter(filterName, val); }, debounceMs || 350);
      });
    }

    function bindSelect(selector, filterName) {
      $(selector).on("change", function () { setFilter(filterName, $(this).val()); });
    }

    function bindLoadMore() {
      if (o.el.loadMoreBtn) $(o.el.loadMoreBtn).on("click", function () { load(true); });
    }

    function bindPagination() {
      if (!o.pagination) return;
      if (o.el.prevBtn) {
        $(o.el.prevBtn).on("click", function () {
          if (!state.hasPrev) return;
          state.page = Math.max(1, state.page - 1);
          load(true);
        });
      }
      if (o.el.nextBtn) {
        $(o.el.nextBtn).on("click", function () {
          if (!state.hasNext) return;
          state.page = state.page + 1;
          load(true);
        });
      }
    }

    // Rows are rendered dynamically, so their click handler must be
    // delegated from a stable ancestor (document), same as before.
    function bindRowClick() {
      if (o.rowClickable && o.rowSelectorClass && o.onRowClick) {
        $(document).on("click", "." + o.rowSelectorClass, function () { o.onRowClick($(this).data("id")); });
      }
    }

    // Per-row "Edit" button (used instead of whole-row navigation for
    // catalog-style views, e.g. the Attribute Catalog) -- delegated the
    // same way since rows are re-rendered on every load.
    function bindRowEdit() {
      if (!o.onEdit) return;
      $(document).on("click", ".dtv-edit-btn", function (e) {
        e.stopPropagation();
        o.onEdit($(this).data("id"));
      });
    }

    return {
      load: load,
      setFilter: setFilter,
      bindSearch: bindSearch,
      bindSelect: bindSelect,
      bindLoadMore: bindLoadMore,
      bindPagination: bindPagination,
      bindRowClick: bindRowClick,
      bindRowEdit: bindRowEdit,
      rowVm: rowVm
    };
  }

  C360.DataTableView = { create: createDataTableView };
})(window.C360);
