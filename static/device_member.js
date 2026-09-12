/* Which device this browser is, and which family member is using it.
 *
 * Rally has no logins. It is one household on one network, and the kitchen
 * display nobody is signed in to is the normal case rather than an edge one.
 * Per-person behavioral settings still need two answers — *which device is
 * this* and *whose is it* — and both are things only this browser knows, so
 * both live here.
 *
 * The device id is a token this browser mints once and keeps. It is not an
 * account and it identifies nothing about the person: it exists so that the
 * answers somebody gave on the kitchen tablet stay on the kitchen tablet
 * rather than following them to their phone. The preferences themselves are
 * server-side, keyed on that token, so Settings can configure a device from
 * the device and the family can see and forget devices from any of them.
 *
 * Storage can throw — Safari in private browsing, a locked-down kiosk profile —
 * so every access is guarded. A browser that cannot remember its own id gets a
 * fresh one per page load, which means it simply never accumulates settings and
 * always behaves the way Rally does by default. A screen that will not remember
 * anything must still show the calendar.
 */
(function () {
    'use strict';

    var DEVICE_KEY = 'rally.device-id';
    var MEMBER_KEY = 'rally.device-member';

    /* Held for the life of the page so that a browser with unusable storage
       still gives one stable answer per load, rather than a different device
       to every caller on the same screen. */
    var cachedDeviceId = null;

    function read(key) {
        try {
            return window.localStorage.getItem(key);
        } catch (error) {
            return null;
        }
    }

    function write(key, value) {
        try {
            if (value === null) {
                window.localStorage.removeItem(key);
            } else {
                window.localStorage.setItem(key, String(value));
            }
            return true;
        } catch (error) {
            return false;
        }
    }

    /* `crypto.randomUUID` where it exists, and a random string where it does
       not. Nothing depends on this being unguessable — it is a name for a
       browser inside one house, not a credential — only on two devices not
       colliding. */
    function mint() {
        try {
            if (window.crypto && typeof window.crypto.randomUUID === 'function') {
                return window.crypto.randomUUID();
            }
        } catch (error) {
            /* fall through to the arithmetic below */
        }
        return 'dev-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
    }

    /* This browser's device token, minted and stored on first use. Always
       returns something: a browser that cannot store one still gets an id for
       this page, and simply looks like a brand new device every time. */
    function deviceId() {
        if (cachedDeviceId) return cachedDeviceId;
        var stored = read(DEVICE_KEY);
        if (stored) {
            cachedDeviceId = stored;
            return cachedDeviceId;
        }
        cachedDeviceId = mint();
        write(DEVICE_KEY, cachedDeviceId);
        return cachedDeviceId;
    }

    /* A coarse guess at what this device is, used only as the starting name in
       the device list and always editable. Getting it wrong costs a rename;
       leaving every row called "Unnamed device" costs the family the ability to
       tell which one is the kitchen tablet. */
    function guessLabel() {
        var ua = '';
        try {
            ua = window.navigator.userAgent || '';
        } catch (error) {
            return 'Unnamed device';
        }
        if (/iPad/i.test(ua)) return 'iPad';
        if (/iPhone|iPod/i.test(ua)) return 'iPhone';
        if (/Android/i.test(ua)) return /Mobile/i.test(ua) ? 'Android phone' : 'Android tablet';
        if (/Macintosh|Mac OS X/i.test(ua)) return 'Mac';
        if (/Windows/i.test(ua)) return 'Windows PC';
        if (/CrOS/i.test(ua)) return 'Chromebook';
        if (/Linux/i.test(ua)) return 'Linux PC';
        return 'Unnamed device';
    }

    /* Returns the stored family member id, or null. Parsed rather than handed
       back as a string: every caller compares it against `member.id`, which is
       a number, and `'3' === 3` is false. */
    function memberId() {
        var raw = read(MEMBER_KEY);
        if (!raw) return null;
        var id = parseInt(raw, 10);
        return Number.isFinite(id) ? id : null;
    }

    /* `null` clears the binding, which is how a device goes back to Rally's
       defaults — a phone that was handed on, or a tablet that became the
       kitchen display. */
    function setMemberId(id) {
        return write(MEMBER_KEY, id === null || id === undefined || id === '' ? null : id);
    }

    /* The member record this device belongs to, given the list the page has
       already fetched. Null when nobody claimed it — and also when the stored
       id no longer matches anybody, which is what happens after a member is
       deleted. Falling back to the defaults there is the honest reading: the
       person whose preference this was is gone. */
    function member(members) {
        var id = memberId();
        if (id === null || !members) return null;
        for (var i = 0; i < members.length; i++) {
            if (members[i].id === id) return members[i];
        }
        return null;
    }

    /* Tell the server this device exists and when it was last seen, and name it
       if it has no name yet. Fire-and-forget: a page must not wait on the
       device registry to render, and a failure here costs a row in a list, not
       a calendar. */
    function announce(label) {
        var body = label === undefined ? { label: guessLabel() } : { label: label };
        return fetch('/api/devices/' + encodeURIComponent(deviceId()), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).catch(function () { return null; });
    }

    /* This device's answers for the member using it, resolved by the server
       with the defaults filled in. Returns the settings map, or an empty one
       when nobody has claimed the device or the request fails — both of which
       mean "behave the way Rally does by default". */
    function preferences() {
        var mine = memberId();
        if (mine === null) return Promise.resolve({});
        return fetch('/api/devices/' + encodeURIComponent(deviceId()) + '/preferences')
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (body) {
                if (!body || !body.members) return {};
                return body.members[String(mine)] || {};
            })
            .catch(function () { return {}; });
    }

    window.RallyDevice = {
        deviceId: deviceId,
        guessLabel: guessLabel,
        memberId: memberId,
        setMemberId: setMemberId,
        member: member,
        announce: announce,
        preferences: preferences
    };
})();
