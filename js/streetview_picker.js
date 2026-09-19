/**
 * Interactive Street View Checkpoint Map Picker (Mode A).
 * Modular Leaflet map picker for adding, editing, and syncing GPS route checkpoints.
 */

// ── Interactive Street View Checkpoint Map Picker (Mode A) ─────────────────
let cpPickerMap = null;
let cpPickerMarkers = [];
let cpPickerPolyline = null;
let cpPickerPoints = []; // Array of { timeStr: string, sec: number, lat: number, lon: number, ele: number }

function initCheckpointPickerModal() {
    const modal = document.getElementById('modal-checkpoint-picker');
    const btnOpen = document.getElementById('btn-open-checkpoint-picker');
    const btnCloseX = document.getElementById('btn-close-cp-picker-x');
    const btnCancel = document.getElementById('btn-cancel-cp-picker');
    const btnApply = document.getElementById('btn-apply-cp-picker');
    const btnUndo = document.getElementById('btn-cp-picker-undo');
    const btnClear = document.getElementById('btn-cp-picker-clear');
    const btnCenter = document.getElementById('btn-cp-picker-center');
    const cpText = document.getElementById('streetview_checkpoints');
    const btnOpenTab = document.getElementById('btn-open-route-editor-tab');
    const btnOpenStandalone = document.getElementById('btn-open-route-editor-standalone');

    function openStandaloneRouteEditor() {
        const inputSelect = document.getElementById('input_name');
        const selectedFile = inputSelect ? inputSelect.value : '';
        const totalDur = getVideoDurationForPicker();
        const currentText = document.getElementById('streetview_checkpoints')?.value?.trim() || '';
        localStorage.setItem('streetview_checkpoints_transfer', currentText);
        
        let url = 'route_editor.html';
        const params = [];
        if (selectedFile) params.push('video=' + encodeURIComponent(selectedFile));
        if (totalDur) params.push('duration=' + encodeURIComponent(totalDur));
        if (params.length > 0) url += '?' + params.join('&');
        
        window.open(url, '_blank');
    }

    window.openStandaloneRouteEditor = openStandaloneRouteEditor;

    function openModal() {
        if (modal) {
            modal.style.display = 'flex';
            initOrUpdatePickerMap();
        }
    }

    function closeModal() {
        if (modal) {
            modal.style.display = 'none';
        }
    }

    window.openCheckpointPickerModal = openModal;
    window.closeCheckpointPickerModal = closeModal;

    if (btnOpen) btnOpen.addEventListener('click', openModal);
    if (btnCloseX) btnCloseX.addEventListener('click', closeModal);
    if (btnCancel) btnCancel.addEventListener('click', closeModal);
    if (btnOpenTab) btnOpenTab.addEventListener('click', openStandaloneRouteEditor);
    if (btnOpenStandalone) btnOpenStandalone.addEventListener('click', openStandaloneRouteEditor);

    window.addEventListener('message', (e) => {
        if (e.data && e.data.type === 'CHECKPOINTS_UPDATED' && e.data.text) {
            const cpText = document.getElementById('streetview_checkpoints');
            if (cpText) cpText.value = e.data.text;
            if (typeof parseTextToCheckpoints === 'function') parseTextToCheckpoints(e.data.text);
        }
    });

    window.addEventListener('storage', (e) => {
        if (e.key === 'streetview_checkpoints_result' && e.newValue) {
            const cpText = document.getElementById('streetview_checkpoints');
            if (cpText) cpText.value = e.newValue;
            if (typeof parseTextToCheckpoints === 'function') parseTextToCheckpoints(e.newValue);
        }
    });

    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });

    if (btnUndo) {
        btnUndo.addEventListener('click', () => {
            if (cpPickerPoints.length > 0) {
                cpPickerPoints.pop();
                recalcTimestampsAndRender();
            }
        });
    }

    if (btnClear) {
        btnClear.addEventListener('click', () => {
            if (confirm('Clear all checkpoints from the map?')) {
                cpPickerPoints = [];
                recalcTimestampsAndRender();
            }
        });
    }

    if (btnCenter) {
        btnCenter.addEventListener('click', () => {
            fitMapToPath();
        });
    }

    const inpQuickPaste = document.getElementById('cp-picker-quick-paste');
    const btnQuickAdd = document.getElementById('btn-cp-picker-quick-add');

    function handleQuickPasteAdd() {
        if (!inpQuickPaste) return;
        const val = inpQuickPaste.value.trim();
        if (!val) return;

        if (val.includes('\n')) {
            parseTextToCheckpoints(val);
            inpQuickPaste.value = '';
            return;
        }

        const pt = parsePastedCoordinateString(val);
        if (pt) {
            cpPickerPoints.push(pt);
            recalcTimestampsAndRender();
            if (cpPickerMap) {
                cpPickerMap.setView([pt.lat, pt.lon], Math.max(cpPickerMap.getZoom(), 18));
            }
            inpQuickPaste.value = '';
        } else {
            alert('Could not recognize coordinates in pasted text. Please enter like: 00:00:02, 40.782500, -73.968000[, 42]');
        }
    }

    if (btnQuickAdd) btnQuickAdd.addEventListener('click', handleQuickPasteAdd);
    if (inpQuickPaste) {
        inpQuickPaste.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleQuickPasteAdd();
            }
        });
    }

    const btnAutoTrace = document.getElementById('btn-cp-picker-autotrace');
    const inpHeading = document.getElementById('cp-picker-heading');
    const inpPace = document.getElementById('cp-picker-pace');
    const traceStatus = document.getElementById('cp-picker-trace-status');

    if (btnAutoTrace) {
        btnAutoTrace.addEventListener('click', async () => {
            const inputSelect = document.getElementById('input_name');
            const selectedFile = inputSelect ? inputSelect.value : '';
            if (!selectedFile) {
                alert('Please select an Input Video File first in the main controls.');
                return;
            }

            // Determine Start Anchor Point
            let startLat = 40.781200;
            let startLon = -73.966500;
            let startEle = 40.0;

            if (cpPickerPoints.length > 0) {
                startLat = cpPickerPoints[0].lat;
                startLon = cpPickerPoints[0].lon;
                startEle = cpPickerPoints[0].ele || 40.0;
            }

            const initialHeading = parseFloat(inpHeading?.value || '270');
            const walkingSpeed = parseFloat(inpPace?.value || '1.15');

            btnAutoTrace.disabled = true;
            if (traceStatus) {
                traceStatus.style.display = 'inline';
                traceStatus.style.color = '#a78bfa';
                traceStatus.textContent = '⏳ Analyzing 360 motion & tracking trajectory...';
            }

            try {
                const formData = new FormData();
                /* action:auto_trace_video_trajectory → api/v1/streetview/trace-trajectory */
                formData.append('input', selectedFile);
                formData.append('start_lat', String(startLat));
                formData.append('start_lon', String(startLon));
                formData.append('start_ele', String(startEle));
                formData.append('initial_heading', String(initialHeading));
                formData.append('walking_speed', String(walkingSpeed));
                formData.append('checkpoint_interval', '10');

                const res = await fetch('api/v1/streetview/trace-trajectory', { method: 'POST', body: formData });
                const data = await res.json();

                if (data && data.status === 'success' && data.checkpoints) {
                    cpPickerPoints = data.checkpoints.map(cp => ({
                        timeStr: cp.timeStr,
                        sec: cp.sec,
                        lat: cp.lat,
                        lon: cp.lon,
                        ele: cp.ele
                    }));

                    recalcTimestampsAndRender();
                    fitMapToPath();

                    if (traceStatus) {
                        traceStatus.style.display = 'inline';
                        traceStatus.style.color = '#4ade80';
                        traceStatus.textContent = `✓ Auto-traced ${data.points_count} points (${data.total_distance_m}m)!`;
                        setTimeout(() => {
                            if (traceStatus) traceStatus.style.display = 'none';
                        }, 4000);
                    }
                } else {
                    alert(data?.error || 'Failed to auto-trace video trajectory.');
                    if (traceStatus) traceStatus.style.display = 'none';
                }
            } catch (err) {
                console.error(err);
                alert('Auto-trace error: ' + err.message);
                if (traceStatus) traceStatus.style.display = 'none';
            } finally {
                btnAutoTrace.disabled = false;
            }
        });
    }

    if (inpHeading) {
        inpHeading.addEventListener('input', () => {
            renderDirectionArrow();
        });
    }

    document.querySelectorAll('.btn-heading-dir').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const h = parseFloat(e.currentTarget.getAttribute('data-heading') || '0');
            if (inpHeading) inpHeading.value = h;
            updateDirectionPresetButtons(h);
            renderDirectionArrow();
        });
    });

    if (btnApply) {
        btnApply.addEventListener('click', () => {
            if (cpPickerPoints.length === 0) {
                alert('No checkpoints placed yet. Click on the satellite map to add points along your path.');
                return;
            }
            const lines = cpPickerPoints.map(p => {
                const latStr = p.lat.toFixed(8);
                const lonStr = p.lon.toFixed(8);
                const eleStr = p.ele ? Math.round(p.ele) : 315;
                return `${p.timeStr}, ${latStr}, ${lonStr}, ${eleStr}`;
            });
            if (cpText) {
                cpText.value = lines.join('\n');
            }
            closeModal();
        });
    }
}

