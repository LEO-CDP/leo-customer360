/**
 * C360 Event Observer version 1.0.0 - Updated for Beacon & Session Handling
 */
(function(global) {
    'use strict';

    // Configuration
    var CONFIG = {
        CONTENT_TYPE_FORM: 'application/x-www-form-urlencoded',
        RETRY_DELAY: 2222,
        TIME_TO_FLUSH: 5555,
        DEBUG: false // Set to true to see logs
    };

    // Global session tracking variable
    var localSessionKey = "";

    function hasOwn(obj, key) {
        return Object.prototype.hasOwnProperty.call(obj, key);
    }

    function toSafeParamValue(value) {
        if (value === null || typeof value === 'undefined') {
            return '';
        }
        if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
            return value;
        }
        if (typeof value === 'object') {
            try {
                return JSON.stringify(value);
            } catch (e) {
                return String(value);
            }
        }
        return String(value);
    }

    // Logger Utility
    function log(msg, type) {
        if (!window.console) return;
        var prefix = "[LeoCDP] ";
        if (type === 'error') {
            window.console.error(prefix + msg);
        } else if (CONFIG.DEBUG) {
            window.console.log(prefix + msg);
        }
    }

    // --- Network Layer ---

    function createXHR() {
        if (window.XMLHttpRequest) {
            return new XMLHttpRequest();
        }
        try {
            return new ActiveXObject("Microsoft.XMLHTTP");
        } catch (e) {
            log("XHR not supported.", "error");
            return null;
        }
    }

    var Network = {
        request: function(method, url, data, headers, callback) {
            var xhr = createXHR();
            if (!xhr) return;

            xhr.open(method, url, true);
            
            if (headers) {
                Object.keys(headers).forEach(function(key) {
                    xhr.setRequestHeader(key, headers[key]);
                });
            }

            xhr.withCredentials = true;

            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4) {
                    var isSuccess = (xhr.status >= 200 && xhr.status < 300) || xhr.status === 304 || xhr.status === 1223;
                    if (callback) {
                        callback(isSuccess, xhr.responseText, xhr);
                    }
                }
            };

            xhr.send(data);
        },

        get: function(url, callback) {
            this.request("GET", url, null, null, function(success, response, xhr) {
                if (success && callback) {
                    callback(response);
                } else {
                    log("GET failed: " + url, "error");
                }
            });
        },

        post: function(url, data, callback) {
            var isJson = typeof data === 'string' && (data.charAt(0) === '{' || data.charAt(0) === '[');
            var headers = { 'Content-Type': isJson ? 'application/json' : CONFIG.CONTENT_TYPE_FORM };
            if (isJson) {
                headers['Accept'] = 'application/json';
            }
            this.request("POST", url, data, headers, function(success, response) {
                if (success && callback) {
                    callback(response);
                } else if (!success) {
                    log("POST failed: " + url, "error");
                }
            });
        },

        postJson: function(url, data, callback) {
            var payloadStr = typeof data === 'string' ? data : JSON.stringify(data);
            var headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            };
            this.request("POST", url, payloadStr, headers, function(success, response, xhr) {
                if (callback) {
                    callback(success, response, xhr);
                }
            });
        },

        /**
         * Tries to use sendBeacon, falls back to XHR.
         * Note: Beacon responses cannot be read by JS.
         * @param {string} url 
         * @param {string} payload - JSON string payload
         * @param {function} callback - (success, responseText)
         * @param {boolean} forceXHR - If true, bypasses Beacon to ensure we get a response
         */
        sendBeaconOrXHR: function(url, payload, callback, forceXHR) {
            // Determine content type from payload
            var isJson = typeof payload === 'string' && (payload.charAt(0) === '{' || payload.charAt(0) === '[');
            var mimeType = isJson ? 'application/json' : CONFIG.CONTENT_TYPE_FORM;

            if (!forceXHR && navigator.sendBeacon) {
                try {
                    var blob = new Blob([payload], { type: mimeType });
                    var queued = navigator.sendBeacon(url, blob);
                    if (queued) {
                        if (callback) callback(true, null);
                        return;
                    }
                } catch (e) {
                    log("Beacon failed, falling back to XHR: " + e.message, "error");
                }
            }

            // Fallback (or forced) to XHR
            if (isJson) {
                this.postJson(url, payload, function(success, resp) {
                    if (callback) callback(success, resp);
                });
            } else {
                this.post(url, payload, function(resp) {
                    if (callback) callback(true, resp);
                });
            }
        }
    };


    function trackingCallback(text){
        if(typeof text === "string" && text.length > 0){
            try {
                var data = JSON.parse(text);
                // Check if server returned a session identifier or key
                var returnedSession = data.session_id || data.sessionKey;
                if(returnedSession && localSessionKey !== returnedSession){
                    localSessionKey = returnedSession;
                    
                    // Update the main object if method exists
                    if (global.LeoEventObserver && typeof global.LeoEventObserver.setSessionKey === 'function') {
                        global.LeoEventObserver.setSessionKey(localSessionKey);
                    }
                    log("SessionKey updated: " + localSessionKey);
                }
            } catch (e) {
                // Non-JSON or beacon acknowledgement, ignore
            }
        }
    }

    // --- Batching & Queue Layer ---

    var BatchManager = {
        queues: {},
        isFlushing: {},

        createPayload: function(items) {
            var eventsList = [];
            var dataSourceId = "";
            var userId = "";
            var sessionKey = localSessionKey || (global.LeoEventObserver && global.LeoEventObserver.getSessionKey ? global.LeoEventObserver.getSessionKey(true) : "");

            for (var i = 0; i < items.length; i++) {
                var it = items[i];
                if (!dataSourceId && it.obsid) {
                    dataSourceId = it.obsid;
                }

                var evtName = it.metric || it.eventType || "page-view";
                var evtData = {};
                if (it.eventdata) {
                    try {
                        evtData = JSON.parse(decodeURIComponent(it.eventdata));
                    } catch(e) {
                        evtData = { raw: it.eventdata };
                    }
                }

                // Extract user identity if provided in profile data or eventData
                if (!userId) {
                    if (it.profiledata) {
                        try {
                            var pData = typeof it.profiledata === 'object' ? it.profiledata : JSON.parse(it.profiledata);
                            userId = pData.loginId || pData.userId || pData.user_id || pData.email || "";
                        } catch(pe) {}
                    }
                    if (!userId && evtData) {
                        userId = evtData.user_id || evtData.userId || evtData.loginId || "";
                    }
                }

                var pageUrl = "";
                try { pageUrl = decodeURIComponent(it.tpurl || ""); } catch(ue) { pageUrl = it.tpurl || ""; }

                var pageTitle = "";
                try { pageTitle = decodeURIComponent(it.tpname || ""); } catch(te) { pageTitle = it.tpname || ""; }

                var refUrl = "";
                try { refUrl = decodeURIComponent(it.tprefurl || ""); } catch(re) { refUrl = it.tprefurl || ""; }

                var singleEvent = {
                    "event_name": evtName,
                    "event_time": new Date().toISOString(),
                    "page_url": pageUrl,
                    "page_title": pageTitle,
                    "referrer_url": refUrl,
                    "visitor_id": it.visid || (global.LeoEventObserver ? global.LeoEventObserver.getVisitorId() : ""),
                    "session_id": sessionKey,
                    "fingerprint_id": it.fgp || lscache.get("leocdp_fgp") || "",
                    "event_data": evtData
                };

                // Elevate UTM parameters to top-level event for fast querying
                if (evtData && typeof evtData === 'object') {
                    if (evtData.utm_source) singleEvent.utm_source = evtData.utm_source;
                    if (evtData.utm_medium) singleEvent.utm_medium = evtData.utm_medium;
                    if (evtData.utm_campaign) singleEvent.utm_campaign = evtData.utm_campaign;
                    if (evtData.utm_term) singleEvent.utm_term = evtData.utm_term;
                    if (evtData.utm_content) singleEvent.utm_content = evtData.utm_content;
                }

                // E-commerce & Conversion attribution fields
                if (it.tsid) singleEvent.transaction_id = it.tsid;
                if (typeof it.tsval !== 'undefined' && it.tsval !== null) singleEvent.transaction_value = Number(it.tsval);
                if (it.tscur) singleEvent.currency_code = it.tscur;
                if (it.scitems) {
                    try {
                        singleEvent.shopping_cart_items = typeof it.scitems === 'object' ? it.scitems : JSON.parse(it.scitems);
                    } catch(se) {}
                }

                // Customer profile identity update payload (for CIR & personalization)
                if (it.profiledata) {
                    try {
                        singleEvent.profile_data = typeof it.profiledata === 'object' ? it.profiledata : JSON.parse(it.profiledata);
                    } catch(pe) {}
                }

                eventsList.push(singleEvent);
            }

            var payloadObj = {
                "data_source_id": dataSourceId || "11111111-1111-1111-1111-111111111111",
                "session_id": sessionKey || null,
                "user_id": userId || null,
                "events": eventsList
            };

            return JSON.stringify(payloadObj);
        },

        enqueue: function(url, data, batchSize) {
            if (!this.queues[url]) {
                this.queues[url] = [];
            }
            this.queues[url].push(data);

            if (this.queues[url].length >= batchSize) {
                this.flush(url);
            }
        },

        flush: function(url) {
            var self = this;
            var queue = this.queues[url];

            if (!queue || queue.length === 0 || this.isFlushing[url]) {
                return;
            }

            this.isFlushing[url] = true;

            var buffer = queue.slice(0);
            this.queues[url] = [];

            var eventCount = buffer.length;
            var payloadJson = this.createPayload(buffer);
            var forceXHR = !(localSessionKey && localSessionKey.length > 0);

            Network.sendBeaconOrXHR(url, payloadJson, function(success, responseText) {
                self.isFlushing[url] = false;

                if (success) {
                    if (responseText) {
                        trackingCallback(responseText);
                    }
                    if (CONFIG.DEBUG) {
                        log("Batch sent successfully (" + (responseText ? "XHR" : "Beacon") + "): " + eventCount + " events");
                    }
                } else {
                    log("Batch failed. Restoring data to queue.", "error");
                    self.queues[url] = buffer.concat(self.queues[url]);
                }
            }, forceXHR);
        }
    };

    // --- Global Interface ---

    var LeoCorsRequest = {
        // Allow external setting of key if needed
        setSessionKey: function(key) {
			if(key && key !== "") localSessionKey = key;
        },

        get: function(url) {
            Network.get(url, trackingCallback);
        },
        
        post: function(url, params) {
            Network.post(url, params, trackingCallback);
        },

        batchSend: function(url, paramsObj, batchSize) {
            BatchManager.enqueue(url, paramsObj, batchSize || 10);
        }
    };

    // --- Automatic Flush Timer ---
    var flushPendingQueues = function() {
        Object.keys(BatchManager.queues).forEach(function(url) {
            BatchManager.flush(url);
        });
    };

    setInterval(flushPendingQueues, CONFIG.TIME_TO_FLUSH);

    // --- Flush on Page Unload ---
    if (window.addEventListener) {
        var handlePageExit = function() {
            flushPendingQueues();
        };

        window.addEventListener('pagehide', handlePageExit, false);
        window.addEventListener('beforeunload', handlePageExit, false);
    }

    global.LeoCorsRequest = LeoCorsRequest;
    global.BatchManager = BatchManager;
    global.LeoObserverConfig = CONFIG;
    global.hasOwn = hasOwn;
    global.toSafeParamValue = toSafeParamValue;

})(typeof window === 'undefined' ? this : window);

