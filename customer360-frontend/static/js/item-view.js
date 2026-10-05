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
  function detailValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
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
    onEdit: function (id) { showDetails(contentById[String(id)], false); },
    editLabel: "Details",
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
    onEdit: function (id) { showDetails(productsById[String(id)], true); },
    editLabel: "Details",
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

  function openItemForm() {
    $("#item-form")[0].reset();
    $("#item-form-error").addClass("hidden").text("");
    $("#item-form-fields, #product-source-fields").addClass("hidden");
    $("#content-form-type-wrap").removeClass("hidden");
    $("#product-form-type-wrap").addClass("hidden");
    $("#item-form-save").prop("disabled", true).text("Save item");
    $("#item-form-modal").removeClass("hidden").addClass("flex");
    populateDomainSelects();
    $("#item-form-domain").val("retail");
    updateProductTypePreview();
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
      common.tenant_id = C360.config.current.tenantId;
      common.item_type = String($("#content-form-type").val() || "");
      common.segment_tags = parseCommaList($("#item-form-keywords").val());
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
    return $.extend(common, {
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
  }

  function submitItemForm(event) {
    event.preventDefault();
    var kind = selectedKind();
    var $error = $("#item-form-error");
    $error.addClass("hidden").text("");
    if (!kind) {
      $error.removeClass("hidden").text("Select Content or Product before saving.");
      return;
    }
    var payload;
    try {
      payload = formPayload(kind);
    } catch (error) {
      $error.removeClass("hidden").text(error.message);
      return;
    }
    var $save = $("#item-form-save");
    $save.prop("disabled", true).text("Saving…");
    var endpoint = kind === "product" ? "/content-items/products" : "/content-items/";
    api(endpoint, payload, "POST")
      .done(function () {
        closeModal("#item-form-modal");
        setTab(kind === "product" ? "products" : "content", false);
        showToast(kind === "product" ? "Product created." : "Content item created.", "success");
        loadActive();
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : "Could not create the item. Check the fields and try again.");
      })
      .always(function () { $save.prop("disabled", false).text("Save item"); });
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

  function detailRows(item, isProduct) {
    var values = [
      ["Title", item.title],
      ["Summary", item.summary],
      ["Domain", domainLabel(item.domain)],
      ["Type", isProduct ? item.product_type : item.item_type],
      ["Status", statusLabel(item.status_code)],
      ["Tags", tagLabel(isProduct ? item.keywords : item.segment_tags)],
      ["Image URL", item.image_url],
      ["CTA", item.cta_url]
    ];
    if (isProduct) {
      values.push(
        ["Source ID", item.source_id],
        ["Source type", item.source_type],
        ["Product identity", [item.product_id_type, item.product_id].join(": ")],
        ["Original price", priceLabel(item.original_price, item.currency)],
        ["Sale price", priceLabel(item.sale_price, item.currency)],
        ["Extended attributes", item.ext_attributes]
      );
    }
    return values;
  }

  function showDetails(item, isProduct) {
    if (!item) return;
    $("#item-details-title").text(isProduct ? "Product details" : "Content details");
    var $body = $("#item-details-body").empty();
    detailRows(item, isProduct).forEach(function (entry) {
      var $row = $("<div>").addClass("grid gap-1 border-b border-slate-100 pb-3 sm:grid-cols-[10rem_1fr]");
      $("<dt>").addClass("text-xs font-semibold uppercase tracking-wide text-slate-500").text(entry[0]).appendTo($row);
      $("<dd>").addClass("break-words whitespace-pre-wrap text-sm text-slate-800").text(detailValue(entry[1])).appendTo($row);
      $body.append($row);
    });
    $("#item-details-modal").removeClass("hidden").addClass("flex");
  }

  function bindEvents() {
    populateDomainSelects();
    contentTable.bindSearch("#content-search", "q", 300);
    contentTable.bindSelect("#content-domain-filter", "domain");
    contentTable.bindSelect("#content-type-filter", "item_type");
    contentTable.bindLoadMore();
    contentTable.bindRowEdit();
    productTable.bindSearch("#product-search", "q", 300);
    productTable.bindSelect("#product-domain-filter", "domain");
    productTable.bindLoadMore();
    productTable.bindRowEdit();

    $(document)
      .off(".contentProducts")
      .on("click.contentProducts", ".item-category-tab", function () {
        setTab($(this).attr("data-item-kind"), true);
      })
      .on("click.contentProducts", "#btn-item-add", openItemForm)
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
        $("#item-form-save").prop("disabled", !kind);
        if (kind === "product") $("#item-form-cta-label").val("View product");
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
      .on("click.contentProducts", "#item-details-close", function () {
        closeModal("#item-details-modal");
      })
      .on("click.contentProducts", "#item-form-modal, #product-import-modal, #item-details-modal", function (event) {
        if (event.target === this) closeModal("#" + this.id);
      })
      .on("click.contentProducts", "#content-clear-filters", function () {
        $("#content-search, #content-domain-filter, #content-type-filter").val("");
        contentTable.clearFilters();
      })
      .on("click.contentProducts", "#product-clear-filters", function () {
        $("#product-search, #product-domain-filter").val("");
        productTable.clearFilters();
      });

    $(document).off("keydown.contentProducts").on("keydown.contentProducts", function (event) {
      if (event.key === "Escape") {
        closeModal("#item-form-modal");
        closeModal("#product-import-modal");
        closeModal("#item-details-modal");
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