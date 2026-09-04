/*
 * LEO JS code for LEO CDP - version 1.0.0 - Customer 360 Web SDK
 * Cross-domain tracking, unified customer identity resolution, and personalization.
 */

//  Leo Tag Audit - Checks for the presence of common tracking tags on the page
(function(global) {
	'use strict';

	function hasScriptMatching(pattern) {
		var scripts = global.document && global.document.getElementsByTagName('script');

		if (!scripts) {
			return false;
		}

		for (var index = 0; index < scripts.length; index += 1) {
			if (pattern.test(scripts[index].src || '')) {
				return true;
			}
		}

		return false;
	}

	function hasGlobalFunction(name) {
		return typeof global[name] === 'function';
	}

	function checkTrackingTags() {
		var dataLayer = global.dataLayer;
		var hasGoogleTagManagerScript = hasScriptMatching(/googletagmanager\.com\/gtm\.js(?:\?|$)/i);
		var hasGoogleAnalyticsScript = hasScriptMatching(/googletagmanager\.com\/gtag\/js(?:\?|$)/i);

		return {
			ga4: hasGlobalFunction('gtag') || hasGoogleAnalyticsScript,
			gtm: hasGoogleTagManagerScript || !!global.google_tag_manager,
			metaPixel: hasGlobalFunction('fbq') || hasScriptMatching(/connect\.facebook\.net\/[^/]+\/fbevents\.js/i),
			tiktokPixel: !!global.ttq || hasScriptMatching(/analytics\.tiktok\.com\/i18n\/pixel\/events\.js/i),
			checkedAt: new Date().toISOString(),
			dataLayer: Array.isArray(dataLayer)
		};
	}

	global.LeoTagAudit = global.LeoTagAudit || {};
	global.LeoTagAudit.checkTrackingTags = checkTrackingTags;
})(typeof window !== 'undefined' ? window : this);