// formatSecToHHMMSS and parseHHMMSSToSec are loaded from js/route_utils.js
const formatSecToHHMMSS = window.formatSecToHHMMSS;
const parseHHMMSSToSec = window.parseHHMMSSToSec;

function getHaversineDistanceMeters(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const phi1 = lat1 * Math.PI / 180;
    const phi2 = lat2 * Math.PI / 180;
    const dPhi = (lat2 - lat1) * Math.PI / 180;
    const dLambda = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dPhi / 2) * Math.sin(dPhi / 2) +
              Math.cos(phi1) * Math.cos(phi2) *
              Math.sin(dLambda / 2) * Math.sin(dLambda / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
}

function getVideoDurationForPicker() {
    const vid = document.getElementById('main-video-player');
    if (vid && vid.duration && isFinite(vid.duration) && vid.duration > 0) {
        return Math.round(vid.duration);
    }
    const autoPad = document.getElementById('streetview_auto_pad')?.checked ?? true;
    return autoPad ? 125 : 120;
}

function initOrUpdatePickerMap() {
    const mapContainer = document.getElementById('checkpoint-picker-map');
    if (!mapContainer || typeof L === 'undefined') return;

    if (!cpPickerMap) {
        // Default center on Lacul Ștei with deep zoom support up to 22
        cpPickerMap = L.map('checkpoint-picker-map', {
            maxZoom: 22
        }).setView([40.781200, -73.966500], 18);

        const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxNativeZoom: 19,
            maxZoom: 22,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
        });

        const esriSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            maxNativeZoom: 18,
            maxZoom: 22,
            attribution: '© Esri, Maxar'
        });

        const googleSat = L.tileLayer('https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
            subdomains: ['0', '1', '2', '3'],
            maxNativeZoom: 20,
            maxZoom: 22,
            attribution: '© Google Maps'
        }).addTo(cpPickerMap);

        const googleHybrid = L.tileLayer('https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', {
            subdomains: ['0', '1', '2', '3'],
            maxNativeZoom: 20,
            maxZoom: 22,
            attribution: '© Google Maps'
        });

        const baseMaps = {
            "Google Satellite (Ultra Zoom)": googleSat,
            "Google Hybrid (Roads & Labels)": googleHybrid,
            "Esri / Maxar Satellite": esriSat,
            "OpenStreetMap (Standard)": osm
        };

        L.control.layers(baseMaps, null, { position: 'topright' }).addTo(cpPickerMap);

        cpPickerPolyline = L.polyline([], { color: '#38bdf8', weight: 5, opacity: 0.9, smoothFactor: 1 }).addTo(cpPickerMap);

        cpPickerMap.on('click', (e) => {
            addCheckpointPoint(e.latlng.lat, e.latlng.lng);
        });
    }

    setTimeout(() => {
        cpPickerMap.invalidateSize();
        const cpText = document.getElementById('streetview_checkpoints')?.value?.trim() || '';
        if (cpPickerPoints.length === 0 && cpText) {
            parseTextToCheckpoints(cpText);
        } else {
            recalcTimestampsAndRender();
        }
        fitMapToPath();
    }, 150);
}