// ------------------------------------------------------------------------------------//


// ------------ BEGIN lscache ------------------
/**
 * lscache library https://github.com/pamelafox/lscache
 * Copyright (c) 2011, Pamela Fox
 */
(function(root, factory) {
    if (typeof define === 'function' && define.amd) {
        // AMD. Register as an anonymous module.
        define([], factory);
    } else if (typeof module !== "undefined" && module.exports) {
        // CommonJS/Node module
        module.exports = factory();
    } else {
        // Browser globals
        root.lscache = factory();
    }
}(this, function() {

    // Prefix for all lscache keys
    var CACHE_PREFIX = 'leocache-';

    // Suffix for the key name on the expiration items in localStorage
    var CACHE_SUFFIX = '-cacheexpiration';

    // expiration date radix (set to Base-36 for most space savings)
    var EXPIRY_RADIX = 10;

    // time resolution in milliseconds
    var expiryMilliseconds = 60 * 1000;
    // ECMAScript max Date (epoch + 1e8 days)
    var maxDate = calculateMaxDate(expiryMilliseconds);

    var cachedStorage;
    var cachedJSON;
    var cacheBucket = '';
    var warnings = false;

    // Determines if localStorage is supported in the browser;
    // result is cached for better performance instead of being run each time.
    // Feature detection is based on how Modernizr does it;
    // it's not straightforward due to FF4 issues.
    // It's not run at parse-time as it takes 200ms in Android.
    function supportsStorage() {
        var key = '__lscachetest__';
        var value = key;

        if (cachedStorage !== undefined) {
            return cachedStorage;
        }

        // some browsers will throw an error if you try to access local storage (e.g. brave browser)
        // hence check is inside a try/catch
        
        try {
        	if (typeof window.localStorage !== "object") {
                return false;
            }
        	
            setItem(key, value);
            removeItem(key);
            cachedStorage = true;
        } catch (e) {
        	console.error(e);
            // If we hit the limit, and we don't have an empty localStorage then it means we have support
            if (isOutOfSpace(e) && localStorage.length) {
                cachedStorage = true; // just maxed it out and even the set test failed.
            } else {
                cachedStorage = false;
            }
        }
        return cachedStorage;
    }

    // Check to set if the error is us dealing with being out of space
    function isOutOfSpace(e) {
        return e && (
            e.name === 'QUOTA_EXCEEDED_ERR' ||
            e.name === 'NS_ERROR_DOM_QUOTA_REACHED' ||
            e.name === 'QuotaExceededError'
        );
    }

    // Determines if native JSON (de-)serialization is supported in the browser.
    function supportsJSON() {
        /*jshint eqnull:true */
        if (cachedJSON === undefined) {
            cachedJSON = (window.JSON != null);
        }
        return cachedJSON;
    }

    /**
     * Returns a string where all RegExp special characters are escaped with a \.
     * @param {String} text
     * @return {string}
     */
    function escapeRegExpSpecialCharacters(text) {
        return text.replace(/[[\]{}()*+?.\\^$|]/g, '\\$&');
    }

    /**
     * Returns the full string for the localStorage expiration item.
     * @param {String} key
     * @return {string}
     */
    function expirationKey(key) {
        return key + CACHE_SUFFIX;
    }

    /**
     * Returns the number of minutes since the epoch.
     * @return {number}
     */
    function currentTime() {
        return Math.floor((new Date().getTime()) / expiryMilliseconds);
    }

    /**
     * Wrapper functions for localStorage methods
     */

    function getItem(key) {
        return localStorage.getItem(CACHE_PREFIX + cacheBucket + key);
    }

    function setItem(key, value) {
        // Fix for iPad issue - sometimes throws QUOTA_EXCEEDED_ERR on setItem.
        localStorage.removeItem(CACHE_PREFIX + cacheBucket + key);
        localStorage.setItem(CACHE_PREFIX + cacheBucket + key, value);
    }

    function removeItem(key) {
        localStorage.removeItem(CACHE_PREFIX + cacheBucket + key);
    }

    function eachKey(fn) {
        var prefixRegExp = new RegExp('^' + CACHE_PREFIX + escapeRegExpSpecialCharacters(cacheBucket) + '(.*)');
        // Loop in reverse as removing items will change indices of tail
        for (var i = localStorage.length - 1; i >= 0; --i) {
            var key = localStorage.key(i);
            key = key && key.match(prefixRegExp);
            key = key && key[1];
            if (key && key.indexOf(CACHE_SUFFIX) < 0) {
                fn(key, expirationKey(key));
            }
        }
    }

    function flushItem(key) {
        var exprKey = expirationKey(key);

        removeItem(key);
        removeItem(exprKey);
    }

    function flushExpiredItem(key) {
        var exprKey = expirationKey(key);
        var expr = getItem(exprKey);

        if (expr) {
            var expirationTime = parseInt(expr, EXPIRY_RADIX);

            // Check if we should actually kick item out of storage
            if (currentTime() >= expirationTime) {
                removeItem(key);
                removeItem(exprKey);
                return true;
            }
        }
    }

    function warn(message, err) {
        if (!warnings) return;
        if (!('console' in window) || typeof window.console.warn !== 'function') return;
        window.console.warn("lscache - " + message);
        if (err) window.console.warn("lscache - The error was: " + err.message);
    }

    function calculateMaxDate(expiryMilliseconds) {
        return Math.floor(8.64e15 / expiryMilliseconds);
    }

    var lscache = {
        /**
         * Stores the value in localStorage. Expires after specified number of minutes.
         * @param {string} key
         * @param {Object|string} value
         * @param {number} time
         * @return {boolean} whether the value was inserted successfully
         */
        set: function(key, value, time) {
            if (!supportsStorage()) return false;

            // If we don't get a string value, try to stringify
            // In future, localStorage may properly support storing non-strings
            // and this can be removed.

            if (!supportsJSON()) return false;
            try {
                value = JSON.stringify(value);
            } catch (e) {
                // Sometimes we can't stringify due to circular refs
                // in complex objects, so we won't bother storing then.
                return false;
            }

            try {
                setItem(key, value);
            } catch (e) {
                if (isOutOfSpace(e)) {
                    // If we exceeded the quota, then we will sort
                    // by the expire time, and then remove the N oldest
                    var storedKeys = [];
                    var storedKey;
                    eachKey(function(key, exprKey) {
                        var expiration = getItem(exprKey);
                        if (expiration) {
                            expiration = parseInt(expiration, EXPIRY_RADIX);
                        } else {
                            // TODO: Store date added for non-expiring items for smarter removal
                            expiration = maxDate;
                        }
                        storedKeys.push({
                            key: key,
                            size: (getItem(key) || '').length,
                            expiration: expiration
                        });
                    });
                    // Sorts the keys with oldest expiration time last
                    storedKeys.sort(function(a, b) {
                        return (b.expiration - a.expiration);
                    });

                    var targetSize = (value || '').length;
                    while (storedKeys.length && targetSize > 0) {
                        storedKey = storedKeys.pop();
                        warn("Cache is full, removing item with key '" + key + "'");
                        flushItem(storedKey.key);
                        targetSize -= storedKey.size;
                    }
                    try {
                        setItem(key, value);
                    } catch (e) {
                        // value may be larger than total quota
                        warn("Could not add item with key '" + key + "', perhaps it's too big?", e);
                        return false;
                    }
                } else {
                    // If it was some other error, just give up.
                    warn("Could not add item with key '" + key + "'", e);
                    return false;
                }
            }

            // If a time is specified, store expiration info in localStorage
            if (time) {
                setItem(expirationKey(key), (currentTime() + time).toString(EXPIRY_RADIX));
            } else {
                // In case they previously set a time, remove that info from localStorage.
                removeItem(expirationKey(key));
            }
            return true;
        },

        /**
         * Retrieves specified value from localStorage, if not expired.
         * @param {string} key
         * @return {string|Object}
         */
        get: function(key) {
            if (!supportsStorage()) return null;

            // Return the de-serialized item if not expired
            if (flushExpiredItem(key)) {
                return null;
            }

            // Tries to de-serialize stored value if its an object, and returns the normal value otherwise.
            var value = getItem(key);
            if (!value || !supportsJSON()) {
                return value;
            }

            try {
                // We can't tell if its JSON or a string, so we try to parse
                return JSON.parse(value);
            } catch (e) {
                // If we can't parse, it's probably because it isn't an object
                return value;
            }
        },

        /**
         * Removes a value from localStorage.
         * Equivalent to 'delete' in memcache, but that's a keyword in JS.
         * @param {string} key
         */
        remove: function(key) {
            if (!supportsStorage()) return;

            flushItem(key);
        },

        /**
         * Returns whether local storage is supported.
         * Currently exposed for testing purposes.
         * @return {boolean}
         */
        supported: function() {
            return supportsStorage();
        },

        /**
         * Flushes all lscache items and expiry markers without affecting rest of localStorage
         */
        flush: function() {
            if (!supportsStorage()) return;

            eachKey(function(key) {
                flushItem(key);
            });
        },

        /**
         * Flushes expired lscache items and expiry markers without affecting rest of localStorage
         */
        flushExpired: function() {
            if (!supportsStorage()) return;

            eachKey(function(key) {
                flushExpiredItem(key);
            });
        },

        /**
         * Appends CACHE_PREFIX so lscache will partition data in to different buckets.
         * @param {string} bucket
         */
        setBucket: function(bucket) {
            cacheBucket = bucket;
        },

        /**
         * Resets the string being appended to CACHE_PREFIX so lscache will use the default storage behavior.
         */
        resetBucket: function() {
            cacheBucket = '';
        },

        /**
         * @returns {number} The currently set number of milliseconds each time unit represents in
         *   the set() function's "time" argument.
         */
        getExpiryMilliseconds: function() {
            return expiryMilliseconds;
        },

        /**
         * Sets the number of milliseconds each time unit represents in the set() function's
         *   "time" argument.
         * Sample values:
         *  1: each time unit = 1 millisecond
         *  1000: each time unit = 1 second
         *  60000: each time unit = 1 minute (Default value)
         *  360000: each time unit = 1 hour
         * @param {number} milliseconds
         */
        setExpiryMilliseconds: function(milliseconds) {
            expiryMilliseconds = milliseconds;
            maxDate = calculateMaxDate(expiryMilliseconds);
        },

        /**
         * Sets whether to display warnings when an item is removed from the cache or not.
         */
        enableWarnings: function(enabled) {
            warnings = enabled;
        }
    };

    // Return the module
    return lscache;
}));

