/**
 * Phase U1: выставляет --color-brand-* на document.documentElement из одного hex (акцент бренда).
 * Подключается только со страницы storybook; основная админка не зависит от этого файла.
 */
(function () {
    'use strict';

    function clamp(n, a, b) {
        return Math.min(b, Math.max(a, n));
    }

    function hexToRgb(hex) {
        var s = String(hex || '').replace('#', '').trim();
        if (s.length === 3) {
            return {
                r: parseInt(s[0] + s[0], 16),
                g: parseInt(s[1] + s[1], 16),
                b: parseInt(s[2] + s[2], 16),
            };
        }
        if (s.length !== 6 || !/^[0-9a-fA-F]+$/.test(s)) {
            return { r: 37, g: 99, b: 235 };
        }
        return {
            r: parseInt(s.slice(0, 2), 16),
            g: parseInt(s.slice(2, 4), 16),
            b: parseInt(s.slice(4, 6), 16),
        };
    }

    function rgbToHsl(r, g, b) {
        r /= 255;
        g /= 255;
        b /= 255;
        var max = Math.max(r, g, b);
        var min = Math.min(r, g, b);
        var h = 0;
        var s = 0;
        var l = (max + min) / 2;
        if (max !== min) {
            var d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            switch (max) {
                case r:
                    h = (g - b) / d + (g < b ? 6 : 0);
                    break;
                case g:
                    h = (b - r) / d + 2;
                    break;
                default:
                    h = (r - g) / d + 4;
            }
            h /= 6;
        }
        return { h: h * 360, s: s * 100, l: l * 100 };
    }

    function hslToRgb(h, s, l) {
        h /= 360;
        s /= 100;
        l /= 100;
        var r;
        var g;
        var b;
        if (s === 0) {
            r = g = b = l;
        } else {
            var hue2rgb = function hue2rgb(p, q, t) {
                if (t < 0) t += 1;
                if (t > 1) t -= 1;
                if (t < 1 / 6) return p + (q - p) * 6 * t;
                if (t < 1 / 2) return q;
                if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
                return p;
            };
            var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
            var p = 2 * l - q;
            r = hue2rgb(p, q, h + 1 / 3);
            g = hue2rgb(p, q, h);
            b = hue2rgb(p, q, h - 1 / 3);
        }
        return {
            r: Math.round(r * 255),
            g: Math.round(g * 255),
            b: Math.round(b * 255),
        };
    }

    function rgbToHex(r, g, b) {
        function x(n) {
            var h = clamp(Math.round(n), 0, 255).toString(16);
            return h.length === 1 ? '0' + h : h;
        }
        return '#' + x(r) + x(g) + x(b);
    }

    /**
     * @param {string} hex — например #2563eb
     */
    function applyBrandPalette(hex) {
        var rgb = hexToRgb(hex);
        var hsl = rgbToHsl(rgb.r, rgb.g, rgb.b);
        var root = document.documentElement;
        var pairs = [
            [50, 96, 0.12],
            [100, 90, 0.22],
            [200, 82, 0.35],
            [400, 68, 0.55],
            [500, 58, 0.72],
            [600, 48, 0.85],
            [700, 40, 0.88],
            [800, 32, 0.9],
            [900, 24, 0.92],
        ];
        for (var i = 0; i < pairs.length; i++) {
            var name = pairs[i][0];
            var L = pairs[i][1];
            var satMul = pairs[i][2];
            var out = hslToRgb(hsl.h, clamp(hsl.s * satMul, 8, 100), L);
            root.style.setProperty('--color-brand-' + name, rgbToHex(out.r, out.g, out.b));
        }
    }

    window.restoMindApplyBrandTokens = applyBrandPalette;
    window.restoMindDefaultBrandHex = '#2563eb';
})();