// createNumberedIcon and parsePastedCoordinateString are loaded from js/route_utils.js
const createNumberedIcon = window.createNumberedIcon;
const parsePastedCoordinateString = window.parsePastedCoordinateString;

function parseTextToCheckpoints(text) {
    cpPickerPoints = [];
    if (!text) return;

    const lines = text.split('\n');
    let autoSec = 0;

    lines.forEach((line) => {
        const pt = parsePastedCoordinateString(line);
        if (pt) {
            if (pt.sec === 0 && !pt.timeStr) pt.sec = autoSec;
            cpPickerPoints.push(pt);
            autoSec += 10;
        }
    });

    recalcTimestampsAndRender();
}

function addCheckpointPoint(lat, lon) {
    const ele = 315;
    cpPickerPoints.push({
        timeStr: '',
        sec: 0,
        lat: lat,
        lon: lon,
        ele: ele
    });
    recalcTimestampsAndRender();
}

function recalcTimestampsAndRender() {
    const totalDur = getVideoDurationForPicker();
    const vidInfo = document.getElementById('cp-picker-vid-info');
    if (vidInfo) {
        vidInfo.textContent = `Video Duration: ${totalDur}s (${formatSecToHHMMSS(totalDur)})`;
    }

    let totalDist = 0;
    const cumDists = [0];
    for (let i = 1; i < cpPickerPoints.length; i++) {
        const d = getHaversineDistanceMeters(
            cpPickerPoints[i - 1].lat, cpPickerPoints[i - 1].lon,
            cpPickerPoints[i].lat, cpPickerPoints[i].lon
        );
        totalDist += d;
        cumDists.push(totalDist);
    }

    for (let i = 0; i < cpPickerPoints.length; i++) {
        if (cpPickerPoints.length === 1) {
            cpPickerPoints[i].sec = 0;
        } else {
            const fraction = totalDist > 0 ? (cumDists[i] / totalDist) : (i / (cpPickerPoints.length - 1));
            cpPickerPoints[i].sec = Math.round(fraction * totalDur);
        }
        cpPickerPoints[i].timeStr = formatSecToHHMMSS(cpPickerPoints[i].sec);
    }

    renderMapMarkers();
    renderSidebarTable(totalDist);
}

