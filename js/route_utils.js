// js/route_utils.js - Shared route & coordinate calculation helpers
(function (global) {
    function formatSecToHHMMSS(sec) {
        const s = Math.max(0, Math.round(sec));
        const hh = String(Math.floor(s / 3600)).padStart(2, '0');
        const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
        const ss = String(s % 60).padStart(2, '0');
        return `${hh}:${mm}:${ss}`;
    }

    function parseHHMMSSToSec(str) {
        if (!str) return 0;
        const parts = str.toString().trim().split(':').map(Number);
        if (parts.length === 3 && !isNaN(parts[0]) && !isNaN(parts[1]) && !isNaN(parts[2])) {
            return parts[0] * 3600 + parts[1] * 60 + parts[2];
        }
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
            return parts[0] * 60 + parts[1];
        }
        if (parts.length === 1 && !isNaN(parts[0])) {
            return parts[0];
        }
        return 0;
    }

    function createNumberedIcon(num, isStart, isEnd) {
        const bg = isStart ? '#10b981' : (isEnd ? '#ef4444' : '#0284c7');
        const svg = `
            <svg width="28" height="36" viewBox="0 0 28 36" xmlns="http://www.w3.org/2000/svg">
                <path d="M14 0C6.27 0 0 6.27 0 14c0 10.5 14 22 14 22s14-11.5 14-22c0-7.73-6.27-14-14-14z" fill="${bg}" stroke="#ffffff" stroke-width="1.5"/>
                <circle cx="14" cy="14" r="10" fill="#0f172a" />
                <text x="14" y="18" fill="#38bdf8" font-size="11" font-weight="bold" text-anchor="middle" font-family="-apple-system, sans-serif">${num}</text>
            </svg>
        `;
        return (typeof L !== 'undefined' && L.divIcon) ? L.divIcon({
            html: svg,
            className: 'custom-map-pin',
            iconSize: [28, 36],
            iconAnchor: [14, 36],
            popupAnchor: [0, -36]
        }) : null;
    }

    function calculateBearing(lat1, lon1, lat2, lon2) {
        const toRad = (d) => d * Math.PI / 180;
        const toDeg = (r) => r * 180 / Math.PI;
        const phi1 = toRad(lat1);
        const phi2 = toRad(lat2);
        const dLambda = toRad(lon2 - lon1);

        const y = Math.sin(dLambda) * Math.cos(phi2);
        const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLambda);
        let brng = toDeg(Math.atan2(y, x));
        return (brng + 360) % 360;
    }

    function getDestinationPoint(lat, lon, brngDeg, distMeters) {
        if (distMeters === undefined) distMeters = 35;
        const R = 6371e3;
        const toRad = (d) => d * Math.PI / 180;
        const toDeg = (r) => r * 180 / Math.PI;
        const phi1 = toRad(lat);
        const lambda1 = toRad(lon);
        const brng = toRad(brngDeg);
        const delta = distMeters / R;

        const phi2 = Math.asin(Math.sin(phi1) * Math.cos(delta) + Math.cos(phi1) * Math.sin(delta) * Math.cos(brng));
        const lambda2 = lambda1 + Math.atan2(Math.sin(brng) * Math.sin(delta) * Math.cos(phi1), Math.cos(delta) - Math.sin(phi1) * Math.sin(phi2));

        return [toDeg(phi2), toDeg(lambda2)];
    }

    function updateDirectionPresetButtons(brng) {
        const norm = (Math.round(brng) % 360 + 360) % 360;
        document.querySelectorAll('.btn-heading-dir').forEach(btn => {
            const val = parseInt(btn.getAttribute('data-heading') || '0', 10);
            const diff = Math.min(Math.abs(norm - val), 360 - Math.abs(norm - val));
            if (diff <= 25) {
                btn.style.background = 'rgba(56, 189, 248, 0.35)';
                btn.style.borderColor = 'rgba(56, 189, 248, 0.8)';
                btn.style.fontWeight = 'bold';
            } else {
                btn.style.background = 'rgba(255,255,255,0.08)';
                btn.style.borderColor = 'rgba(255,255,255,0.15)';
                btn.style.fontWeight = 'normal';
            }
        });
    }

    function parsePastedCoordinateString(str) {
        if (!str || !str.trim()) return null;
        const raw = str.trim().replace(/^#.*$/, '').trim();
        if (!raw) return null;

        let timeSec = null;
        let timeStr = '';
        const timeMatch = raw.match(/\b(\d{1,2}:\d{2}(?::\d{2})?)\b/);
        if (timeMatch) {
            timeStr = timeMatch[1];
            timeSec = parseHHMMSSToSec(timeStr);
        }

        const withoutTime = timeStr ? raw.replace(timeStr, '') : raw;
        const tokens = withoutTime.match(/[-+]?[0-9]*\.?[0-9]+/g);
        if (!tokens || tokens.length < 2) return null;

        const numbers = tokens.map(Number).filter(n => !isNaN(n));
        if (numbers.length < 2) return null;

        let lat = null;
        let lon = null;
        let ele = 315;

        for (let i = 0; i < numbers.length - 1; i++) {
            if (Math.abs(numbers[i]) <= 90 && Math.abs(numbers[i+1]) <= 180 && (Math.abs(numbers[i]) > 0 || Math.abs(numbers[i+1]) > 0)) {
                lat = numbers[i];
                lon = numbers[i+1];
                if (numbers.length > i + 2 && numbers[i+2] > 0 && numbers[i+2] < 9000 && numbers[i+2] !== lat && numbers[i+2] !== lon) {
                    ele = numbers[i+2];
                }
                break;
            }
        }

        if (lat === null || lon === null) return null;

        return {
            timeStr: timeStr ? formatSecToHHMMSS(timeSec) : (timeSec !== null ? formatSecToHHMMSS(timeSec) : ''),
            sec: timeSec !== null ? timeSec : 0,
            hasCustomTime: (timeSec !== null),
            lat: lat,
            lon: lon,
            ele: ele
        };
    }

    // Attach to global window object
    global.formatSecToHHMMSS = formatSecToHHMMSS;
    global.parseHHMMSSToSec = parseHHMMSSToSec;
    global.createNumberedIcon = createNumberedIcon;
    global.calculateBearing = calculateBearing;
    global.getDestinationPoint = getDestinationPoint;
    global.updateDirectionPresetButtons = updateDirectionPresetButtons;
    global.parsePastedCoordinateString = parsePastedCoordinateString;
})(typeof window !== 'undefined' ? window : this);