// ------------ END LEO Cache ------------------

// ------------ BEGIN LEO Event Observer -------

var leoSessionStringKey = "leoctxsk";
var leoVisitorIdStringKey = "leocdp_vid";

(function(global, undefined) {
    'use strict';

    var LeoEventObserver = {'fingerprintId' : ""};
    var sessionKey = false;
    var debug = false;

    function hasOwn(obj, key) {
        return Object.prototype.hasOwnProperty.call(obj, key);
    }

    function toSafeParamValue(value) {
        if (value === null || typeof value === 'undefined') {
            return '';
        }
        if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
            return value;
        }
        if (typeof value === 'object') {
            try {
                return JSON.stringify(value);
            } catch (e) {
                return String(value);
            }
        }
        return String(value);
    }

    function sendMessage(message) {
        if (typeof global.__leoObserverProxySendMessage === 'function') {
            global.__leoObserverProxySendMessage(message);
        } else if (typeof window !== 'undefined' && typeof window.__leoObserverProxySendMessage === 'function') {
            window.__leoObserverProxySendMessage(message);
        } else if (typeof global.sendMessage === 'function' && global.sendMessage !== sendMessage) {
            global.sendMessage(message);
        } else if (typeof window !== 'undefined' && typeof window.sendMessage === 'function' && window.sendMessage !== sendMessage) {
            window.sendMessage(message);
        } else if (typeof window !== 'undefined' && window.parent && window.parent !== window) {
            try {
                window.parent.postMessage(message, '*');
            } catch (e) {
                if (window.console && window.console.error) {
                    window.console.error('[LeoObserver] postMessage failed:', e);
                }
            }
        }
    }

    var BatchManager = global.BatchManager || {
        enqueue: function(url, data, batchSize) {
            if (global.LeoCorsRequest && typeof global.LeoCorsRequest.batchSend === 'function') {
                global.LeoCorsRequest.batchSend(url, data, batchSize);
            }
        }
    };

    var CONFIG = global.LeoObserverConfig || { DEBUG: false };
    function log(msg, type) {
        if (!global.console) return;
        var prefix = "[LeoCDP] ";
        if (type === 'error') {
            global.console.error(prefix + msg);
        } else if (CONFIG.DEBUG || debug) {
            global.console.log(prefix + msg);
        }
    }

    function debugLog(data){
    	if(debug && window.console){
			window.console.log(data);
		}
    }
    
    function setSessionKey(key){
    	sessionKey = key;
    	lscache.set(leoSessionStringKey, sessionKey);
        if (global.LeoCorsRequest && typeof global.LeoCorsRequest.setSessionKey === 'function') {
            global.LeoCorsRequest.setSessionKey(sessionKey);
        }
    }
    
    function getSessionKey(autoResfresh){
    	sessionKey = lscache.get(leoSessionStringKey);
		if(typeof sessionKey !== 'string' && autoResfresh === true){
			sessionKey = "";
		}
    	return sessionKey;
    }
    
    function clearSessionKey(){
    	lscache.remove(leoSessionStringKey);
    }
    
    function initFingerprint(callback){
    	if (typeof Fingerprint2 === 'undefined' || typeof Fingerprint2.get !== 'function') {
    		if (typeof callback === 'function') {
    			callback('');
    		}
    		return;
    	}

    	var options = { excludes: { enumerateDevices : true, deviceMemory : true}};
    	Fingerprint2.get(options, function (components) {
    	    if (!components || !components.length) {
    	        if (typeof callback === 'function') {
    	            callback('');
    	        }
    	        return;
    	    }
    
    	    var values = components.map(function (component) {
    	        return component && component.value;
    	    }).filter(function (value) {
    	        return typeof value !== 'undefined' && value !== null;
    	    });
    	    var fingerprintId = Fingerprint2.x64hash128(values.join(''), 31);
  
    		lscache.set("leocdp_fgp", fingerprintId);

    		if (typeof callback === 'function') {
    			callback(fingerprintId);
    		}
    	});
    }
    

    function generateVisitorId() {
        var injectedVid = (typeof global.INJECTED_VISITOR_ID === 'string' && global.INJECTED_VISITOR_ID)
            || (typeof INJECTED_VISITOR_ID === 'string' && INJECTED_VISITOR_ID)
            || (global.LeoEventObserver && global.LeoEventObserver.visitorId)
            || (typeof window !== 'undefined' && typeof window.injectedVisitorId === 'string' && window.injectedVisitorId);
    	if(typeof injectedVid === 'string' && injectedVid.length > 5) {
    		return injectedVid;
    	} else {
            var cryptoObj = (global.crypto || global.msCrypto);
            if (cryptoObj && typeof cryptoObj.getRandomValues === 'function') {
                var bytes = new Uint8Array(16);
                cryptoObj.getRandomValues(bytes);

                // RFC 4122 version 4 + variant bits
                bytes[6] = (bytes[6] & 0x0f) | 0x40;
                bytes[8] = (bytes[8] & 0x3f) | 0x80;

                var hex = [];
                for (var i = 0; i < bytes.length; i++) {
                    hex.push((bytes[i] + 0x100).toString(16).substr(1));
                }
                return hex.join('');
            }

            // Fallback for very old environments without crypto support
            var d = new Date().getTime();
            return 'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                var r = (d % 16) | 0;
                d = Math.floor(d / 16);
                return (c == 'x' ? r : (r & 0x3 | 0x8)).toString(16);
            });
    	}
    }
    
    function getVisitorId() {
        var key = leoVisitorIdStringKey;
        var uuid =  lscache.get(key); 
        
        var injectedVid = (typeof global.INJECTED_VISITOR_ID === 'string' && global.INJECTED_VISITOR_ID)
            || (typeof INJECTED_VISITOR_ID === 'string' && INJECTED_VISITOR_ID)
            || (global.LeoEventObserver && global.LeoEventObserver.visitorId)
            || (typeof window !== 'undefined' && typeof window.injectedVisitorId === 'string' && window.injectedVisitorId);

        if(typeof injectedVid === 'string' && injectedVid.length > 5 && typeof uuid === 'string') {
        	if(uuid !== injectedVid) {
        		uuid = injectedVid;
        		lscache.set(key, uuid);
        	}
        }
        
        if (typeof uuid !== 'string') {
        	uuid = generateVisitorId();
            lscache.set(key, uuid);
        } 

        return uuid;
    }

    var doTracking = function(eventType, params) {
        if (!params || typeof params !== 'object') {
            params = {};
        }

        var activeSessionKey = getSessionKey(true);
        if (global.LeoCorsRequest && typeof global.LeoCorsRequest.setSessionKey === 'function') {
            global.LeoCorsRequest.setSessionKey(activeSessionKey);
        }

        var payload = {};
        for (var key in params) {
            if (!hasOwn(params, key)) {
                continue;
            }
            if (key === 'batchsize' || key === 'screen' || typeof params[key] === 'undefined' || params[key] === null || typeof params[key] === 'function') {
                continue;
            }
            payload[key] = params[key];
        }

        var batchSize = parseInt(params.batchsize, 10);
        if (!isFinite(batchSize) || batchSize < 1) {
            batchSize = 1;
        }

        payload.visid = getVisitorId();
        payload.sessionKey = activeSessionKey;
        payload.eventType = eventType;
        if (params.screen) payload.screen = params.screen;

        var targetUrl = (typeof global.PREFIX_EVENT_TRACKING_URL === 'string' && global.PREFIX_EVENT_TRACKING_URL)
            ? global.PREFIX_EVENT_TRACKING_URL
            : (typeof PREFIX_EVENT_TRACKING_URL === 'string' && PREFIX_EVENT_TRACKING_URL
                ? PREFIX_EVENT_TRACKING_URL
                : ((typeof PREFIX_EVENT_VIEW_URL === 'string' && PREFIX_EVENT_VIEW_URL) ? PREFIX_EVENT_VIEW_URL : '/data/api/v1/tracking/logs'));

        if (eventType === "action" && typeof PREFIX_EVENT_ACTION_URL === 'string' && PREFIX_EVENT_ACTION_URL) {
            targetUrl = PREFIX_EVENT_ACTION_URL;
        } else if (eventType === "conversion" && typeof PREFIX_EVENT_CONVERSION_URL === 'string' && PREFIX_EVENT_CONVERSION_URL) {
            targetUrl = PREFIX_EVENT_CONVERSION_URL;
        } else if (eventType === "feedback" && typeof PREFIX_EVENT_FEEDBACK_URL === 'string' && PREFIX_EVENT_FEEDBACK_URL) {
            targetUrl = PREFIX_EVENT_FEEDBACK_URL;
        }

        var batchMgr = global.BatchManager || BatchManager;
        if (batchMgr && typeof batchMgr.enqueue === 'function') {
            batchMgr.enqueue(targetUrl, payload, batchSize);
        }
        if (CONFIG.DEBUG || debug) {
            log("LeoEventObserver queued " + eventType + " event for: " + targetUrl, "debug");
        }
    };

    var updateProfile = function(params) {
        if (!params || typeof params !== 'object') {
            params = {};
        }

        var activeSessionKey = getSessionKey(true);
        if (global.LeoCorsRequest && typeof global.LeoCorsRequest.setSessionKey === 'function') {
            global.LeoCorsRequest.setSessionKey(activeSessionKey);
        }

        var payload = {};
        for (var key in params) {
            if (!hasOwn(params, key)) {
                continue;
            }
            if (typeof params[key] === 'undefined' || params[key] === null || typeof params[key] === 'function') {
                continue;
            }
            payload[key] = params[key];
        }

        payload.visid = getVisitorId();
        payload.sessionKey = activeSessionKey;
        payload.metric = "profile-update";
        payload.eventType = "action";

        // Cache profile identity locally for personalization
        if (payload.profiledata) {
            try {
                var pData = typeof payload.profiledata === 'object' ? payload.profiledata : JSON.parse(payload.profiledata);
                lscache.set("leocdp_profile", pData, 1440); // 24-hour cache
            } catch(e) {}
        }

        var targetUrl = (typeof global.PREFIX_UPDATE_PROFILE_URL === 'string' && global.PREFIX_UPDATE_PROFILE_URL)
            ? global.PREFIX_UPDATE_PROFILE_URL
            : (typeof PREFIX_UPDATE_PROFILE_URL === 'string' && PREFIX_UPDATE_PROFILE_URL
                ? PREFIX_UPDATE_PROFILE_URL
                : ((typeof PREFIX_EVENT_TRACKING_URL === 'string' && PREFIX_EVENT_TRACKING_URL) ? PREFIX_EVENT_TRACKING_URL : '/data/api/v1/tracking/logs'));

        // Immediate flush for profile updates so identity resolution happens promptly
        var batchMgr = global.BatchManager || BatchManager;
        if (batchMgr && typeof batchMgr.enqueue === 'function') {
            batchMgr.enqueue(targetUrl, payload, 1);
        }
    };

    var getPersonalization = function(slotId, params, callback) {
        var profile = lscache.get("leocdp_profile") || {};
        var visitorId = getVisitorId();
        var sessionKey = getSessionKey(true);
        var personalizationContext = {
            slotId: slotId || 'default',
            visitorId: visitorId,
            sessionKey: sessionKey,
            profile: profile,
            recommendedItems: []
        };
        if (typeof callback === 'function') {
            callback(personalizationContext);
        }
    };

    var objectToQueryString = function(params) {
        if (!params || typeof params !== 'object') {
            return '';
        }

        var normalized = {};
        for (var key in params) {
            if (!hasOwn(params, key)) {
                continue;
            }
            var value = params[key];
            if (typeof value === 'undefined' || value === null || typeof value === 'function') {
                continue;
            }
            normalized[key] = toSafeParamValue(value);
        }

        var observeWithFingerprint = (typeof global.OBSERVE_WITH_FINGERPRINT !== 'undefined')
            ? global.OBSERVE_WITH_FINGERPRINT
            : ((typeof OBSERVE_WITH_FINGERPRINT !== 'undefined') ? OBSERVE_WITH_FINGERPRINT : true);

        if (observeWithFingerprint) {
            var fingerprint = lscache.get("leocdp_fgp") || LeoEventObserver.fingerprintId || "";
            if (fingerprint) {
                normalized.fgp = fingerprint;
            }
        }

        var pairs = [];
        for (var field in normalized) {
            if (!hasOwn(normalized, field)) {
                continue;
            }
            pairs.push(encodeURIComponent(field) + '=' + encodeURIComponent(normalized[field]));
        }
        return pairs.join('&');
    };
    
    function leoObserverProxyReady(data) {
    	if (data && data.sessionKey) {
    		setSessionKey(data.sessionKey);
    	}
    	
    	var vid = getVisitorId();
    	var newVisitorId = data && data.visitorId;
    	if(typeof newVisitorId === "string" && newVisitorId.length > 5 && newVisitorId !== vid){
    		lscache.set(leoVisitorIdStringKey, newVisitorId);
    	}
    	
    	var contextPayload = {
    		event: "LeoObserverProxyReady",
    		sessionKey: getSessionKey(true),
    		visitorId: getVisitorId(),
    		fingerprintId: lscache.get("leocdp_fgp") || LeoEventObserver.fingerprintId || ""
    	};

		sendMessage(contextPayload);
		sendMessage("LeoObserverProxyReady");
        debugLog(data);
    }

    var getContextSession = function(params) {
        if (!params || typeof params !== 'object') {
            params = {};
        }

        // Initialize and resolve active session context immediately
        var sessionData = {
            sessionKey: getSessionKey(true),
            visitorId: getVisitorId(),
            fingerprintId: lscache.get("leocdp_fgp") || LeoEventObserver.fingerprintId || "",
            status: 101,
            ready: true
        };

        leoObserverProxyReady(sessionData);
    };

    // --- Expose Public API ---
    LeoEventObserver.doTracking = doTracking;
    LeoEventObserver.getContextSession = getContextSession;
    LeoEventObserver.updateProfile = updateProfile;
    LeoEventObserver.getPersonalization = getPersonalization;
    LeoEventObserver.initFingerprint = initFingerprint;
    LeoEventObserver.getVisitorId = getVisitorId;
    LeoEventObserver.getSessionKey = getSessionKey;
	LeoEventObserver.setSessionKey = setSessionKey;

    global.LeoEventObserver = LeoEventObserver;

})(typeof window === 'undefined' ? this : window);