function renderMapMarkers() {
    if (!cpPickerMap) return;

    cpPickerMarkers.forEach(m => cpPickerMap.removeLayer(m));
    cpPickerMarkers = [];

    const latlngs = [];

    cpPickerPoints.forEach((p, idx) => {
        const isStart = (idx === 0);
        const isEnd = (idx === cpPickerPoints.length - 1 && cpPickerPoints.length > 1);
        const icon = createNumberedIcon(idx + 1, isStart, isEnd);

        const marker = L.marker([p.lat, p.lon], {
            icon: icon,
            draggable: true,
            title: `Point ${idx + 1} (${p.timeStr})`
        }).addTo(cpPickerMap);

        marker.on('drag', (e) => {
            const newPos = e.target.getLatLng();
            cpPickerPoints[idx].lat = newPos.lat;
            cpPickerPoints[idx].lon = newPos.lng;
            updatePolylineOnly();
            updateStatsAndRowOnly(idx);
        });

        marker.on('dragend', () => {
            recalcTimestampsAndRender();
        });

        cpPickerMarkers.push(marker);
        latlngs.push([p.lat, p.lon]);
    });

    if (cpPickerPolyline) {
        cpPickerPolyline.setLatLngs(latlngs);
    }

    renderDirectionArrow();
}

let cpPickerDirectionLine = null;
let cpPickerDirectionMarker = null;

// calculateBearing, getDestinationPoint, updateDirectionPresetButtons are loaded from js/route_utils.js
const calculateBearing = window.calculateBearing;
const getDestinationPoint = window.getDestinationPoint;
const updateDirectionPresetButtons = window.updateDirectionPresetButtons;