// LEO Proxy : collect and send events to LEO CDP server via cross-domain iframe
(function() {
	var leoC360SourceId = window.leoC360DataSourceId || window.leoC360SourceId || window.leoDataSourceId || "";
	var batchSize = typeof window.leoTrackingBatchSize === 'number'
		? window.leoTrackingBatchSize
		: (typeof window.leoObserverBatchSize === 'number' ? window.leoObserverBatchSize : 10);
	window.leoObserverBatchSize = batchSize;
	window.leoTrackingBatchSize = batchSize;
	if (leoC360SourceId) {
		window.leoC360SourceId = leoC360SourceId;
		window.leoC360DataSourceId = leoC360SourceId;
	}

	var TIME_TO_ADD_PROXY_IFRAME = typeof window.leoProxyDelay === 'number' ? window.leoProxyDelay : 300; // delay to avoid blocking page load
    
    if (typeof window.LeoObserverProxy === "undefined") {
    	
    	// Resolve parent origin cleanly (including port)
    	var leoProxyOrigin = window.location.origin || (window.location.protocol + '//' + window.location.host);
    	var isParentHttps = window.location.protocol === "https:";

    	// Detect script source to automatically resolve CDN / proxy paths
    	var scriptSrc = "";
    	if (document.currentScript && document.currentScript.src) {
    		scriptSrc = document.currentScript.src;
    	} else {
    		var scripts = document.getElementsByTagName('script');
    		for (var sIdx = scripts.length - 1; sIdx >= 0; sIdx--) {
    			var sSrc = scripts[sIdx].src || '';
    			if (sSrc.indexOf('leo.proxy.js') >= 0 || sSrc.indexOf('c360-web-sdk') >= 0) {
    				scriptSrc = sSrc;
    				break;
    			}
    		}
    	}

    	var scriptOrigin = "";
    	if (scriptSrc) {
    		try {
    			var parsedScript = new URL(scriptSrc, window.location.href);
    			scriptOrigin = parsedScript.origin;
    		} catch(e) {}
    		if (!scriptOrigin) {
    			var sMatch = scriptSrc.match(/^(https?:\/\/[^/]+)(\/.*)?$/i);
    			if (sMatch) {
    				scriptOrigin = sMatch[1];
    			}
    		}
    	}

    	// Extract tracking endpoint if provided
    	var trackingEndpoint = window.leoTrackingEndpoint || window.leoObserverTrackingEndpoint || "";
    	var endpointOrigin = "";
    	var endpointHost = "";
    	if (trackingEndpoint) {
    		try {
    			var parsedEp = new URL(trackingEndpoint, window.location.href);
    			endpointOrigin = parsedEp.origin;
    			endpointHost = parsedEp.host;
    		} catch(e) {}
    		if (!endpointHost) {
    			var epMatch = trackingEndpoint.match(/^(https?:\/\/)?([^/]+)(\/.*)?$/i);
    			if (epMatch) {
    				endpointHost = epMatch[2];
    				endpointOrigin = (epMatch[1] || (isParentHttps ? "https://" : "http://")) + epMatch[2];
    			}
    		}
    	}
    	
    	// Normalize log domain (order: leoTrackingEndpoint host -> leoObserverLogDomain -> script origin host -> beta.leocdp.com)
    	var rawLogDomain = endpointHost || window.leoObserverLogDomain || (scriptOrigin ? (scriptOrigin.split('://')[1] || '') : "beta.leocdp.com");
    	var logProtocol;
    	if (isParentHttps) {
    		logProtocol = "https://";
    	} else if (rawLogDomain.indexOf("http://") === 0) {
    		logProtocol = "http://";
    	} else if (rawLogDomain.indexOf("https://") === 0) {
    		logProtocol = "https://";
    	} else {
    		logProtocol = window.location.protocol ? (window.location.protocol + "//") : "https://";
    	}

    	var cleanLogDomain = rawLogDomain.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    	var targetPostMessage = logProtocol + cleanLogDomain;
    	
		// Resolve proxy HTML URL with canonical iframe path.
        var proxyPath = window.leoCdpProxyPath || "";
        var proxyHtmlUrl = "";
        if (proxyPath) {
        	if (proxyPath.indexOf("http://") === 0 || proxyPath.indexOf("https://") === 0) {
        		proxyHtmlUrl = proxyPath + "#";
        	} else if (proxyPath.charAt(0) === "/") {
        		proxyHtmlUrl = targetPostMessage + proxyPath + "#";
        	} else {
        		proxyHtmlUrl = targetPostMessage + "/" + proxyPath + "#";
        	}
        } else {
        	proxyHtmlUrl = targetPostMessage + "/cdp-sdk/html/cdp-event-proxy.html#";
        }

        if (isParentHttps) {
        	proxyHtmlUrl = proxyHtmlUrl.replace(/^http:\/\//i, "https://");
        	targetPostMessage = targetPostMessage.replace(/^http:\/\//i, "https://");
        }

        var LeoObserverProxy = { 
        	'synchLeoVisitorCallback' : false,
        	'personalizationCallbacks': {},
        	'isReady': false,
        	'visitorId': '',
        	'sessionKey': '',
        	'fingerprintId': ''
        };
        window.LeoObserverProxy = LeoObserverProxy;
        window.LeoIframeProxy = false;
        
        var pendingEvents = [];
        var iframeId = "leotech_event_proxy";
        setTimeout(function(){
        	var node = document.getElementById(iframeId);

        	if( node == null ){
        		// Check for cross-domain visitor ID in query parameter, hash, or injected global
        		var leosyn = '';
        		var searchStr = window.location.search || '';
        		var hashStr = window.location.hash || '';
        		var match = searchStr.match(/[?&]leosyn=([^&#]+)/) || hashStr.match(/[#&]leosyn=([^&#]+)/);
        		if (match) {
        			try {
        				leosyn = decodeURIComponent(match[1]);
        			} catch (e) {
        				leosyn = match[1];
        			}
        		}
        		if (!leosyn && typeof window.injectedVisitorId === 'string' && window.injectedVisitorId.length > 5) {
        			leosyn = window.injectedVisitorId;
        		}

        		var iframeProxyUrl = proxyHtmlUrl + cleanLogDomain + '_' + encodeURIComponent(leoProxyOrigin);
    	        if( leosyn && leosyn.length > 5 ) {
    	        	iframeProxyUrl = iframeProxyUrl + '_' + encodeURIComponent(leosyn);
    	        }

    	        // Cross domain iframe
    	        var iframeProxy = document.createElement("iframe");
    	        iframeProxy.setAttribute("style", "display:none!important;width:0px!important;height:0px!important;border:none!important;" );
    	        iframeProxy.setAttribute("sandbox", "allow-scripts allow-same-origin");
    	        iframeProxy.width = 0;
    	        iframeProxy.height = 0;
    	        iframeProxy.id = iframeId;
    	        iframeProxy.name = iframeId;
    	        iframeProxy.src = iframeProxyUrl;

    	        // Append to trigger iframe post back data to server
    	        var body = document.getElementsByTagName("body");
    	        if (body.length > 0) {
    	            body[0].appendChild(iframeProxy);
    	            window.LeoIframeProxy = iframeProxy;
    	        } else if (document.documentElement) {
    	        	document.documentElement.appendChild(iframeProxy);
    	        	window.LeoIframeProxy = iframeProxy;
    	        }
        	}
        }, TIME_TO_ADD_PROXY_IFRAME);

        // Put message to the queue in the child iframe
        var putEventToQueue = function(msg) {
        	if (!LeoObserverProxy.isReady || !window.LeoIframeProxy || !window.LeoIframeProxy.contentWindow) {
        		pendingEvents.push(msg);
        		return;
        	}
        	try {
        		window.LeoIframeProxy.contentWindow.postMessage(msg, targetPostMessage);
        	} catch(err) {
        		try {
        			window.LeoIframeProxy.contentWindow.postMessage(msg, '*');
        		} catch(e) {}
        	}
        };

        var flushPendingEvents = function() {
        	if (window.LeoIframeProxy && window.LeoIframeProxy.contentWindow && pendingEvents.length > 0) {
        		while (pendingEvents.length > 0) {
        			var queuedMsg = pendingEvents.shift();
        			try {
        				window.LeoIframeProxy.contentWindow.postMessage(queuedMsg, targetPostMessage);
        			} catch(err) {
        				try {
        					window.LeoIframeProxy.contentWindow.postMessage(queuedMsg, '*');
        				} catch(e) {}
        			}
        		}
        	}
        };

        LeoObserverProxy.messageHandler = function(data) {
        	var eventPayload = null;
        	var eventType = '';

        	if (typeof data === 'string') {
        		try {
        			eventPayload = JSON.parse(data);
        			eventType = eventPayload.event || '';
        		} catch (ex) {
        			eventType = data;
        		}
        	} else if (typeof data === 'object' && data !== null) {
        		eventPayload = data;
        		eventType = eventPayload.event || '';
        	}

            if (eventType === "LeoObserverProxyLoaded") {
 				initLeoContextSession();
            } 
            else if (eventType === "LeoObserverProxyReady" || (typeof data === 'string' && data.indexOf("LeoObserverProxyReady") === 0)) {
            	LeoObserverProxy.isReady = true;
            	flushPendingEvents();
            	var sessionContext = {
            		sessionKey: (eventPayload && eventPayload.sessionKey) || LeoObserverProxy.sessionKey || '',
            		visitorId: (eventPayload && eventPayload.visitorId) || LeoObserverProxy.visitorId || '',
            		fingerprintId: (eventPayload && eventPayload.fingerprintId) || LeoObserverProxy.fingerprintId || '',
            		ready: true
            	};

            	if (sessionContext.sessionKey) LeoObserverProxy.sessionKey = sessionContext.sessionKey;
            	if (sessionContext.visitorId) LeoObserverProxy.visitorId = sessionContext.visitorId;
            	if (sessionContext.fingerprintId) LeoObserverProxy.fingerprintId = sessionContext.fingerprintId;

            	var f = window.leoObserverProxyReady;
                if (typeof f === "function") {
                	try {
                		f(sessionContext);
                	} catch(cbErr) {
                		console.error("[LeoProxy] leoObserverProxyReady callback error:", cbErr);
                	}
                }

                if (typeof window.dispatchEvent === "function" && typeof CustomEvent === "function") {
                	window.dispatchEvent(new CustomEvent("leo_observer_ready", { detail: sessionContext }));
                }
            }
            else if (typeof data === 'string' && data.indexOf('synchLeoVisitorId') === 0) {
            	var vid = data.substring('synchLeoVisitorId-'.length);
            	LeoObserverProxy.visitorId = vid;
            	if (typeof LeoObserverProxy.synchLeoVisitorCallback === 'function') {
            		LeoObserverProxy.synchLeoVisitorCallback(vid);
            	}
            }  
            else if (eventType === 'leoPersonalization' && eventPayload && eventPayload.slotId) {
            	var cb = LeoObserverProxy.personalizationCallbacks[eventPayload.slotId];
            	if (typeof cb === 'function') {
            		cb(eventPayload.data || eventPayload);
            	}
            }
        };
        
        // Listen to messages from child iframe
        function bindEvent(element, metricName, eventHandler) {
            if (element.addEventListener) {
                element.addEventListener(metricName, eventHandler, false);
            } else if (element.attachEvent) {
                element.attachEvent('on' + metricName, eventHandler);
            }
        }
        
        bindEvent(window, 'message', function(e) {
        	// Allow origin matching targetPostMessage or if origins match hostname
        	if (e.origin && targetPostMessage && targetPostMessage !== '*' && e.origin !== targetPostMessage) {
        		// Check hostname match (for port differences in development)
        		var eventOriginHost = (e.origin.split('://')[1] || '').split(':')[0];
        		var targetHost = (cleanLogDomain.split(':')[0] || '');
        		if (eventOriginHost !== targetHost && targetHost !== 'localhost' && eventOriginHost !== 'localhost') {
        			return;
        		}
        	}  
        	LeoObserverProxy.messageHandler(e.data);
        });

        var getObserverParams = function(metricName, eventData, profileObject, extData, transactionId, shoppingCartItems, transactionValue, currencyCode ) {
			var tprefurl = document.referrer || "";
			var tprefdomain = extractRootDomain(tprefurl);
			
            var mediaHost = extractRootDomain(document.location.href);
			var tpname = window.srcTouchpointName || document.title || "";
            var tpurl = window.srcTouchpointUrl || document.location.href || "";
            var currentBatch = typeof window.leoTrackingBatchSize === 'number'
            	? window.leoTrackingBatchSize
            	: (typeof window.leoObserverBatchSize === 'number' ? window.leoObserverBatchSize : batchSize);
            var currentObsId = window.leoC360DataSourceId || window.leoC360SourceId || window.leoDataSourceId || leoC360SourceId || "";

			var screen = "";
			if(window.screen) {
				screen = window.screen.width + "x" + window.screen.height; 
			}
			            
            // Tracking parameters
            var params = {
                'obsid': currentObsId,
                'batchsize': currentBatch,
                'mediahost': mediaHost,
				'screen': screen,
                'tprefurl': encodeURIComponent(tprefurl),
                'tprefdomain': tprefdomain,
                'tpurl': encodeURIComponent(tpurl),
                'tpname': encodeURIComponent(tpname)
            };
            
            if(typeof metricName === "string" && typeof eventData === "object" && eventData !== null){
            	params['metric'] = metricName;                
             	params['eventdata'] = encodeURIComponent(JSON.stringify(eventData)); 
            }
            if(typeof profileObject === "object" && profileObject !== null){
            	params['profiledata'] = JSON.stringify(profileObject); 
            }
            if(typeof extData === "object" && extData !== null){
            	params['extData'] = JSON.stringify(extData); 
            }
            if(typeof shoppingCartItems === "object" && shoppingCartItems !== null){
            	params['tsid'] = typeof transactionId === "string" ? transactionId : ""; 
            	params['scitems'] = JSON.stringify(shoppingCartItems); 
            	params['tsval'] = typeof transactionValue === "number" ? transactionValue : 0; 
            	params['tscur'] = typeof currencyCode === "string" ? currencyCode : "USD"; 
            }
            return params;
        };
        
        var extractRootDomain = function(url){
        	if (!url) return "";
        	try {
        		var hostname = new URL(url).hostname;
        		var toks = hostname.split('.');
        		return toks.slice(-1 * Math.min(2, toks.length)).join('.');
        	} catch(e) {
        		return "";
        	}
        };

		var initLeoContextSession = function(){
            var payload = JSON.stringify({
                'call': 'getContextSession',
                'params': getObserverParams(false)
            });
            putEventToQueue(payload);
		};
		
		LeoObserverProxy.synchLeoVisitorId = function(callback) {
			LeoObserverProxy.synchLeoVisitorCallback = callback;
			if (LeoObserverProxy.visitorId && typeof callback === 'function') {
				callback(LeoObserverProxy.visitorId);
			}
            var payload = JSON.stringify({
                'call': 'synchLeoVisitorId'
            });
            putEventToQueue(payload);
        };

        // event-view(pageview|screenview|storeview|trueview|placeview,contentId,sessionKey,visitorId)
        LeoObserverProxy.recordViewEvent = function(metricName, eventData) {
            if (typeof eventData !== "object" || eventData === null) {
            	eventData = {};
            }
            var params = getObserverParams(metricName, eventData);
            var payload = JSON.stringify({
                'call': 'doTracking',
                'params': params,
                'eventType': 'view'
            });
            putEventToQueue(payload);
        };

        // event-action(click|play|touch|contact|watch|test,sessionKey,visitorId)
        LeoObserverProxy.recordActionEvent = function(metricName, eventData) {
            if (typeof eventData !== "object" || eventData === null) {
            	eventData = {};
            }
            var params = getObserverParams(metricName, eventData);
            var payload = JSON.stringify({
                'call': 'doTracking',
                'params': params,
                'eventType': 'action'
            });
            putEventToQueue(payload);
        };

        // event-conversion(add_to_cart|submit_form|checkout|join,sessionKey,visitorId)
        LeoObserverProxy.recordConversionEvent = function(metricName, eventData, transactionId, shoppingCartItems, transactionValue, currencyCode) {
            if (typeof eventData !== "object" || eventData === null) {
            	eventData = {};
            }
            var params = getObserverParams(metricName, eventData, false, false, transactionId, shoppingCartItems, transactionValue, currencyCode);
            var payload = JSON.stringify({
                'call': 'doTracking',
                'params': params,
                'eventType': 'conversion'
            });
            putEventToQueue(payload);
        };
        
        // event-feedback(submit-survey|submit-ces-form|submit-csat-form|submit-nps-form)
        LeoObserverProxy.recordFeedbackEvent = function(metricName, eventData) {
            if (typeof eventData !== "object" || eventData === null) {
            	eventData = {};
            }
            var params = getObserverParams(metricName, eventData);
            var payload = JSON.stringify({
                'call': 'doTracking',
                'params': params,
                'eventType': 'feedback'
            });
            putEventToQueue(payload);
        };
        
        // Update contact profile identities using Embedded Web Form or login session
        LeoObserverProxy.updateProfileBySession = function(profileObject, extData) {
            if (typeof profileObject === "object" && profileObject !== null) {
                var payload = JSON.stringify({
                    'call': 'updateProfile',
                    'params': getObserverParams(false, false, profileObject, extData)
                });
                putEventToQueue(payload);
            }
        };

        // Customer personalization: query personalized content or recommendations for current visitor
        LeoObserverProxy.getPersonalization = function(slotId, callback) {
        	if (typeof callback === 'function' && slotId) {
        		LeoObserverProxy.personalizationCallbacks[slotId] = callback;
        	}
        	var payload = JSON.stringify({
        		'call': 'getPersonalization',
        		'slotId': slotId || '',
        		'params': getObserverParams(false)
        	});
        	putEventToQueue(payload);
        };

        // Helpers to inspect resolved visitor & session identity
        LeoObserverProxy.getVisitorId = function() {
        	return LeoObserverProxy.visitorId || '';
        };

        LeoObserverProxy.getSessionKey = function() {
        	return LeoObserverProxy.sessionKey || '';
        };

        // Expose high-level LeoObserver helper facade
        var LeoObserver = window.LeoObserver || {};
        LeoObserver.recordEventPageView = LeoObserver.recordEventPageView || function(eventData) {
            LeoObserverProxy.recordViewEvent("page-view", eventData || {});
        };
        LeoObserver.recordEventContentView = LeoObserver.recordEventContentView || function(eventData) {
            LeoObserverProxy.recordViewEvent("content-view", eventData || {});
        };
        LeoObserver.recordEventItemView = LeoObserver.recordEventItemView || function(eventData) {
            LeoObserverProxy.recordViewEvent("item-view", eventData || {});
        };
        LeoObserver.recordEventClickDetails = LeoObserver.recordEventClickDetails || function(eventData) {
            LeoObserverProxy.recordActionEvent("click-details", eventData || {});
        };
        LeoObserver.recordEventSearch = LeoObserver.recordEventSearch || function(eventData) {
            LeoObserverProxy.recordActionEvent("search", eventData || {});
        };
        LeoObserver.recordEventSubmitContact = LeoObserver.recordEventSubmitContact || function(eventData) {
            LeoObserverProxy.recordActionEvent("submit-contact", eventData || {});
        };
        LeoObserver.recordEventRegisterAccount = LeoObserver.recordEventRegisterAccount || function(eventData) {
            LeoObserverProxy.recordActionEvent("register-account", eventData || {});
        };
        LeoObserver.recordEventUserLogin = LeoObserver.recordEventUserLogin || function(eventData) {
            LeoObserverProxy.recordActionEvent("user-login", eventData || {});
        };
        LeoObserver.recordEventLogout = LeoObserver.recordEventLogout || function(eventData) {
            LeoObserverProxy.recordActionEvent("logout", eventData || {});
        };
        LeoObserver.recordEventShortLinkClick = LeoObserver.recordEventShortLinkClick || function(eventData) {
            LeoObserverProxy.recordActionEvent("short-link-click", eventData || {});
        };
        LeoObserver.recordEventLogin = LeoObserver.recordEventLogin || function(eventData) {
            LeoObserverProxy.recordViewEvent("login-success", eventData || {});
        };
        LeoObserver.recordEventAskQuestion = LeoObserver.recordEventAskQuestion || function(eventData) {
            LeoObserverProxy.recordActionEvent("ask-question", eventData || {});
        };
        LeoObserver.recordEventConversion = LeoObserver.recordEventConversion || function(transactionId, transactionValue, currencyCode, items, eventData) {
            LeoObserverProxy.recordConversionEvent("purchase", eventData || {}, transactionId, items || [], transactionValue, currencyCode || "USD");
        };
        LeoObserver.recordEventFeedback = LeoObserver.recordEventFeedback || function(feedbackType, feedbackData) {
            LeoObserverProxy.recordFeedbackEvent(feedbackType || "submit-survey", feedbackData || {});
        };
        LeoObserver.updateProfileBySession = LeoObserver.updateProfileBySession || function(profileData, extData) {
            LeoObserverProxy.updateProfileBySession(profileData || {}, extData);
        };
        LeoObserver.getPersonalization = LeoObserver.getPersonalization || function(slotId, callback) {
            LeoObserverProxy.getPersonalization(slotId, callback);
        };
        LeoObserver.synchLeoVisitorId = LeoObserver.synchLeoVisitorId || function(callback) {
            LeoObserverProxy.synchLeoVisitorId(callback);
        };
        LeoObserver.getVisitorId = LeoObserver.getVisitorId || function() {
            return LeoObserverProxy.getVisitorId();
        };
        LeoObserver.getSessionKey = LeoObserver.getSessionKey || function() {
            return LeoObserverProxy.getSessionKey();
        };
        LeoObserver.isReady = LeoObserver.isReady || function() {
            return !!LeoObserverProxy.isReady;
        };
        LeoObserver.track = LeoObserver.track || function(metricName, eventData, eventType) {
            eventType = eventType || "action";
            if (eventType === "view") {
                LeoObserverProxy.recordViewEvent(metricName || "page-view", eventData || {});
            } else if (eventType === "conversion") {
                LeoObserverProxy.recordConversionEvent(metricName || "conversion", eventData || {});
            } else if (eventType === "feedback") {
                LeoObserverProxy.recordFeedbackEvent(metricName || "feedback", eventData || {});
            } else {
                LeoObserverProxy.recordActionEvent(metricName || "action", eventData || {});
            }
        };
        LeoObserver.addTrackingAllLinks = LeoObserver.addTrackingAllLinks || function() {
            setTimeout(function () {
                var links = document.querySelectorAll('a');
                for (var i = 0; i < links.length; i++) {
                    (function(aNode) {
                        aNode.addEventListener('click', function() {
                            var url = aNode.getAttribute('href') || "";
                            var text = aNode.innerText || aNode.textContent || "";
                            LeoObserver.recordEventClickDetails({ 'url': url, 'link-text': text });
                        });
                    })(links[i]);
                }
            }, 500);
        };
        LeoObserver.addTrackingAllButtons = LeoObserver.addTrackingAllButtons || function() {
            setTimeout(function () {
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    (function(btnNode) {
                        btnNode.addEventListener('click', function() {
                            var text = btnNode.innerText || btnNode.textContent || "";
                            LeoObserver.recordEventClickDetails({ 'button-text': text });
                        });
                    })(buttons[i]);
                }
            }, 500);
        };

        window.LeoObserver = LeoObserver;
        window.LeoObserverProxy = LeoObserverProxy;
    }
})();