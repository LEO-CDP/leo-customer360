var flowy = function(canvas, grab, release, snapping, rearrange, spacing_x, spacing_y) {
    if (typeof jQuery === "undefined") {
        throw new Error("Flowy requires jQuery 3.7.1 or 4.x.");
    }
    var $ = jQuery;
    if (!grab) {
        grab = function() {};
    }
    if (!release) {
        release = function() {};
    }
    if (!snapping) {
        snapping = function() {
            return true;
        }
    }
    if (!rearrange) {
        rearrange = function() {
            return false;
        }
    }
    if (spacing_x == null) {
        spacing_x = 20;
    }
    if (spacing_y == null) {
        spacing_y = 80;
    }
    if (!canvas || !$(canvas).length) {
        throw new TypeError("Flowy requires a canvas element.");
    }
    if (!Element.prototype.matches) {
        Element.prototype.matches = Element.prototype.msMatchesSelector ||
            Element.prototype.webkitMatchesSelector;
    }
    if (!Element.prototype.closest) {
        Element.prototype.closest = function(s) {
            var el = this;
            do {
                if (Element.prototype.matches.call(el, s)) return el;
                el = el.parentElement || el.parentNode;
            } while (el !== null && el.nodeType === 1);
            return null;
        };
    }
    var loaded = false;
    var zoomScale = 1;
    flowy.setZoom = function(scale) {
        if (!Number.isFinite(scale) || scale <= 0) {
            throw new RangeError("Flowy zoom scale must be a positive number.");
        }
        zoomScale = scale;
    };
    flowy.load = function() {
        if (!loaded)
            loaded = true;
        else
            return;
        var blocks = [];
        var blockstemp = [];
        var $canvas = $(canvas);
        var canvas_div = $canvas[0];
        function canvasPosition(element) {
            var canvasRect = canvas_div.getBoundingClientRect();
            var elementRect = element.getBoundingClientRect();
            return {
                left: (elementRect.left - canvasRect.left) / zoomScale + canvas_div.scrollLeft,
                top: (elementRect.top - canvasRect.top) / zoomScale + canvas_div.scrollTop,
                width: elementRect.width / zoomScale,
                height: elementRect.height / zoomScale
            };
        }
        function canvasPointerPosition(clientX, clientY) {
            var canvasRect = canvas_div.getBoundingClientRect();
            return {
                x: (clientX - canvasRect.left) / zoomScale + canvas_div.scrollLeft,
                y: (clientY - canvasRect.top) / zoomScale + canvas_div.scrollTop
            };
        }
        var active = false;
        var paddingx = spacing_x;
        var paddingy = spacing_y;
        var rearrange = false;
        var drag, dragx, dragy, original;
        var mouse_x, mouse_y;
        var dragblock = false;
        var prevblock = 0;
        var $indicator = $("<div>").addClass("indicator invisible");
        var el = $indicator[0];
        $canvas.append($indicator);
        function blockElement(id) {
            var input = $canvas.find(".blockid[value='" + id + "']")[0];
            return input ? input.parentNode : null;
        }
        function arrowElement(id) {
            var input = $canvas.find(".arrowid[value='" + id + "']")[0];
            return input ? input.parentNode : null;
        }
        function blockIdOf(element) {
            return parseInt($(element).find(".blockid").val());
        }
        flowy.import = function(output) {
            if (!output || typeof output.html !== "string" || !Array.isArray(output.blockarr)) {
                throw new TypeError("Flowy.import expects an output object from flowy.output().");
            }
            var importedBlocks = output.blockarr.map(function(block) {
                return {
                    childwidth: parseFloat(block.childwidth) || 0,
                    parent: parseFloat(block.parent),
                    id: parseFloat(block.id),
                    x: parseFloat(block.x),
                    y: parseFloat(block.y),
                    width: parseFloat(block.width),
                    height: parseFloat(block.height)
                };
            });
            if (importedBlocks.some(function(block) {
                return !Number.isFinite(block.id) || !Number.isFinite(block.parent) ||
                    !Number.isFinite(block.x) || !Number.isFinite(block.y) ||
                    !Number.isFinite(block.width) || !Number.isFinite(block.height);
            })) {
                throw new TypeError("Flowy.import received invalid block coordinates.");
            }
            blocks = importedBlocks;
            blockstemp = [];
            if (original) {
                $(original).removeClass("dragnow");
            }
            if (drag && drag.parentNode === document.body) {
                $(drag).remove();
            }
            active = false;
            rearrange = false;
            dragblock = false;
            $canvas.html(output.html);
            el = $canvas.find(".indicator")[0] || el;
            $indicator = $(el).addClass("invisible");
            if (!el.parentNode) {
                $canvas.append(el);
            }
            if (blocks.length > 1) {
                rearrangeMe();
                checkOffset();
            }
        };
        function serializeCanvas() {
            var $canvasCopy = $canvas.clone();
            var $sourceControls = $canvas.find("input, select, textarea");
            var $copiedControls = $canvasCopy.find("input, select, textarea");
            $sourceControls.each(function(index) {
                var $control = $(this);
                var $copiedControl = $copiedControls.eq(index);
                if ($control.is("select")) {
                    $control.find("option").each(function(optionIndex) {
                        if ($(this).prop("selected")) {
                            $copiedControl.find("option").eq(optionIndex).attr("selected", "");
                        } else {
                            $copiedControl.find("option").eq(optionIndex).removeAttr("selected");
                        }
                    });
                } else if ($control.is("textarea")) {
                    $copiedControl.text($control.val());
                } else {
                    $copiedControl.attr("value", $control.val());
                    if ($control.prop("checked")) {
                        $copiedControl.attr("checked", "");
                    } else {
                        $copiedControl.removeAttr("checked");
                    }
                }
            });
            return $canvasCopy.html();
        }
        flowy.output = function() {
            var json_data = {
                html: serializeCanvas(),
                blockarr: blocks.map(function(block) {
                    return Object.assign({}, block);
                }),
                blocks: []
            };
            for (var i = 0; i < blocks.length; i++) {
                json_data.blocks.push({
                    id: blocks[i].id,
                    parent: blocks[i].parent,
                    data: [],
                    attr: []
                });
                var blockInput = $canvas.find(".blockid[value='" + blocks[i].id + "']")[0];
                if (!blockInput) {
                    throw new Error("Flowy block " + blocks[i].id + " is missing from the canvas.");
                }
                var blockParent = blockInput.parentNode;
                $(blockParent).find("input, select, textarea").each(function() {
                    json_data.blocks[i].data.push({
                        name: $(this).attr("name"),
                        value: $(this).val()
                    });
                });
                Array.prototype.slice.call(blockParent.attributes).forEach(function(attribute) {
                    var jsonobj = {};
                    jsonobj[attribute.name] = attribute.value;
                    json_data.blocks[i].attr.push(jsonobj);
                });
            }
            return json_data;
        };
        flowy.deleteBlocks = function() {
            blocks = [];
            blockstemp = [];
            if (original) {
                $(original).removeClass("dragnow");
            }
            if (drag && drag.parentNode) {
                $(drag).remove();
            }
            active = false;
            rearrange = false;
            dragblock = false;
            drag = undefined;
            original = undefined;
            $indicator.addClass("invisible");
            $canvas.empty().append($indicator);
        };

        flowy.beginDrag = function(event) {
            if (event.targetTouches) {
                mouse_x = event.changedTouches[0].clientX;
                mouse_y = event.changedTouches[0].clientY;
            } else {
                mouse_x = event.clientX;
                mouse_y = event.clientY;
            }
            var $paletteItem = $(event.target).closest(".create-flowy");
            if (event.button !== 2 && $paletteItem.length) {
                original = $paletteItem[0];
                var $newNode = $paletteItem.clone().addClass("block").removeClass("create-flowy");
                var blockId = blocks.length === 0 ? 0 : Math.max.apply(Math, blocks.map(a => a.id)) + 1;
                $newNode.append("<input type='hidden' name='blockid' class='blockid' value='" + blockId + "'>");
                $(document.body).append($newNode);
                drag = $newNode[0];
                $paletteItem.addClass("dragnow");
                blockGrabbed($paletteItem[0]);
                $(drag).addClass("dragging");
                active = true;
                dragx = mouse_x - $paletteItem[0].getBoundingClientRect().left;
                dragy = mouse_y - $paletteItem[0].getBoundingClientRect().top;
                $(drag).css({
                    left: mouse_x - dragx + "px",
                    top: mouse_y - dragy + "px"
                });
            }
        }

        flowy.endDrag = function(event) {
            if (event.button !== 2 && (active || rearrange)) {
                dragblock = false;
                blockReleased();
                if (!$indicator.hasClass("invisible")) {
                    $indicator.addClass("invisible");
                }
                if (active) {
                    $(original).removeClass("dragnow");
                    $(drag).removeClass("dragging");
                }
                if (parseInt($(drag).find(".blockid").val()) === 0 && rearrange) {
                    firstBlock("rearrange")    
                } else if (active && blocks.length == 0 &&
                    drag.getBoundingClientRect().top > canvas_div.getBoundingClientRect().top &&
                    drag.getBoundingClientRect().left > canvas_div.getBoundingClientRect().left) {
                    firstBlock("drop");
                } else if (active && blocks.length == 0) {
                    removeSelection();
                } else if (active) {
                    var blocko = blocks.map(a => a.id);
                    for (var i = 0; i < blocks.length; i++) {
                        if (checkAttach(blocko[i])) {
                            active = false;
                            if (blockSnap(drag, false, blockElement(blocko[i]))) {
                                snap(drag, i, blocko);
                            } else {
                                active = false;
                                removeSelection();
                            }
                            break;
                        } else if (i == blocks.length - 1) {
                            active = false;
                            removeSelection();
                        }
                    }
                } else if (rearrange) {
                    var blocko = blocks.map(a => a.id);
                    for (var i = 0; i < blocks.length; i++) {
                        if (checkAttach(blocko[i])) {
                            active = false;
                            $(drag).removeClass("dragging");
                            snap(drag, i, blocko);
                            break;
                        } else if (i == blocks.length - 1) {
                            if (beforeDelete(drag, blocks.filter(id => id.id == blocko[i])[0])) {
                                active = false;
                                $(drag).removeClass("dragging");
                                snap(drag, blocko.indexOf(prevblock), blocko);
                                break;
                            } else {
                                rearrange = false;
                                blockstemp = [];
                                active = false;
                                removeSelection();
                                break;
                            }
                        }
                    }
                }
            }
        }
        
        function checkAttach(id) {
            var draggedPosition = canvasPosition(drag);
            var draggedCenterX = draggedPosition.left + draggedPosition.width / 2;
            var targetBlock = blocks.find(block => block.id == id);
            return draggedCenterX >= targetBlock.x - (targetBlock.width / 2) - paddingx &&
                draggedCenterX <= targetBlock.x + (targetBlock.width / 2) + paddingx &&
                draggedPosition.top >= targetBlock.y - (targetBlock.height / 2) &&
                draggedPosition.top <= targetBlock.y + targetBlock.height;
        }
        
        function removeSelection() {
            $canvas.append($indicator);
            if (drag && drag.parentNode) {
                $(drag).remove();
            }
        }
        
        function firstBlock(type) {
            if (type == "drop") {
                if (!blockSnap(drag, true, undefined)) {
                    active = false;
                    removeSelection();
                    return;
                }
                active = false;
                var rootPosition = canvasPosition(drag);
                $(drag).css({
                    top: rootPosition.top + "px",
                    left: rootPosition.left + "px"
                });
                $canvas.append(drag);
                rootPosition = canvasPosition(drag);
                blocks.push({
                    parent: -1,
                    childwidth: 0,
                    id: parseInt($(drag).find(".blockid").val()),
                    x: rootPosition.left + rootPosition.width / 2,
                    y: rootPosition.top + rootPosition.height / 2,
                    width: rootPosition.width,
                    height: rootPosition.height
                });
            } else if (type == "rearrange") {
                $(drag).removeClass("dragging");
                rearrange = false;
                for (var w = 0; w < blockstemp.length; w++) {
                    if (blockstemp[w].id != parseInt($(drag).find(".blockid").val())) {
                        const blockParent = blockElement(blockstemp[w].id);
                        const arrowParent = arrowElement(blockstemp[w].id);
                        var blockPosition = canvasPosition(blockParent);
                        var arrowPosition = canvasPosition(arrowParent);
                        $(blockParent).css({ left: blockPosition.left - 1, top: blockPosition.top - 1 });
                        $(arrowParent).css({ left: arrowPosition.left - 1, top: arrowPosition.top - 1 });
                        $canvas.append(blockParent, arrowParent);
                        blockPosition = canvasPosition(blockParent);
                        blockstemp[w].x = blockPosition.left + blockPosition.width / 2;
                        blockstemp[w].y = blockPosition.top + blockPosition.height / 2;
                    }
                }
                var draggedRootPosition = canvasPosition(drag);
                blockstemp.find(block => block.id === blockIdOf(drag)).x =
                    draggedRootPosition.left + draggedRootPosition.width / 2;
                blockstemp.find(block => block.id === blockIdOf(drag)).y =
                    draggedRootPosition.top + draggedRootPosition.height / 2;
                blocks = blocks.concat(blockstemp);
                blockstemp = [];
            }
        }
        
        function drawArrow(arrow, x, y) {
            var $arrowBlock = $("<div class='arrowblock'><input type='hidden' class='arrowid'></div>");
            $arrowBlock.find(".arrowid").val(arrow.id);
            $canvas.append($arrowBlock);
            updateArrow(arrow, x, y);
        }

        function updateArrow(arrow, x, y) {
            var parent = blocks.find(block => block.id === arrow.parent);
            var $arrowId = $canvas.find('.arrowid[value="' + arrow.id + '"]');
            if (!parent || !$arrowId.length) return;

            var $arrowBlock = $arrowId.parent();
            var $svg = $("<svg><path></path><path></path></svg>").attr({
                preserveAspectRatio: "none",
                fill: "none",
                xmlns: "http://www.w3.org/2000/svg"
            });
            var $path = $svg.children("path").eq(0);
            var $arrowhead = $svg.children("path").eq(1);
            if (x < 0) {
                var elbow = parent.x - arrow.x + 5;
                $path.attr("d", "M" + elbow + " 0L" + elbow + " " + (paddingy / 2) +
                    "L5 " + (paddingy / 2) + "L5 " + y);
                $arrowhead.attr("d", "M0 " + (y - 5) + "H10L5 " + y + "L0 " + (y - 5) + "Z");
                $arrowBlock.css("left", arrow.x - 5 + "px");
            } else {
                $path.attr("d", "M20 0L20 " + (paddingy / 2) + "L" + x +
                    " " + (paddingy / 2) + "L" + x + " " + y);
                $arrowhead.attr("d", "M" + (x - 5) + " " + (y - 5) + "H" +
                    (x + 5) + "L" + x + " " + y + "L" + (x - 5) + " " + (y - 5) + "Z");
                $arrowBlock.css("left", parent.x - 20 + "px");
            }
            $path.attr({ stroke: "#4B5563", "stroke-width": "2px" });
            $arrowhead.attr("fill", "#4B5563");
            $svg.append($path, $arrowhead);
            $arrowBlock.empty().append($arrowId, $svg).css("top", parent.y + (parent.height / 2) + "px");
        }

        function snap(drag, i, blocko) {
            var $drag = $(drag);
            var draggedBlockId = parseInt($drag.find(".blockid").val());
            if (!rearrange) {
                $canvas.append(drag);
            }
            var totalwidth = 0;
            var totalremove = 0;
            for (var w = 0; w < blocks.filter(id => id.parent == blocko[i]).length; w++) {
                var children = blocks.filter(id => id.parent == blocko[i])[w];
                if (children.childwidth > children.width) {
                    totalwidth += children.childwidth + paddingx;
                } else {
                    totalwidth += children.width + paddingx;
                }
            }
            totalwidth += parseInt(window.getComputedStyle(drag).width);
            for (var w = 0; w < blocks.filter(id => id.parent == blocko[i]).length; w++) {
                var children = blocks.filter(id => id.parent == blocko[i])[w];
                if (children.childwidth > children.width) {
                    $(blockElement(children.id)).css("left", blocks.filter(a => a.id == blocko[i])[0].x - (totalwidth / 2) + totalremove + (children.childwidth / 2) - (children.width / 2) + "px");
                    children.x = blocks.filter(id => id.parent == blocko[i])[0].x - (totalwidth / 2) + totalremove + (children.childwidth / 2);
                    totalremove += children.childwidth + paddingx;
                } else {
                    $(blockElement(children.id)).css("left", blocks.filter(a => a.id == blocko[i])[0].x - (totalwidth / 2) + totalremove + "px");
                    children.x = blocks.filter(id => id.parent == blocko[i])[0].x - (totalwidth / 2) + totalremove + (children.width / 2);
                    totalremove += children.width + paddingx;
                }
            }
            var newParent = blocks.find(block => block.id === blocko[i]);
            $drag.css({
                left: newParent.x - (totalwidth / 2) + totalremove + "px",
                top: newParent.y + (newParent.height / 2) + paddingy + "px"
            });
            if (rearrange) {
                var draggedPosition = canvasPosition(drag);
                blockstemp.find(block => block.id === draggedBlockId).x =
                    draggedPosition.left + draggedPosition.width / 2;
                blockstemp.find(block => block.id === draggedBlockId).y =
                    draggedPosition.top + draggedPosition.height / 2;
                blockstemp.find(block => block.id === draggedBlockId).parent = blocko[i];
                for (var w = 0; w < blockstemp.length; w++) {
                    if (blockstemp[w].id != draggedBlockId) {
                        const blockParent = blockElement(blockstemp[w].id);
                        const arrowParent = arrowElement(blockstemp[w].id);
                        var blockPosition = canvasPosition(blockParent);
                        var arrowPosition = canvasPosition(arrowParent);
                        $(blockParent).css({ left: blockPosition.left + "px", top: blockPosition.top + "px" });
                        $(arrowParent).css({ left: arrowPosition.left + "px", top: arrowPosition.top + "px" });
                        $canvas.append(blockParent, arrowParent);

                        blockPosition = canvasPosition(blockParent);
                        blockstemp[w].x = blockPosition.left + blockPosition.width / 2;
                        blockstemp[w].y = blockPosition.top + blockPosition.height / 2;
                    }
                }
                blocks = blocks.concat(blockstemp);
                blockstemp = [];
            } else {
                var childPosition = canvasPosition(drag);
                blocks.push({
                    childwidth: 0,
                    parent: blocko[i],
                    id: draggedBlockId,
                    x: childPosition.left + childPosition.width / 2,
                    y: childPosition.top + childPosition.height / 2,
                    width: childPosition.width,
                    height: childPosition.height
                });
            }
            
            var arrowblock = blocks.find(a => a.id === draggedBlockId);
            var arrowx = arrowblock.x - blocks.filter(a => a.id == blocko[i])[0].x + 20;
            var arrowy = paddingy;
            drawArrow(arrowblock, arrowx, arrowy);
            
            if (blocks.filter(a => a.id == blocko[i])[0].parent != -1) {
                var flag = false;
                var idval = blocko[i];
                while (!flag) {
                    if (blocks.filter(a => a.id == idval)[0].parent == -1) {
                        flag = true;
                    } else {
                        var zwidth = 0;
                        for (var w = 0; w < blocks.filter(id => id.parent == idval).length; w++) {
                            var children = blocks.filter(id => id.parent == idval)[w];
                            if (children.childwidth > children.width) {
                                if (w == blocks.filter(id => id.parent == idval).length - 1) {
                                    zwidth += children.childwidth;
                                } else {
                                    zwidth += children.childwidth + paddingx;
                                }
                            } else {
                                if (w == blocks.filter(id => id.parent == idval).length - 1) {
                                    zwidth += children.width;
                                } else {
                                    zwidth += children.width + paddingx;
                                }
                            }
                        }
                        blocks.filter(a => a.id == idval)[0].childwidth = zwidth;
                        idval = blocks.filter(a => a.id == idval)[0].parent;
                    }
                }
                blocks.filter(id => id.id == idval)[0].childwidth = totalwidth;
            }
            if (rearrange) {
                rearrange = false;
                $(drag).removeClass("dragging");
            }
            rearrangeMe();
            checkOffset();
        }

        function touchblock(event) {
            dragblock = false;
            var $block = $(event.target).closest(".block");
            if (!$block.length || event.type === "mouseup" || event.button === 2) {
                return;
            }
            if (event.targetTouches) {
                mouse_x = event.targetTouches[0].clientX;
                mouse_y = event.targetTouches[0].clientY;
            } else {
                mouse_x = event.clientX;
                mouse_y = event.clientY;
            }
            if (!active && !rearrange) {
                dragblock = true;
                drag = $block[0];
                dragx = mouse_x - drag.getBoundingClientRect().left;
                dragy = mouse_y - drag.getBoundingClientRect().top;
            }
        }

        flowy.moveBlock = function(event) {
            if (event.targetTouches) {
                mouse_x = event.targetTouches[0].clientX;
                mouse_y = event.targetTouches[0].clientY;
            } else {
                mouse_x = event.clientX;
                mouse_y = event.clientY;
            }
            if (dragblock) {
                rearrange = true;
                $(drag).addClass("dragging");
                var blockid = blockIdOf(drag);
                prevblock = blocks.filter(a => a.id == blockid)[0].parent;
                blockstemp.push(blocks.filter(a => a.id == blockid)[0]);
                blocks = blocks.filter(function(e) {
                    return e.id != blockid
                });
                if (blockid != 0) {
                    var arrow = arrowElement(blockid);
                    if (arrow) $(arrow).remove();
                }
                var layer = blocks.filter(a => a.parent == blockid);
                var flag = false;
                var foundids = [];
                var allids = [];
                while (!flag) {
                    for (var i = 0; i < layer.length; i++) {
                        if (layer[i] != blockid) {
                            blockstemp.push(blocks.filter(a => a.id == layer[i].id)[0]);
                            const blockParent = blockElement(layer[i].id);
                            const arrowParent = arrowElement(layer[i].id);
                            const blockPosition = canvasPosition(blockParent);
                            const arrowPosition = canvasPosition(arrowParent);
                            const draggedPosition = canvasPosition(drag);
                            $(blockParent).css({
                                left: (blockPosition.left - draggedPosition.left) + "px",
                                top: (blockPosition.top - draggedPosition.top) + "px"
                            });
                            $(arrowParent).css({
                                left: (arrowPosition.left - draggedPosition.left) + "px",
                                top: (arrowPosition.top - draggedPosition.top) + "px"
                            });
                            $(drag).append(blockParent, arrowParent);
                            foundids.push(layer[i].id);
                            allids.push(layer[i].id);
                        }
                    }
                    if (foundids.length == 0) {
                        flag = true;
                    } else {
                        layer = blocks.filter(a => foundids.includes(a.parent));
                        foundids = [];
                    }
                }
                for (var i = 0; i < blocks.filter(a => a.parent == blockid).length; i++) {
                    var blocknumber = blocks.filter(a => a.parent == blockid)[i];
                    blocks = blocks.filter(function(e) {
                        return e.id != blocknumber
                    });
                }
                for (var i = 0; i < allids.length; i++) {
                    var blocknumber = allids[i];
                    blocks = blocks.filter(function(e) {
                        return e.id != blocknumber
                    });
                }
                if (blocks.length > 1) {
                    rearrangeMe();
                }
                dragblock = false;
            }
            if (active) {
                $(drag).css({ left: mouse_x - dragx + "px", top: mouse_y - dragy + "px" });
            } else if (rearrange) {
                var draggedPosition = canvasPointerPosition(mouse_x - dragx, mouse_y - dragy);
                $(drag).css({
                    left: draggedPosition.x + "px",
                    top: draggedPosition.y + "px"
                });
                var movingBlock = blockstemp.find(block => block.id === blockIdOf(drag));
                movingBlock.x = draggedPosition.x + canvasPosition(drag).width / 2;
                movingBlock.y = draggedPosition.y + canvasPosition(drag).height / 2;
            }
            if (active || rearrange) {
                if (mouse_x > canvas_div.getBoundingClientRect().width + canvas_div.getBoundingClientRect().left - 10 && mouse_x < canvas_div.getBoundingClientRect().width + canvas_div.getBoundingClientRect().left + 10) {
                    $canvas.scrollLeft($canvas.scrollLeft() + 10);
                } else if (mouse_x < canvas_div.getBoundingClientRect().left + 10 && mouse_x > canvas_div.getBoundingClientRect().left - 10) {
                    $canvas.scrollLeft($canvas.scrollLeft() - 10);
                } else if (mouse_y > canvas_div.getBoundingClientRect().height + canvas_div.getBoundingClientRect().top - 10 && mouse_y < canvas_div.getBoundingClientRect().height + canvas_div.getBoundingClientRect().top + 10) {
                    $canvas.scrollTop($canvas.scrollTop() + 10);
                } else if (mouse_y < canvas_div.getBoundingClientRect().top + 10 && mouse_y > canvas_div.getBoundingClientRect().top - 10) {
                    $canvas.scrollLeft($canvas.scrollLeft() - 10);
                }
                var blocko = blocks.map(a => a.id);
                for (var i = 0; i < blocks.length; i++) {
                    if (checkAttach(blocko[i])) {
                        var parent = blockElement(blocko[i]);
                        $indicator.css({
                            left: (parent.offsetWidth / 2) - 5 + "px",
                            top: parent.offsetHeight + "px"
                        }).removeClass("invisible");
                        $(parent).append($indicator);
                        break;
                    } else if (i == blocks.length - 1) {
                        $indicator.addClass("invisible");
                    }
                }
            }
        }

        function checkOffset() {
            if (!blocks.length) return;
            var leftmost = Math.min.apply(Math, blocks.map(block => block.x - block.width / 2));
            if (leftmost >= 0) return;

            blocks.forEach(function(block) {
                var element = blockElement(block.id);
                if (!element) return;
                $(element).css("left", block.x - block.width / 2 - leftmost + 20 + "px");

                if (block.parent === -1) return;
                var parent = blocks.find(item => item.id === block.parent);
                var arrow = arrowElement(block.id);
                if (!parent || !arrow) return;
                $(arrow).css("left", block.x < parent.x
                    ? block.x - leftmost + 15 + "px"
                    : parent.x - 20 - leftmost + 20 + "px");
            });

            blocks.forEach(function(block) {
                var element = blockElement(block.id);
                if (!element) return;
                var blockPosition = canvasPosition(element);
                block.x = blockPosition.left + blockPosition.width / 2 - 20;
            });
        }

        function rearrangeMe() {
            var parentIds = Array.from(new Set(blocks.map(block => block.parent)))
                .filter(parentId => parentId !== -1);

            parentIds.forEach(function(parentId) {
                var parent = blocks.find(block => block.id === parentId);
                var children = blocks.filter(block => block.parent === parentId);
                if (!parent || !children.length) return;

                var totalWidth = children.reduce(function(width, child, index) {
                    if (!blocks.some(block => block.parent === child.id)) {
                        child.childwidth = 0;
                    }
                    return width + Math.max(child.childwidth, child.width) +
                        (index < children.length - 1 ? paddingx : 0);
                }, 0);
                parent.childwidth = totalWidth;

                var offset = 0;
                children.forEach(function(child) {
                    var subtreeWidth = Math.max(child.childwidth, child.width);
                    var $blockInput = $canvas.find(".blockid[value='" + child.id + "']");
                    if (!$blockInput.length) return;

                    var blockElement = $blockInput.parent()[0];
                    var childWidth = child.childwidth > child.width ? child.childwidth : child.width;
                    child.x = parent.x - (totalWidth / 2) + offset + (childWidth / 2);
                    child.y = parent.y + (parent.height / 2) + paddingy + (child.height / 2);
                    $(blockElement).css({
                        left: child.x - (child.width / 2) + "px",
                        top: child.y - (child.height / 2) + "px"
                    });

                    updateArrow(child, child.x - parent.x + 20, paddingy);
                    offset += subtreeWidth + paddingx;
                });
            });
        }
        
        $(document).on("mousedown.flowy", flowy.beginDrag);
        $(document).on("mousedown.flowy", touchblock);
        $(document).on("touchstart.flowy", flowy.beginDrag);
        $(document).on("touchstart.flowy", touchblock);
        $(document).on("mouseup.flowy", touchblock);
        $(document).on("mousemove.flowy", flowy.moveBlock);
        $(document).on("touchmove.flowy", flowy.moveBlock);
        $(document).on("mouseup.flowy", flowy.endDrag);
        $(document).on("touchend.flowy", flowy.endDrag);
    }

    function blockGrabbed(block) {
        grab(block);
    }

    function blockReleased() {
        release();
    }

    function blockSnap(drag, first, parent) {
        return snapping(drag, first, parent);
    }

    function beforeDelete(drag, parent) {
        return rearrange(drag, parent);
    }

    flowy.load();
}