function renderDirectionArrow() {
    if (!cpPickerMap) return;

    if (cpPickerDirectionLine) {
        cpPickerMap.removeLayer(cpPickerDirectionLine);
        cpPickerDirectionLine = null;
    }
    if (cpPickerDirectionMarker) {
        cpPickerMap.removeLayer(cpPickerDirectionMarker);
        cpPickerDirectionMarker = null;
    }

    if (cpPickerPoints.length === 0) return;

    const inpHeading = document.getElementById('cp-picker-heading');
    const p1 = cpPickerPoints[0];

    // If 2 or more points are placed, auto-calculate bearing from Pin 1 to Pin 2
    if (cpPickerPoints.length >= 2) {
        const p2 = cpPickerPoints[1];
        const autoBrng = calculateBearing(p1.lat, p1.lon, p2.lat, p2.lon);
        if (inpHeading) inpHeading.value = Math.round(autoBrng);
        updateDirectionPresetButtons(autoBrng);
        return;
    }

    // When only Pin 1 is placed, show interactive direction arrow extending from Pin 1
    const currentHeading = parseFloat(inpHeading?.value || '270');
    updateDirectionPresetButtons(currentHeading);
    const dest = getDestinationPoint(p1.lat, p1.lon, currentHeading, 35);

    cpPickerDirectionLine = L.polyline([[p1.lat, p1.lon], dest], {
        color: '#a855f7',
        weight: 4,
        dashArray: '6, 6',
        opacity: 0.95
    }).addTo(cpPickerMap);

    const arrowIcon = L.divIcon({
        html: `
            <div style="width: 28px; height: 28px; background: linear-gradient(135deg, #a855f7 0%, #7c3aed 100%); border: 2px solid #ffffff; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 13px; font-weight: bold; box-shadow: 0 4px 10px rgba(0,0,0,0.6); cursor: grab;" title="Drag to point walking direction!">
                ➤
            </div>
        `,
        className: 'custom-dir-arrow',
        iconSize: [28, 28],
        iconAnchor: [14, 14]
    });

    cpPickerDirectionMarker = L.marker(dest, {
        icon: arrowIcon,
        draggable: true,
        title: 'Drag to point walking direction!'
    }).addTo(cpPickerMap);

    cpPickerDirectionMarker.on('drag', (e) => {
        const newPos = e.target.getLatLng();
        const newBrng = calculateBearing(p1.lat, p1.lon, newPos.lat, newPos.lng);
        if (inpHeading) inpHeading.value = Math.round(newBrng);
        updateDirectionPresetButtons(newBrng);
        if (cpPickerDirectionLine) {
            cpPickerDirectionLine.setLatLngs([[p1.lat, p1.lon], [newPos.lat, newPos.lng]]);
        }
    });

    cpPickerDirectionMarker.on('dragend', () => {
        renderDirectionArrow();
    });
}

function updatePolylineOnly() {
    if (!cpPickerPolyline) return;
    const latlngs = cpPickerPoints.map(p => [p.lat, p.lon]);
    cpPickerPolyline.setLatLngs(latlngs);
}

function updateStatsAndRowOnly(idx) {
    const latInp = document.getElementById(`cp-lat-${idx}`);
    const lonInp = document.getElementById(`cp-lon-${idx}`);
    if (latInp && lonInp && cpPickerPoints[idx]) {
        latInp.value = cpPickerPoints[idx].lat.toFixed(6);
        lonInp.value = cpPickerPoints[idx].lon.toFixed(6);
    }
}

