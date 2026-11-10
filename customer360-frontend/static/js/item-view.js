/* Customer 360 Admin -- tenant-scoped content and product catalog. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var api = C360.config.api;
  var showApiError = C360.config.showApiError;
  var fmt = C360.fmt;
  var activeTab = "content";
  var selectedFile = null;
  var importKind = "product";
  var editingItem = null;
  var promotionSelection = null;
  var promotionBusy = false;
  var promotionReturnFocus = null;
  var promotionOptions = null;
  var promotionOptionsLoading = false;
  var promotionOptionsRequest = 0;
  var contentById = {};
  var productsById = {};
  var MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
  var REQUIRED_TSV_COLUMNS = [
    "Product_Type", "Store_ID", "Product_ID_Type", "Product_ID", "Name", "Full_URL"
  ];
  var REQUIRED_CONTENT_TSV_COLUMNS = ["Domain", "Item_Type", "Title"];
  var ALLOWED_CONTENT_TSV_COLUMNS = REQUIRED_CONTENT_TSV_COLUMNS.concat([
    "Summary", "Image_URL", "CTA_Label", "CTA_URL", "Segment_Tags",
    "Published_At", "Status_Code"
  ]);

  function statusLabel(code) { return Number(code) === 1 ? "Active" : "Inactive"; }
  function statusClass(code) {
    return Number(code) === 1 ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600";
  }
  function typeClass(type) {
    return {
      article: "bg-violet-100 text-violet-700",
      news: "bg-sky-100 text-sky-700",
      video: "bg-rose-100 text-rose-700",
      product: "bg-emerald-100 text-emerald-700"
    }[type] || "bg-slate-100 text-slate-600";
  }
  function domainLabel(domain) { return fmt.domainLabel(domain || "all"); }
  function tagLabel(tags) {
    return Array.isArray(tags) && tags.length ? tags.join(", ") : "—";
  }
  function priceLabel(price, currency) {
    if (price === null || price === undefined || price === "") return "—";
    return (currency ? currency + " " : "") + Number(price).toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2
    });
  }
  function fileSizeLabel(size) {
    if (size < 1024 * 1024) return Math.ceil(size / 1024) + " KiB";
    return (size / (1024 * 1024)).toFixed(1) + " MiB";
  }
  function rowId(item) { return item.content_item_id || item.product_item_id; }

  function contentVm(item) {
    contentById[String(item.content_item_id)] = item;
    return $.extend({}, item, {
      typeLabel: fmt.titleCase(item.item_type || ""),
      typeBadgeClass: typeClass(item.item_type),
      domainLabel: domainLabel(item.domain),
      statusLabel: statusLabel(item.status_code),
      statusBadgeClass: statusClass(item.status_code),
      tagsLabel: tagLabel(item.segment_tags),
      updatedLabel: fmt.dateTime(item.updated_at)
    });
  }

  function productVm(item) {
    productsById[String(item.product_item_id)] = item;
    return $.extend({}, item, {
      domainLabel: domainLabel(item.domain),
      sourceLabel: [item.source_type, item.source_id].filter(Boolean).join(" / ") || "—",
      productIdentity: [item.product_id_type, item.product_id].filter(Boolean).join(": "),
      priceLabel: priceLabel(item.sale_price, item.currency),
      statusLabel: statusLabel(item.status_code),
      statusBadgeClass: statusClass(item.status_code),
      tagsLabel: tagLabel(item.keywords),
      updatedLabel: fmt.dateTime(item.updated_at)
    });
  }

  var contentTable = C360.DataTableView.create({
    limit: 50,
    columns: [
      {
        label: "Content item", type: "identity", nameField: "title",
        subField: "summary", avatarField: "typeLabel", avatarBgField: "avatarBg",
        avatarColorField: "avatarColor", avatarTextClass: "text-[10px]"
      },
      { label: "Type", type: "badge", field: "typeLabel", classField: "typeBadgeClass" },
      { label: "Domain", field: "domainLabel" },
      { label: "Tags", field: "tagsLabel", muted: true },
      { label: "Status", type: "badge", field: "statusLabel", classField: "statusBadgeClass" },
      { label: "Updated", field: "updatedLabel", muted: true }
    ],
    rowVm: contentVm,
    rowId: rowId,
    rowClickable: false,
    onEdit: function (id) { openItemForm("content", contentById[String(id)]); },
    editLabel: "Edit",
    onSecondaryAction: function (id, button) { openPromotionDialog("content", contentById[String(id)], button); },
    canSecondaryAction: function () { return C360.config.isAdmin(); },
    secondaryActionLabel: "Creative",
    secondaryActionAria: function (item) { return "Create or refresh creative for " + (item.title || "content item"); },
    resourceLabel: "content item",
    fetch: function (params) {
      return api("/content-items/", $.extend({
        tenant_id: C360.config.current.tenantId,
        content_only: true
      }, params));
    },
    onFetched: function (items) {
      var shown = $("#content-items-tbody").children().length + items.length;
      $("#content-items-table-wrap").toggleClass("hidden", !shown);
      $("#item-content-count").text(shown);
    },
    onError: function (xhr) { showPageError("Could not load content items.", xhr); },
    el: {
      thead: "#content-items-thead",
      tbody: "#content-items-tbody",
      loading: "#content-loading",
      empty: "#content-empty",
      countLabel: "#content-count-label",
      loadMoreBtn: "#content-load-more"
    }
  });

  var productTable = C360.DataTableView.create({
    limit: 50,
    columns: [
      {
        label: "Product", type: "identity", nameField: "title",
        subField: "productIdentity", avatarField: "productBadge",
        avatarBg: "bg-emerald-100", avatarColor: "text-emerald-700",
        avatarTextClass: "text-[10px]"
      },
      { label: "Source", field: "sourceLabel" },
      { label: "Domain", field: "domainLabel" },
      { label: "Sale price", field: "priceLabel" },
      { label: "Tags", field: "tagsLabel", muted: true },
      { label: "Status", type: "badge", field: "statusLabel", classField: "statusBadgeClass" },
      { label: "Updated", field: "updatedLabel", muted: true }
    ],
    rowVm: function (item) {
      var vm = productVm(item);
      vm.productBadge = "SKU";
      return vm;
    },
    rowId: function (item) { return item.product_item_id; },
    rowClickable: false,
    onEdit: function (id) { openItemForm("product", productsById[String(id)]); },
    editLabel: "Edit",
    onSecondaryAction: function (id, button) { openPromotionDialog("product", productsById[String(id)], button); },
    canSecondaryAction: function () { return C360.config.isAdmin(); },
    secondaryActionLabel: "Creative",
    secondaryActionAria: function (item) { return "Create or refresh creative for " + (item.title || "product"); },
    resourceLabel: "product",
    fetch: function (params) {
      return api("/content-items/products", $.extend({
        tenant_id: C360.config.current.tenantId
      }, params));
    },
    onFetched: function (items) {
      var shown = $("#product-items-tbody").children().length + items.length;
      $("#product-items-table-wrap").toggleClass("hidden", !shown);
      $("#item-product-count").text(shown);
    },
    onError: function (xhr) { showPageError("Could not load product items.", xhr); },
    el: {
      thead: "#product-items-thead",
      tbody: "#product-items-tbody",
      loading: "#product-loading",
      empty: "#product-empty",
      countLabel: "#product-count-label",
      loadMoreBtn: "#product-load-more"
    }
  });

  function showPageError(fallback, xhr) {
    var $error = $("#content-products-page-error");
    var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
    $error.text(typeof detail === "string" ? detail : fallback).removeClass("hidden");
    if (showApiError) showApiError("content and product catalog", xhr);
  }

  function clearPageError() {
    $("#content-products-page-error").addClass("hidden").text("");
  }

  function closePromotionDialog() {
    if (promotionBusy) return;
    var dialog = document.getElementById("item-promotion-modal");
    if (dialog && dialog.open) dialog.close();
    promotionSelection = null;
    promotionOptions = null;
    promotionOptionsLoading = false;
    promotionOptionsRequest += 1;
    if (promotionReturnFocus && document.contains(promotionReturnFocus)) promotionReturnFocus.focus();
    promotionReturnFocus = null;
  }

  function updatePromotionControls() {
    var ready = !!promotionSelection && promotionSelection.hasContent && !!promotionOptions &&
      !promotionOptionsLoading && !promotionBusy;
    $("#item-promotion-ad-type, #item-promotion-format").prop("disabled", !ready);
    var adType = $("#item-promotion-ad-type").val();
    var format = $("#item-promotion-format").val();
    var valid = ready && promotionOptions.ad_types.some(function (option) { return option.value === adType; }) &&
      promotionOptions.format_codes.some(function (option) { return option.value === format; });
    $("#item-promotion-submit").prop("disabled", !valid);
  }

  function validPromotionOptions(options) {
    return Array.isArray(options) && options.length > 0 && options.every(function (option, index) {
      return option && typeof option.value === "string" && option.value.trim() === option.value &&
        option.value.length > 0 && option.value.length <= 80 && typeof option.label === "string" &&
        option.label.trim().length > 0 && options.findIndex(function (other) {
          return other && other.value === option.value;
        }) === index;
    });
  }

  function populatePromotionOptions(selector, placeholder, options) {
    var $select = $(selector).empty();
    $select.append($("<option></option>").val("").text(placeholder));
    options.forEach(function (option) {
      $select.append($("<option></option>").val(option.value).text(option.label + " (" + option.value + ")"));
    });
    $select.val("");
  }

  function loadPromotionOptions() {
    if (!promotionSelection || !promotionSelection.hasContent || promotionOptionsLoading || promotionBusy) return;
    var selection = promotionSelection;
    if (!C360.config.isAdmin() || String(C360.config.current.tenantId) !== selection.tenantId) {
      $("#item-promotion-error").text("Your tenant or permissions changed. Close this dialog and select the item again.").removeClass("hidden");
      $("#item-promotion-options-retry").addClass("hidden");
      return;
    }
    var requestId = ++promotionOptionsRequest;
    promotionOptions = null;
    promotionOptionsLoading = true;
    $("#item-promotion-error, #item-promotion-options-retry").addClass("hidden");
    $("#item-promotion-options-status").text("Loading creative choices…").removeClass("hidden");
    updatePromotionControls();
    api("/promotions/creative-options")
      .done(function (result) {
        if (requestId !== promotionOptionsRequest || promotionSelection !== selection) return;
        if (String(C360.config.current.tenantId) !== selection.tenantId) {
          $("#item-promotion-error").text("Your tenant changed. Close this dialog and select the item again.").removeClass("hidden");
          return;
        }
        if (!result || !validPromotionOptions(result.ad_types) || !validPromotionOptions(result.format_codes)) {
          $("#item-promotion-error").text("The API returned invalid creative choices. Try loading them again.").removeClass("hidden");
          $("#item-promotion-options-retry").removeClass("hidden");
          return;
        }
        promotionOptions = result;
        populatePromotionOptions("#item-promotion-ad-type", "Select an ad type", result.ad_types);
        populatePromotionOptions("#item-promotion-format", "Select a format", result.format_codes);
      })
      .fail(function (xhr) {
        if (requestId !== promotionOptionsRequest || promotionSelection !== selection) return;
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        var message = C360.config.formatApiErrorDetail(detail);
        $("#item-promotion-error").text(message || "Could not load creative choices. Please try again.").removeClass("hidden");
        $("#item-promotion-options-retry").removeClass("hidden");
      })
      .always(function () {
        if (requestId !== promotionOptionsRequest || promotionSelection !== selection) return;
        promotionOptionsLoading = false;
        $("#item-promotion-options-status").addClass("hidden").text("");
        updatePromotionControls();
      });
  }

  function openPromotionDialog(kind, item, button) {
    if (!C360.config.isAdmin()) return;
    var itemId = item && (kind === "product" ? item.product_item_id : item.content_item_id);
    if (!item || itemId === null || itemId === undefined || String(itemId) === "") {
      $("#content-products-page-error").text("The item is no longer available. Refresh the catalog and try again.").removeClass("hidden");
      return;
    }
    var dialog = document.getElementById("item-promotion-modal");
    if (!dialog || dialog.open || promotionBusy) return;
    promotionSelection = {
      kind: kind,
      id: String(itemId),
      tenantId: String(C360.config.current.tenantId),
      hasContent: kind !== "product" || !!item.content_item_id
    };
    promotionReturnFocus = button || document.activeElement;
    $("#item-promotion-form")[0].reset();
    $("#item-promotion-kind").text(kind === "product" ? "Product" : "Content item");
    $("#item-promotion-name").text(item.title || "Untitled item");
    $("#item-promotion-description").text(kind === "product"
      ? "The product shares a creative with its last saved linked content. Product pricing and source attributes are not copied."
      : "The last saved item is used. Existing linked creatives are refreshed; new ones start as drafts.");
    $("#item-promotion-error, #item-promotion-success").addClass("hidden").text("");
    $("#item-promotion-options-status, #item-promotion-options-retry").addClass("hidden");
    $("#item-promotion-cancel").text("Cancel");
    promotionOptions = null;
    promotionOptionsLoading = false;
    updatePromotionControls();
    if (!promotionSelection.hasContent) {
      $("#item-promotion-error").text("This product has no linked content item and cannot be converted.").removeClass("hidden");
    }
    dialog.showModal();
    if (promotionSelection.hasContent) loadPromotionOptions();
  }

  function submitPromotion(event) {
    event.preventDefault();
    if (!promotionSelection || promotionBusy) return;
    var selection = promotionSelection;
    var $error = $("#item-promotion-error");
    $error.addClass("hidden").text("");
    $("#item-promotion-success").addClass("hidden").text("");
    if (!C360.config.isAdmin() || String(C360.config.current.tenantId) !== selection.tenantId) {
      $error.text("Your tenant or permissions changed. Close this dialog and select the item again.").removeClass("hidden");
      return;
    }
    if (!selection.hasContent) {
      $error.text("This product has no linked content item and cannot be converted.").removeClass("hidden");
      return;
    }
    var adType = $("#item-promotion-ad-type").val();
    var format = $("#item-promotion-format").val();
    if (!promotionOptions || promotionOptionsLoading ||
        !promotionOptions.ad_types.some(function (option) { return option.value === adType; }) ||
        !promotionOptions.format_codes.some(function (option) { return option.value === format; })) {
      $error.text("Select an ad type and format code from the available choices.").removeClass("hidden");
      return;
    }
    promotionBusy = true;
    $("#item-promotion-submit, #item-promotion-close, #item-promotion-cancel, #item-promotion-ad-type, #item-promotion-format").prop("disabled", true);
    $("#item-promotion-submit span").text("Synchronizing…");
    api("/promotions/" + (selection.kind === "product" ? "products/" : "content/") + encodeURIComponent(selection.id),
      { ad_type: adType, format_code: format }, "PUT")
      .done(function (result) {
        if (String(C360.config.current.tenantId) !== selection.tenantId) {
          $error.text("The tenant changed while the request was running. Check the original tenant for the result.").removeClass("hidden");
          return;
        }
        if (!result || !Number.isSafeInteger(result.creative_id) || result.creative_id <= 0) {
          $error.text("The API did not confirm the creative. Refresh and retry.").removeClass("hidden");
          return;
        }
        $("#item-promotion-success").text(
          "Creative #" + result.creative_id + " created or refreshed" +
          (result.status ? " (status: " + result.status + ")" : "") + ". This did not publish an ad."
        ).removeClass("hidden");
        $("#item-promotion-cancel").text("Close");
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        var message = C360.config.formatApiErrorDetail(detail);
        $error.text(message || "Could not synchronize the creative. Please try again.").removeClass("hidden");
      })
      .always(function () {
        promotionBusy = false;
        $("#item-promotion-close, #item-promotion-cancel").prop("disabled", false);
        $("#item-promotion-submit span").text("Create or refresh");
        updatePromotionControls();
      });
  }

  function setTab(tab, shouldLoad) {
    activeTab = tab === "products" ? "products" : "content";
    var productActive = activeTab === "products";
    $("#item-tab-content")
      .attr("aria-selected", productActive ? "false" : "true")
      .toggleClass("border-indigo-200 bg-indigo-50 text-indigo-700", !productActive)
      .toggleClass("border-transparent bg-transparent text-slate-600", productActive);
    $("#item-tab-products")
      .attr("aria-selected", productActive ? "true" : "false")
      .toggleClass("border-indigo-200 bg-indigo-50 text-indigo-700", productActive)
      .toggleClass("border-transparent bg-transparent text-slate-600", !productActive);
    $("#item-panel-content").toggleClass("hidden", productActive);
    $("#item-panel-products").toggleClass("hidden", !productActive);
    if (shouldLoad) loadActive();
  }

  function loadActive() {
    clearPageError();
    return activeTab === "products" ? productTable.load(false) : contentTable.load(false);
  }

  function populateDomainSelects() {
    var labels = fmt.DOMAIN_LABELS || {};
    ["#content-domain-filter", "#product-domain-filter", "#item-form-domain"].forEach(function (selector) {
      var $select = $(selector);
      var current = $select.val();
      $select.find("option:not([data-default])").remove();
      Object.keys(labels).filter(function (domain) { return domain !== "all"; }).sort().forEach(function (domain) {
        $select.append($("<option></option>").val(domain).text(labels[domain]));
      });
      if (current && $select.find("option").filter(function () { return this.value === current; }).length) {
        $select.val(current);
      }
    });
  }

  function showItemFormError(message) {
    $("#item-form-error-text").text(message);
    $("#item-form-error").removeClass("hidden").addClass("flex");
  }

  function clearItemFormError() {
    $("#item-form-error").addClass("hidden").removeClass("flex");
    $("#item-form-error-text").empty();
  }

  function configureItemEditor(kind) {
    var isProduct = kind === "product";
    var isContent = kind === "content";
    var isEditing = !!editingItem;
    var theme = isProduct
      ? {
          icon: "bi-box-seam",
          iconWrap: "bg-emerald-100 text-emerald-700 ring-emerald-200",
          badge: "bg-emerald-100 text-emerald-700",
          button: "bg-emerald-600 hover:bg-emerald-700 focus:ring-emerald-500"
        }
      : {
          icon: isContent ? "bi-file-earmark-richtext" : "bi-plus-lg",
          iconWrap: "bg-indigo-100 text-indigo-700 ring-indigo-200",
          badge: "bg-indigo-100 text-indigo-700",
          button: "bg-indigo-600 hover:bg-indigo-700 focus:ring-indigo-500"
        };
    var title = isEditing
      ? (isProduct ? "Edit product" : "Edit content item")
      : (isProduct ? "Add product" : (isContent ? "Add content item" : "Add item"));
    var description = isProduct
      ? "Manage the product identity, pricing, and customer-facing details."
      : (isContent
        ? "Create or update an article, news story, or video for recommendations."
        : "Choose the item type, then enter and validate its fields.");
    var saveLabel = isEditing
      ? "Save changes"
      : (isProduct ? "Create product" : (isContent ? "Create content" : "Create item"));

    $("#item-form-title").text(title);
    $("#item-form-description").text(description);
    $("#item-form-icon").attr("class", "bi " + theme.icon + " text-xl");
    $("#item-form-icon-wrap").attr(
      "class",
      "flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl ring-1 ring-inset " + theme.iconWrap
    );
    $("#item-form-mode-badge")
      .attr("class", "rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider " + theme.badge)
      .text(isEditing ? (isProduct ? "Editing product" : "Editing content") : (isProduct ? "New product" : (isContent ? "New content" : "New item")));
    $("#item-form-save")
      .attr(
        "class",
        "inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 " +
          theme.button
      )
      .prop("disabled", !isEditing && !kind);
    $("#item-form-save-icon").attr("class", "bi " + (isEditing ? "bi-check2" : "bi-plus-lg"));
    $("#item-form-save-label").text(saveLabel);
  }

  function openItemForm(kind, item) {
    editingItem = item || null;
    $("#item-form")[0].reset();
    clearItemFormError();
    populateDomainSelects();
    $("#item-kind-fieldset").toggleClass("hidden", !!editingItem);
    $("#item-form-status-wrap").toggleClass("hidden", !editingItem);
    $("#item-form-fields, #product-source-fields, #product-form-type-wrap").addClass("hidden");
    $("#content-form-type-wrap").removeClass("hidden");
    $("#item-form-save").prop("disabled", !editingItem);
    if (editingItem) {
      var selectedKind = kind === "product" ? "product" : "content";
      $("input[name='item-kind'][value='" + selectedKind + "']").prop("checked", true);
      $("#item-form-fields").removeClass("hidden");
      $("#content-form-type-wrap").toggleClass("hidden", selectedKind !== "content");
      $("#product-form-type-wrap, #product-source-fields").toggleClass("hidden", selectedKind !== "product");
      $("#item-form-domain option[value='all']").prop("disabled", selectedKind === "product");
      $("#item-form-domain").val(item.domain);
      if (!$("#item-form-domain").val()) {
        $("#item-form-domain").append($("<option></option>").val(item.domain).text(domainLabel(item.domain)));
        $("#item-form-domain").val(item.domain);
      }
      $("#content-form-type").val(item.item_type || "article");
      $("#item-form-title-input").val(item.title || "");
      $("#item-form-summary").val(item.summary || "");
      $("#item-form-image-url").val(item.image_url || "");
      $("#item-form-cta-label").val(item.cta_label || "");
      $("#item-form-cta-url").val(item.cta_url || "");
      $("#item-form-keywords").val((selectedKind === "product" ? item.keywords : item.segment_tags || []).join(", "));
      $("#item-form-status").val(Number(item.status_code) === 1 ? "1" : "0");
      $("#product-form-source-id").val(item.source_id || "");
      $("#product-form-source-type").val(item.source_type || "");
      $("#product-form-id-type").val(item.product_id_type || "");
      $("#product-form-id").val(item.product_id || "");
      $("#product-form-original-price").val(item.original_price === null ? "" : item.original_price || "");
      $("#product-form-sale-price").val(item.sale_price === null ? "" : item.sale_price || "");
      $("#product-form-currency").val(item.currency || "");
      $("#product-form-ext-attributes").val(JSON.stringify(item.ext_attributes || {}, null, 2));
      configureItemEditor(selectedKind);
    } else {
      var defaultKind = kind === "content" || kind === "product" ? kind : "";
      $("input[name='item-kind']").prop("checked", false);
      $("#item-form-domain").val("retail");
      if (defaultKind) {
        $("input[name='item-kind'][value='" + defaultKind + "']").prop("checked", true);
        $("#item-form-fields").removeClass("hidden");
        $("#content-form-type-wrap").toggleClass("hidden", defaultKind !== "content");
        $("#product-form-type-wrap, #product-source-fields").toggleClass("hidden", defaultKind !== "product");
        $("#item-form-domain option[value='all']").prop("disabled", defaultKind === "product");
        if (defaultKind === "product") $("#item-form-cta-label").val("View product");
      } else {
        $("#item-form-domain option[value='all']").prop("disabled", false);
      }
      configureItemEditor(defaultKind);
    }
    updateProductTypePreview();
    $("#item-form-modal").removeClass("hidden").addClass("flex");
    $("#item-form-title-input").trigger("focus");
  }

  function closeModal(selector) {
    $(selector).addClass("hidden").removeClass("flex");
  }

  function selectedKind() {
    return String($("input[name='item-kind']:checked").val() || "");
  }

  function updateProductTypePreview() {
    var domain = String($("#item-form-domain").val() || "");
    $("#product-form-type").text(domain === "banking" ? "STOCK" : (domain ? domain + "_product" : "Choose a domain"));
  }

  function parseCommaList(value) {
    return String(value || "").split(",").map(function (entry) {
      return $.trim(entry);
    }).filter(Boolean).filter(function (entry, index, entries) {
      return entries.indexOf(entry) === index;
    });
  }

  function optionalNumber(selector) {
    var value = $.trim($(selector).val());
    return value ? Number(value) : null;
  }

  function formPayload(kind) {
    var domain = String($("#item-form-domain").val() || "");
    var title = $.trim($("#item-form-title-input").val());
    if (!domain || !title) throw new Error("Domain and title are required.");
    var common = {
      domain: domain,
      title: title,
      summary: $.trim($("#item-form-summary").val()) || null,
      image_url: $.trim($("#item-form-image-url").val()) || null,
      cta_label: $.trim($("#item-form-cta-label").val()) || null,
      cta_url: $.trim($("#item-form-cta-url").val()) || null
    };
    if (kind === "content") {
      if (!editingItem) common.tenant_id = C360.config.current.tenantId;
      common.item_type = String($("#content-form-type").val() || "");
      common.segment_tags = parseCommaList($("#item-form-keywords").val());
      if (editingItem) common.status_code = Number($("#item-form-status").val());
      return common;
    }

    var productId = $.trim($("#product-form-id").val());
    var productIdType = $.trim($("#product-form-id-type").val());
    if (!productId || !productIdType) throw new Error("Product ID and Product ID type are required.");
    var sourceId = $.trim($("#product-form-source-id").val());
    if (!sourceId && domain !== "banking") throw new Error("Source ID is required outside the banking domain.");
    var extAttributes;
    try {
      extAttributes = JSON.parse(String($("#product-form-ext-attributes").val() || "{}"));
    } catch (_error) {
      throw new Error("Extended attributes must be valid JSON.");
    }
    if (!extAttributes || Array.isArray(extAttributes) || typeof extAttributes !== "object") {
      throw new Error("Extended attributes must be a JSON object.");
    }
    var originalPrice = optionalNumber("#product-form-original-price");
    var salePrice = optionalNumber("#product-form-sale-price");
    var currency = $.trim($("#product-form-currency").val()).toUpperCase() || null;
    if ([originalPrice, salePrice].some(function (value) {
      return value !== null && (!Number.isFinite(value) || value < 0);
    })) {
      throw new Error("Prices must be finite, non-negative numbers.");
    }
    if ((originalPrice !== null || salePrice !== null) && !currency) {
      throw new Error("Currency is required when a price is entered.");
    }
    var payload = $.extend(common, {
      product_type: domain === "banking" ? "STOCK" : domain + "_product",
      source_id: sourceId,
      source_type: $.trim($("#product-form-source-type").val()),
      product_id_type: productIdType,
      product_id: productId,
      keywords: parseCommaList($("#item-form-keywords").val()),
      ext_attributes: extAttributes,
      original_price: originalPrice,
      sale_price: salePrice,
      currency: currency
    });
    if (editingItem) payload.status_code = Number($("#item-form-status").val());
    return payload;
  }

  function submitItemForm(event) {
    event.preventDefault();
    var kind = selectedKind();
    clearItemFormError();
    if (!kind) {
      showItemFormError("Select Content or Product before saving.");
      return;
    }
    var payload;
    try {
      payload = formPayload(kind);
    } catch (error) {
      showItemFormError(error.message);
      return;
    }
    var $save = $("#item-form-save");
    var isEditing = !!editingItem;
    $save.prop("disabled", true);
    $("#item-form-save-icon").attr("class", "bi bi-arrow-repeat animate-spin");
    $("#item-form-save-label").text("Saving…");
    var endpoint = isEditing
      ? (kind === "product"
        ? "/content-items/products/" + encodeURIComponent(editingItem.product_item_id)
        : "/content-items/" + encodeURIComponent(editingItem.content_item_id))
      : (kind === "product" ? "/content-items/products" : "/content-items/");
    var method = isEditing ? (kind === "product" ? "PUT" : "PATCH") : "POST";
    api(endpoint, payload, method)
      .done(function () {
        closeModal("#item-form-modal");
        editingItem = null;
        setTab(kind === "product" ? "products" : "content", false);
        showToast(
          isEditing
            ? (kind === "product" ? "Product updated." : "Content item updated.")
            : (kind === "product" ? "Product created." : "Content item created."),
          "success"
        );
        loadActive();
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        showItemFormError(
          typeof detail === "string"
            ? detail
            : (isEditing ? "Could not save your changes. Check the fields and try again." : "Could not create the item. Check the fields and try again.")
        );
      })
      .always(function () {
        configureItemEditor(selectedKind());
      });
  }

  function validateTsvHeader(file, kind) {
    if (!file || !/\.tsv$/i.test(file.name)) throw new Error("Choose a .tsv file.");
    if (file.size > MAX_UPLOAD_BYTES) throw new Error("TSV files are limited to 10 MiB.");
    return file.text().then(function (text) {
      var header = (text.split(/\r?\n/, 1)[0] || "").replace(/^\ufeff/, "").split("\t");
      var requiredColumns = kind === "content" ? REQUIRED_CONTENT_TSV_COLUMNS : REQUIRED_TSV_COLUMNS;
      var missing = requiredColumns.filter(function (column) {
        return header.indexOf(column) === -1;
      });
      if (missing.length) throw new Error("Missing required TSV columns: " + missing.join(", "));
      if (new Set(header).size !== header.length) throw new Error("The TSV contains duplicate column names.");
      if (kind === "content") {
        var unsupported = header.filter(function (column) {
          return ALLOWED_CONTENT_TSV_COLUMNS.indexOf(column) === -1;
        });
        if (unsupported.length) throw new Error("Unsupported content TSV columns: " + unsupported.join(", "));
      }
      return file;
    });
  }

  function setImportError(message) {
    $("#product-import-error").text(message).removeClass("hidden");
    $("#product-import-result").addClass("hidden").empty();
  }

  function handleImportFileChange() {
    var file = $("#product-import-file")[0].files[0] || null;
    selectedFile = null;
    $("#product-import-submit").prop("disabled", true);
    $("#product-import-error").addClass("hidden").text("");
    $("#product-import-result").addClass("hidden").empty();
    $("#product-import-file-label").text(file ? file.name + " · " + fileSizeLabel(file.size) : "No file selected");
    if (!file) return;
    var validation;
    try {
      validation = validateTsvHeader(file, importKind);
    } catch (error) {
      setImportError(error.message || "Choose a valid TSV file.");
      return;
    }
    validation
      .then(function () {
        selectedFile = file;
        $("#product-import-submit").prop("disabled", false);
      })
      .catch(function (error) {
        setImportError(error.message || "Could not validate this TSV file.");
      });
  }

  function submitTsvImport() {
    if (!selectedFile) {
      setImportError("Choose a valid TSV file before importing.");
      return;
    }
    var formData = new FormData();
    formData.append("file", selectedFile);
    var $submit = $("#product-import-submit");
    $submit.prop("disabled", true).text("Validating & submitting…");
    $("#product-import-cancel, #product-import-close").prop("disabled", true);
    $("#product-import-error").addClass("hidden").text("");
    $("#product-import-result").addClass("hidden").empty();
    var isContent = importKind === "content";
    var endpoint = isContent
      ? "/content-items/import/content"
      : "/content-items/import/products";
    api(endpoint, formData, "POST")
      .done(function (result) {
        if (!result || result.status !== "submitted" || !result.run_id) {
          setImportError("The API did not confirm that the TSV import was submitted.");
          return;
        }
        var runId = result.run_id || "unknown";
        var count = isContent ? result.content_items_submitted : result.products_submitted;
        var label = isContent ? "content items" : "products";
        var message = "Validated " + count + " " + label + " and queued import run " + runId + ".";
        $("#content-products-page-status")
          .text(message)
          .removeClass("hidden");
        closeModal("#product-import-modal");
        showToast(message, "success");
        if (isContent) contentTable.reload();
        else productTable.reload();
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        setImportError(typeof detail === "string" ? detail : "TSV import failed validation or could not be submitted.");
      })
      .always(function () {
        $submit.prop("disabled", !selectedFile).text("Validate & import");
        $("#product-import-cancel, #product-import-close").prop("disabled", false);
      });
  }

  function openImportModal(kind) {
    importKind = kind === "content" ? "content" : "product";
    selectedFile = null;
    $("#content-products-page-status").addClass("hidden").empty();
    $("#product-import-file").val("");
    $("#product-import-file-label").text("No file selected");
    $("#product-import-submit").prop("disabled", true).text("Validate & import");
    $("#product-import-title").text(importKind === "content" ? "Import content TSV" : "Import product TSV");
    $("#product-import-description").text(
      importKind === "content"
        ? "Upload news, video, or article items. Every row and domain is validated before the import is queued."
        : "Upload product rows. Every row and product type is validated before the import is queued."
    );
    $("#product-import-file-hint").text(
      importKind === "content"
        ? "Required columns: Domain, Item_Type, Title. Item type must be article, news, or video. Maximum 10 MiB and 5,000 rows."
        : "Required columns include Product_Type, Store_ID, Product_ID_Type, Product_ID, Name, and Full_URL. Maximum 10 MiB and 5,000 rows."
    );
    $("#product-import-error, #product-import-result").addClass("hidden").empty();
    $("#product-import-cancel, #product-import-close").prop("disabled", false);
    $("#product-import-modal").removeClass("hidden").addClass("flex");
  }

  function bindEvents() {
    populateDomainSelects();
    contentTable.bindSearch("#content-search", "q", 300);
    contentTable.bindSearch("#content-tag-filter", "tag", 300);
    contentTable.bindSelect("#content-domain-filter", "domain");
    contentTable.bindSelect("#content-type-filter", "item_type");
    contentTable.bindLoadMore();
    contentTable.bindRowEdit();
    productTable.bindSearch("#product-search", "q", 300);
    productTable.bindSearch("#product-tag-filter", "tag", 300);
    productTable.bindSelect("#product-domain-filter", "domain");
    productTable.bindLoadMore();
    productTable.bindRowEdit();

    $(document)
      .off(".contentProducts")
      .on("click.contentProducts", ".item-category-tab", function () {
        setTab($(this).attr("data-item-kind"), true);
      })
      .on("click.contentProducts", "#btn-item-add", function () { openItemForm(); })
      .on("submit.contentProducts", "#item-promotion-form", submitPromotion)
      .on("click.contentProducts", "#item-promotion-close, #item-promotion-cancel", closePromotionDialog)
      .on("click.contentProducts", "#item-promotion-options-retry", loadPromotionOptions)
      .on("change.contentProducts", "#item-promotion-ad-type, #item-promotion-format", updatePromotionControls)
      .on("cancel.contentProducts", "#item-promotion-modal", function (event) {
        event.preventDefault();
        closePromotionDialog();
      })
      .on("click.contentProducts", "#btn-content-refresh", function () { contentTable.reload(); })
      .on("click.contentProducts", "#btn-products-refresh", function () { productTable.reload(); })
      .on("click.contentProducts", "#item-form-close, #item-form-cancel", function () {
        closeModal("#item-form-modal");
      })
      .on("change.contentProducts", "input[name='item-kind']", function () {
        var kind = selectedKind();
        $("#item-form-fields").toggleClass("hidden", !kind);
        $("#content-form-type-wrap").toggleClass("hidden", kind !== "content");
        $("#product-form-type-wrap, #product-source-fields").toggleClass("hidden", kind !== "product");
        $("#item-form-domain option[value='all']").prop("disabled", kind === "product");
        if (kind === "product" && $("#item-form-domain").val() === "all") {
          $("#item-form-domain").val("retail");
        }
        configureItemEditor(kind);
        if (!editingItem) {
          if (kind === "product") $("#item-form-cta-label").val("View product");
        }
      })
      .on("change.contentProducts", "#item-form-domain", updateProductTypePreview)
      .on("submit.contentProducts", "#item-form", submitItemForm)
      .on("click.contentProducts", "#btn-content-import", function () { openImportModal("content"); })
      .on("click.contentProducts", "#btn-product-import", function () { openImportModal("product"); })
      .on("click.contentProducts", "#product-import-close, #product-import-cancel", function () {
        closeModal("#product-import-modal");
      })
      .on("change.contentProducts", "#product-import-file", handleImportFileChange)
      .on("click.contentProducts", "#product-import-submit", submitTsvImport)
      .on("click.contentProducts", "#item-form-modal, #product-import-modal", function (event) {
        if (event.target === this) closeModal("#" + this.id);
      })
      .on("click.contentProducts", "#content-clear-filters", function () {
        $("#content-search, #content-tag-filter, #content-domain-filter, #content-type-filter").val("");
        contentTable.clearFilters();
      })
      .on("click.contentProducts", "#product-clear-filters", function () {
        $("#product-search, #product-tag-filter, #product-domain-filter").val("");
        productTable.clearFilters();
      });

    $(document).off("keydown.contentProducts").on("keydown.contentProducts", function (event) {
      if (event.key === "Escape") {
        var promotionDialog = document.getElementById("item-promotion-modal");
        if (promotionDialog && promotionDialog.open) return;
        closeModal("#item-form-modal");
        closeModal("#product-import-modal");
      }
    });
  }

  function load() {
    populateDomainSelects();
    setTab(activeTab, false);
    return loadActive();
  }

  C360.itemView = { bindEvents: bindEvents, load: load };
  C360.router.define("/content-products", {
    section: "view-content-products",
    tab: "content-products",
    mount: load
  });
})(window.C360);