function renderSidebarTable(totalDist) {
    const tbody = document.getElementById('cp-picker-tbody');
    const table = document.getElementById('cp-picker-table');
    const emptyHint = document.getElementById('cp-picker-empty-hint');
    const statsEl = document.getElementById('cp-picker-stats');
    const countEl = document.getElementById('cp-picker-pts-count');

    if (statsEl) {
        statsEl.textContent = `${cpPickerPoints.length} pts | ${totalDist.toFixed(1)} m`;
    }
    if (countEl) {
        countEl.textContent = `${cpPickerPoints.length} pts`;
    }

    if (!tbody || !table || !emptyHint) return;

    if (cpPickerPoints.length === 0) {
        emptyHint.style.display = 'block';
        table.style.display = 'none';
        tbody.innerHTML = '';
        return;
    }

    emptyHint.style.display = 'none';
    table.style.display = 'table';
    tbody.innerHTML = '';

    cpPickerPoints.forEach((p, idx) => {
        const tr = document.createElement('tr');
        tr.style.borderBottom = '1px solid rgba(255,255,255,0.06)';

        const isStart = (idx === 0);
        const isEnd = (idx === cpPickerPoints.length - 1 && cpPickerPoints.length > 1);
        const badgeBg = isStart ? '#10b981' : (isEnd ? '#ef4444' : '#0284c7');

        tr.innerHTML = `
            <td style="padding: 6px 4px; text-align:center;">
                <span style="display:inline-block; width:18px; height:18px; line-height:18px; background:${badgeBg}; color:#fff; border-radius:50%; font-size:10px; font-weight:bold;">${idx + 1}</span>
            </td>
            <td style="padding: 6px 4px;">
                <input type="text" value="${p.timeStr}" style="width:100%; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:3px; color:#38bdf8; font-size:11px; padding:2px 4px; font-family:monospace;" onchange="window.updatePointTime(${idx}, this.value)">
            </td>
            <td style="padding: 6px 4px;">
                <div style="display:flex; gap:2px;">
                    <input id="cp-lat-${idx}" type="text" value="${p.lat.toFixed(6)}" style="width:50%; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:3px; color:#fff; font-size:10px; padding:2px 3px; font-family:monospace;" onchange="window.updatePointCoord(${idx}, this.value, null)">
                    <input id="cp-lon-${idx}" type="text" value="${p.lon.toFixed(6)}" style="width:50%; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:3px; color:#fff; font-size:10px; padding:2px 3px; font-family:monospace;" onchange="window.updatePointCoord(${idx}, null, this.value)">
                </div>
            </td>
            <td style="padding: 6px 4px;">
                <input type="number" value="${Math.round(p.ele)}" style="width:100%; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:3px; color:#cbd5e1; font-size:11px; padding:2px 3px; font-family:monospace;" onchange="window.updatePointEle(${idx}, this.value)">
            </td>
            <td style="padding: 6px 2px; text-align:center;">
                <button type="button" style="background:none; border:none; color:#ef4444; font-size:14px; cursor:pointer; padding:0 2px;" onclick="window.deletePoint(${idx})" title="Delete point">&times;</button>
            </td>
        `;

        tr.addEventListener('mouseenter', () => {
            if (cpPickerMarkers[idx]) {
                cpPickerMarkers[idx].setZIndexOffset(1000);
            }
        });
        tr.addEventListener('mouseleave', () => {
            if (cpPickerMarkers[idx]) {
                cpPickerMarkers[idx].setZIndexOffset(0);
            }
        });

        tbody.appendChild(tr);
    });
}

function fitMapToPath() {
    if (!cpPickerMap || cpPickerPoints.length === 0) return;
    const latlngs = cpPickerPoints.map(p => [p.lat, p.lon]);
    if (latlngs.length === 1) {
        cpPickerMap.setView(latlngs[0], 18);
    } else {
        const bounds = L.latLngBounds(latlngs);
        cpPickerMap.fitBounds(bounds, { padding: [50, 50] });
    }
}

window.updatePointTime = function(idx, val) {
    if (cpPickerPoints[idx]) {
        cpPickerPoints[idx].timeStr = val;
        cpPickerPoints[idx].sec = parseHHMMSSToSec(val);
    }
};

window.updatePointCoord = function(idx, latVal, lonVal) {
    if (cpPickerPoints[idx]) {
        if (latVal !== null && !isNaN(parseFloat(latVal))) cpPickerPoints[idx].lat = parseFloat(latVal);
        if (lonVal !== null && !isNaN(parseFloat(lonVal))) cpPickerPoints[idx].lon = parseFloat(lonVal);
        renderMapMarkers();
    }
};

window.updatePointEle = function(idx, val) {
    if (cpPickerPoints[idx]) {
        cpPickerPoints[idx].ele = parseFloat(val) || 315;
    }
};

window.deletePoint = function(idx) {
    if (cpPickerPoints[idx]) {
        cpPickerPoints.splice(idx, 1);
        recalcTimestampsAndRender();
    }
};


export {
    initCheckpointPickerModal,
    cpPickerMap,
    cpPickerMarkers,
    cpPickerPolyline,
    cpPickerPoints
};
