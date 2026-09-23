import { initSystemHealth, getSystemHealth } from './js/diagnostics.js';
import { initCheckpointPickerModal } from './js/streetview_picker.js';

async function safeFetchJson(url, options = {}) {
    try {
        const response = await fetch(url, options);
        const text = await response.text();
        if (!text || !text.trim()) return null;
        try {
            return JSON.parse(text);
        } catch (e) {
            console.warn('Non-JSON response from', url, text.substring(0, 100));
            return null;
        }
    } catch (err) {
        console.warn('Fetch failed for', url, err);
        return null;
    }
}
if (typeof window !== 'undefined') {
    window.safeFetchJson = safeFetchJson;
}

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import {
    showCheckpointPauseModal,
    launchHorizonEditor,
    resumePipelineAfterCheckpoints,
    showTransformSelectModal,
    submitChosenTransforms
} from './js/horizon_modals.js';

// Centralized configuration mirroring config/config.json
const APP_CONFIG = {
    pipeline_defaults: {
        ih_fov: '190.00',
        iv_fov: '190.00',
        raw_rotation: '0',
        yaw: '0',
        pitch: '0',
        roll: '0',
        left_y_offset: '0.00',
        rear_roll_offset: '0.00',
        blend_width: '200',
        anti_vignette_angle: '0.785',
        ffmpeg_preset: 'medium',
        ffmpeg_crf: '18',
        nadir_fov: '75',
        nadir_fov_v: '75',
        nadir_logo_default: 'logo_stei_circle.png',
        stab_quality_mode: '4',
        vidstab_smoothing: '2',
        vidstab_shakiness: '1',
        vidstab_stepsize: '32',
        vidstab_optalgo: 'gauss',
        kabsch_smoothing: '5',
        kopf_keyframe_sec: '2.0',
        kopf_cube_face: '1024',
        kopf_max_features: '400',
        telemetry_smoothing: '30',
        telemetry_ref_frame: '0',
        telemetry_mode: 'smooth',
        telemetry_source: 'auto',
        telemetry_multiplier: '1.0',
        telemetry_fusion: 'mahony',
        telemetry_fusion_gain: '0.51',
        streetview_bitrate: '45M',
        video_bitrate: '80M'
    }
};

safeFetchJson('config/config.json').then(cfg => {
    if (cfg && cfg.pipeline_defaults) {
        Object.assign(APP_CONFIG.pipeline_defaults, cfg.pipeline_defaults);
    }
}).catch(() => {});

// DOM Elements — sliders (in_pad removed)
const sliders = {
    ih_fov:        document.getElementById('ih_fov'),
    iv_fov:        document.getElementById('iv_fov'),
    yaw:           document.getElementById('yaw'),
    pitch:         document.getElementById('pitch'),
    roll:          document.getElementById('roll'),
    left_y_offset: document.getElementById('left_y_offset'),
    rear_roll_offset: document.getElementById('rear_roll_offset')
};

const sliderValues = {
    ih_fov:        document.getElementById('ih_fov_val'),
    iv_fov:        document.getElementById('iv_fov_val'),
    yaw:           document.getElementById('yaw_val'),
    pitch:         document.getElementById('pitch_val'),
    roll:          document.getElementById('roll_val'),
    left_y_offset: document.getElementById('left_y_offset_val'),
    rear_roll_offset: document.getElementById('rear_roll_offset_val')
};

// Nadir & Vidstab sliders (not included in preview, only used on stitch)
const nadirFovSlider  = document.getElementById('nadir_fov');
const nadirFovVSlider = document.getElementById('nadir_fov_v');
const nadirFovVal     = document.getElementById('nadir_fov_val');
const nadirFovVVal    = document.getElementById('nadir_fov_v_val');

const stabSmoothingSlider = document.getElementById('vidstab_smoothing');
const stabShakinessSlider = document.getElementById('vidstab_shakiness');
const stabSmoothingVal    = document.getElementById('vidstab_smoothing_val');
const stabShakinessVal    = document.getElementById('vidstab_shakiness_val');

const blendWidthSlider = document.getElementById('blend_width');
const blendWidthVal    = document.getElementById('blend_width_val');


const PRE_APPLIED_FEATURE_MAP = {
    'input_has_stitched': 'enable_stitching',
    'input_has_telemetry': 'stab_telemetry',
    'input_has_kopf': 'stab_kopf',
    'input_has_kabsch': 'stab_kabsch',
    'input_has_vidstab': 'stab_vidstab',
    'input_has_cinematic': 'stab_cinematic',
    'input_has_horizon': 'stab_horizon',
    'input_has_traveldir': 'stab_traveldir',
    'input_has_nadir': 'nadir_enabled'
};

const STAB_METHOD_IDS = ['stab_telemetry', 'stab_kopf', 'stab_kabsch', 'stab_vidstab', 'stab_cinematic', 'stab_horizon', 'stab_traveldir'];

let isApplyingConfig = false;

function syncStabMasterState() {
    if (isApplyingConfig) return;
    const stabilizeCb = document.getElementById('stabilize');
    if (!stabilizeCb) return;
    const anyStabChecked = STAB_METHOD_IDS.some(id => document.getElementById(id)?.checked);
    if (!anyStabChecked && stabilizeCb.checked) {
        stabilizeCb.checked = false;
        stabilizeCb.dispatchEvent(new Event('change'));
    } else if (anyStabChecked && !stabilizeCb.checked) {
        stabilizeCb.checked = true;
        stabilizeCb.dispatchEvent(new Event('change'));
    }
}

const DEFAULT_STAB_ORDER = ['telemetry', 'kopf', 'kabsch', 'vidstab', 'cinematic', 'horizon', 'traveldir'];
const STAB_METHOD_LABELS = {
    'telemetry': 'Telemetry',
    'kopf': 'Kopf',
    'kabsch': 'Kabsch',
    'vidstab': 'Vidstab',
    'cinematic': 'Cinematic',
    'horizon': 'Horizon',
    'traveldir': 'Travel-Dir'
};

function getOrderedStabMethods() {
    const listEl = document.getElementById('stab-order-list');
    if (!listEl) return [...DEFAULT_STAB_ORDER];
    const items = listEl.querySelectorAll('.stab-order-item');
    const order = [];
    items.forEach(item => {
        const m = item.getAttribute('data-method');
        if (m) order.push(m);
    });
    return order.length ? order : [...DEFAULT_STAB_ORDER];
}

function updateStabButtonStates() {
    const listEl = document.getElementById('stab-order-list');
    if (!listEl) return;
    const items = listEl.querySelectorAll('.stab-order-item');
    const total = items.length;
    items.forEach((item, idx) => {
        const btnUp = item.querySelector('.btn-stab-up');
        const btnDown = item.querySelector('.btn-stab-down');
        
        if (btnUp) {
            const isFirst = (idx === 0);
            btnUp.disabled = isFirst;
            btnUp.style.opacity = isFirst ? '0.2' : '1';
            btnUp.style.cursor = isFirst ? 'not-allowed' : 'pointer';
            btnUp.style.pointerEvents = isFirst ? 'none' : 'auto';
            if (isFirst) {
                btnUp.removeAttribute('title');
            } else {
                btnUp.setAttribute('title', 'Move up');
            }
        }
        if (btnDown) {
            const isLast = (idx === total - 1);
            btnDown.disabled = isLast;
            btnDown.style.opacity = isLast ? '0.2' : '1';
            btnDown.style.cursor = isLast ? 'not-allowed' : 'pointer';
            btnDown.style.pointerEvents = isLast ? 'none' : 'auto';
            if (isLast) {
                btnDown.removeAttribute('title');
            } else {
                btnDown.setAttribute('title', 'Move down');
            }
        }
    });
}

function updateVideoTabsOrder() {
    const tabsContainer = document.querySelector('.viewport-tabs');
    const viewportWrapper = document.querySelector('.viewport-wrapper');
    const orderedMethods = getOrderedStabMethods();

    if (tabsContainer) {
        const tabFinal = document.getElementById('tab-360');
        orderedMethods.forEach(method => {
            const tabEl = document.getElementById(`tab-${method}`);
            if (tabEl) {
                if (tabFinal) {
                    tabsContainer.insertBefore(tabEl, tabFinal);
                } else {
                    tabsContainer.appendChild(tabEl);
                }
            }
        });
        if (tabFinal) {
            tabsContainer.appendChild(tabFinal);
        }
    }

    if (viewportWrapper) {
        const viewFinal = document.getElementById('360-container');
        orderedMethods.forEach(method => {
            const viewEl = document.getElementById(`${method}-container`);
            if (viewEl) {
                if (viewFinal) {
                    viewportWrapper.insertBefore(viewEl, viewFinal);
                } else {
                    viewportWrapper.appendChild(viewEl);
                }
            }
        });
        if (viewFinal) {
            viewportWrapper.appendChild(viewFinal);
        }
    }
}

function checkStabOrderConflicts() {
    const listEl = document.getElementById('stab-order-list');
    const warningEl = document.getElementById('stab-order-warning');
    const warningText = document.getElementById('stab-order-warning-text');
    if (!listEl) return null;

    const items = listEl.querySelectorAll('.stab-order-item');
    const activeOrder = [];
    items.forEach(item => {
        const m = item.getAttribute('data-method');
        const cb = item.querySelector('input[type="checkbox"]');
        if (m && cb && cb.checked) {
            activeOrder.push(m);
        }
    });

    // Reset item highlight borders
    items.forEach(item => {
        item.style.borderColor = 'rgba(255,255,255,0.08)';
    });

    let conflict = null;
    const hIdx = activeOrder.indexOf('horizon');
    const tIdx = activeOrder.indexOf('telemetry');

    if (hIdx !== -1 && tIdx !== -1 && hIdx < tIdx) {
        conflict = {
            type: 'horizon_before_telemetry',
            message: 'Horizon Leveling is scheduled before Telemetry (IMU). Telemetry must run first to establish the true gravity reference frame, otherwise coordinate cross-bleeding and conflicting dual-leveling will occur.'
        };
        const hItem = listEl.querySelector('.stab-order-item[data-method="horizon"]');
        const tItem = listEl.querySelector('.stab-order-item[data-method="telemetry"]');
        if (hItem) hItem.style.borderColor = 'rgba(239, 68, 68, 0.6)';
        if (tItem) tItem.style.borderColor = 'rgba(245, 158, 11, 0.6)';
    }

    if (warningEl) {
        if (conflict) {
            if (warningText) warningText.textContent = conflict.message;
            warningEl.style.display = 'block';
        } else {
            warningEl.style.display = 'none';
        }
    }
    return conflict;
}

function fixStabOrderConflict() {
    const listEl = document.getElementById('stab-order-list');
    if (!listEl) return;
    const tItem = listEl.querySelector('.stab-order-item[data-method="telemetry"]');
    const hItem = listEl.querySelector('.stab-order-item[data-method="horizon"]');
    if (tItem && hItem) {
        listEl.insertBefore(tItem, hItem);
        saveStabOrder();
        updateStabOrderDisplay();
        updateStabButtonStates();
    }
}

function updateStabOrderDisplay() {
    const displayEl = document.getElementById('stab-order-display');
    const listEl = document.getElementById('stab-order-list');
    updateStabButtonStates();
    updateVideoTabsOrder();
    checkStabOrderConflicts();
    if (!displayEl || !listEl) return;

    const items = listEl.querySelectorAll('.stab-order-item');
    const activeLabels = [];

    items.forEach(item => {
        const method = item.getAttribute('data-method');
        const cb = item.querySelector('input[type="checkbox"]');
        if (cb && cb.checked && STAB_METHOD_LABELS[method]) {
            activeLabels.push(STAB_METHOD_LABELS[method]);
        }
    });

    if (activeLabels.length === 0) {
        displayEl.innerHTML = '<span style="color:#94a3b8; font-style:italic;">None (all methods unchecked)</span>';
    } else {
        displayEl.innerHTML = activeLabels.map((lbl) => `<span style="color:#f8fafc; font-weight:600;">${lbl}</span>`).join(' <span style="color:#cbd5e1;">&rarr;</span> ');
    }
}

function moveStabItem(methodKey, direction) {
    const listEl = document.getElementById('stab-order-list');
    if (!listEl) return;
    const item = listEl.querySelector(`.stab-order-item[data-method="${methodKey}"]`);
    if (!item) return;

    if (direction < 0 && item.previousElementSibling) {
        listEl.insertBefore(item, item.previousElementSibling);
    } else if (direction > 0 && item.nextElementSibling) {
        listEl.insertBefore(item.nextElementSibling, item);
    }

    saveStabOrder();
    updateStabOrderDisplay();
    updateStabButtonStates();
}

function resetStabOrder() {
    const listEl = document.getElementById('stab-order-list');
    if (!listEl) return;

    DEFAULT_STAB_ORDER.forEach(m => {
        const item = listEl.querySelector(`.stab-order-item[data-method="${m}"]`);
        if (item) listEl.appendChild(item);
    });

    saveStabOrder();
    updateStabOrderDisplay();
    updateStabButtonStates();
}

function saveStabOrder() {
    try {
        const order = getOrderedStabMethods();
        localStorage.setItem('360_stab_pipeline_order', JSON.stringify(order));
    } catch (e) {}
}

function loadStabOrder(customOrder) {
    try {
        let order = customOrder;
        if (!order) {
            const saved = localStorage.getItem('360_stab_pipeline_order');
            if (saved) {
                try {
                    order = JSON.parse(saved);
                } catch (e) {
                    order = saved;
                }
            }
        }
        if (typeof order === 'string') {
            order = order.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
        }
        if (Array.isArray(order) && order.length > 0) {
            const listEl = document.getElementById('stab-order-list');
            if (!listEl) return;
            order.forEach(m => {
                const item = listEl.querySelector(`.stab-order-item[data-method="${m}"]`);
                if (item) listEl.appendChild(item);
            });
            DEFAULT_STAB_ORDER.forEach(m => {
                if (!order.includes(m)) {
                    const item = listEl.querySelector(`.stab-order-item[data-method="${m}"]`);
                    if (item) listEl.appendChild(item);
                }
            });
            saveStabOrder();
            updateStabOrderDisplay();
        }
    } catch (e) {}
}

window.moveStabItem = moveStabItem;
window.resetStabOrder = resetStabOrder;
window.updateStabOrderDisplay = updateStabOrderDisplay;
window.getOrderedStabMethods = getOrderedStabMethods;
window.saveStabOrder = saveStabOrder;
window.loadStabOrder = loadStabOrder;
window.updateStabButtonStates = updateStabButtonStates;
window.updateVideoTabsOrder = updateVideoTabsOrder;
window.checkStabOrderConflicts = checkStabOrderConflicts;
window.fixStabOrderConflict = fixStabOrderConflict;

function updateInputFeaturesBadge() {
    const badge = document.getElementById('input-features-badge');
    if (!badge) return;
    badge.style.display = 'none';
}

function isPhotoFile(filename) {
    if (!filename) return false;
    return /\.(jpe?g|png|webp)$/i.test(filename.trim());
}

function syncPhotoMode(filename) {
    const isPhoto = isPhotoFile(filename);
    const cardStep2 = document.getElementById('card-step2-stabilization');
    const cardStep4 = document.getElementById('card-step4-streetview');
    const cardCrop = document.getElementById('card-crop-video');
    const cardPreApplied = document.getElementById('card-input-pre-applied');
    const cardExternalUtils = document.getElementById('card-external-utils');
    const btnPreviewVid = document.getElementById('btn-preview-vid');
    const stabCb = document.getElementById('stabilize');
    const svCb = document.getElementById('streetview_enabled');
    const calibFrames = document.getElementById('auto_calibrate_frames');
    const enableStitchCb = document.getElementById('enable_stitching');

    if (cardStep2) cardStep2.style.display = isPhoto ? 'none' : '';
    if (cardStep4) cardStep4.style.display = isPhoto ? 'none' : '';
    if (cardCrop) cardCrop.style.display = isPhoto ? 'none' : '';
    if (cardPreApplied) cardPreApplied.style.display = isPhoto ? 'none' : '';
    if (cardExternalUtils) cardExternalUtils.style.display = isPhoto ? 'none' : '';
    if (btnPreviewVid) btnPreviewVid.style.display = isPhoto ? 'none' : '';

    if (isPhoto) {
        if (stabCb && stabCb.checked) {
            stabCb.checked = false;
            stabCb.dispatchEvent(new Event('change'));
        }
        if (svCb && svCb.checked) {
            svCb.checked = false;
            svCb.dispatchEvent(new Event('change'));
        }
        const utilGcsv = document.getElementById('util_gcsv');
        if (utilGcsv && utilGcsv.checked) utilGcsv.checked = false;
        const utilBigshot = document.getElementById('util_bigsh0t');
        if (utilBigshot && utilBigshot.checked) utilBigshot.checked = false;
        if (enableStitchCb && !enableStitchCb.checked) {
            enableStitchCb.checked = true;
            enableStitchCb.dispatchEvent(new Event('change'));
        }
        if (calibFrames) {
            calibFrames.value = '1';
            Array.from(calibFrames.options).forEach(opt => {
                if (opt.value !== '1') opt.disabled = true;
            });
        }
    } else {
        if (calibFrames) {
            Array.from(calibFrames.options).forEach(opt => {
                opt.disabled = false;
            });
        }
    }
}

function detectInputFeatures(filename) {
    if (!filename) return;
    syncPhotoMode(filename);
    Object.keys(PRE_APPLIED_FEATURE_MAP).forEach(preId => {
        const elPre = document.getElementById(preId);
        if (elPre) elPre.checked = false;
    });
    syncStabMasterState();
    updateInputFeaturesBadge();
}

const options = {
    input:  document.getElementById('input_name'),
    ffmpeg_preset: document.getElementById('ffmpeg_preset'),
    ffmpeg_crf:    document.getElementById('ffmpeg_crf'),
    ffmpeg_hwaccel:document.getElementById('ffmpeg_hwaccel'),
    v360_vulkan:   document.getElementById('v360_vulkan'),
    util_gcsv: document.getElementById('util_gcsv'),
    util_bigsh0t: document.getElementById('util_bigsh0t'),
    output: document.getElementById('output_name')
};

const btnPreview    = document.getElementById('btn-preview');
const btnStitch     = document.getElementById('btn-stitch');
const btnReset      = document.getElementById('btn-reset');
const previewImg    = document.getElementById('preview-img');
const previewSpinner= document.getElementById('preview-spinner');
const progressCard  = document.getElementById('progress-card');
const progressBarFill=document.getElementById('progress-bar-fill');
const progressPercent=document.getElementById('progress-percent');
const statusBadge   = document.getElementById('status-badge');
const metaSpeed     = document.getElementById('meta-speed');
const metaEta       = document.getElementById('meta-eta');
const metaElapsed   = document.getElementById('meta-elapsed');
const phaseLabel    = document.getElementById('phase-label');
let userClearedLog  = false;
let currentJobId = sessionStorage.getItem('current_tab_job_id') || '';
Object.defineProperty(window, 'currentJobId', {
    get: () => currentJobId,
    set: (val) => { currentJobId = val; },
    configurable: true
});

const tabOriginal        = document.getElementById('tab-original');
const tabPreview         = document.getElementById('tab-preview');
const tabStitched        = document.getElementById('tab-stitched');
const tabTelemetry       = document.getElementById('tab-telemetry');
const tabKopf            = document.getElementById('tab-kopf');
const tabKabsch          = document.getElementById('tab-kabsch');
const tabVidstab         = document.getElementById('tab-vidstab');
const tabCinematic        = document.getElementById('tab-cinematic');
const tabHorizon         = document.getElementById('tab-horizon');
const tabTraveldir   = document.getElementById('tab-traveldir');
const tab360             = document.getElementById('tab-360');

const viewOriginal       = document.getElementById('original-container');
const viewPreview        = document.getElementById('preview-container');
const viewStitched       = document.getElementById('stitched-container');
const viewTelemetry      = document.getElementById('telemetry-container');
const viewKopf           = document.getElementById('kopf-container');
const viewKabsch         = document.getElementById('kabsch-container');
const viewVidstab        = document.getElementById('vidstab-container');
const viewCinematic       = document.getElementById('cinematic-container');
const viewHorizon        = document.getElementById('horizon-container');
const viewTraveldir  = document.getElementById('traveldir-container');
const view360            = document.getElementById('360-container');

const originalImg        = document.getElementById('original-img');
const videoTelemetry     = document.getElementById('video-telemetry');
const videoKopf          = document.getElementById('video-kopf');
const videoKabsch        = document.getElementById('video-kabsch');
const videoVidstab       = document.getElementById('video-vidstab');
const videoCinematic      = document.getElementById('video-cinematic');
const videoHorizon       = document.getElementById('video-horizon');
const videoTraveldir = document.getElementById('video-traveldir');
const video360           = document.getElementById('video-360');

const playerCinematicContainer      = document.getElementById('player-cinematic-container');
const playerTraveldirContainer = document.getElementById('player-traveldir-container');

const player360Container = document.getElementById('player-360-container');
const vrPlayPause    = document.getElementById('vr-play-pause');
const vrSeekbar      = document.getElementById('vr-seekbar');
const vrTimeDisplay  = document.getElementById('vr-time-display');
const vrMute         = document.getElementById('vr-mute');
const vrVolume       = document.getElementById('vr-volume');
const vrFullscreen   = document.getElementById('vr-fullscreen');
const vrFirstFrame   = document.getElementById('vr-first-frame');
const vrPrevFrame    = document.getElementById('vr-prev-frame');
const vrNextFrame    = document.getElementById('vr-next-frame');
const vrLastFrame    = document.getElementById('vr-last-frame');

const videoStitched        = document.getElementById('video-stitched');
const playerStitchedContainer = document.getElementById('player-stitched-container');
const vrStitchedPlayPause  = document.getElementById('vr-stitched-play-pause');
const vrStitchedSeekbar    = document.getElementById('vr-stitched-seekbar');
const vrStitchedTimeDisplay= document.getElementById('vr-stitched-time-display');
const vrStitchedMute       = document.getElementById('vr-stitched-mute');
const vrStitchedVolume     = document.getElementById('vr-stitched-volume');
const vrStitchedFullscreen = document.getElementById('vr-stitched-fullscreen');
const vrStitchedFirstFrame = document.getElementById('vr-stitched-first-frame');
const vrStitchedPrevFrame  = document.getElementById('vr-stitched-prev-frame');
const vrStitchedNextFrame  = document.getElementById('vr-stitched-next-frame');
const vrStitchedLastFrame  = document.getElementById('vr-stitched-last-frame');

const popupStitched       = document.getElementById('popup-stitched');
const popupStitchedHeader = document.getElementById('popup-stitched-header');
const popupStitchedBody   = document.getElementById('popup-stitched-body');
const btnClosePopupStitched = document.getElementById('btn-close-popup-stitched');

const popup360            = document.getElementById('popup-360');
const popup360Header      = document.getElementById('popup-360-header');
const popup360Body        = document.getElementById('popup-360-body');
const btnClosePopup360    = document.getElementById('btn-close-popup-360');

const popupOriginal            = document.getElementById('popup-original');
const popupOriginalHeader      = document.getElementById('popup-original-header');
const popupOriginalBody        = document.getElementById('popup-original-body');
const btnClosePopupOriginal    = document.getElementById('btn-close-popup-original');

const popupPreview            = document.getElementById('popup-preview');
const popupPreviewHeader      = document.getElementById('popup-preview-header');
const popupPreviewBody        = document.getElementById('popup-preview-body');
const btnClosePopupPreview    = document.getElementById('btn-close-popup-preview');

const popupTelemetry          = document.getElementById('popup-telemetry');
const popupTelemetryHeader    = document.getElementById('popup-telemetry-header');
const popupTelemetryBody      = document.getElementById('popup-telemetry-body');
const btnClosePopupTelemetry  = document.getElementById('btn-close-popup-telemetry');

const popupKopf               = document.getElementById('popup-kopf');
const popupKopfHeader         = document.getElementById('popup-kopf-header');
const popupKopfBody           = document.getElementById('popup-kopf-body');
const btnClosePopupKopf       = document.getElementById('btn-close-popup-kopf');

const popupKabsch             = document.getElementById('popup-kabsch');
const popupKabschHeader       = document.getElementById('popup-kabsch-header');
const popupKabschBody         = document.getElementById('popup-kabsch-body');
const btnClosePopupKabsch     = document.getElementById('btn-close-popup-kabsch');

const popupVidstab            = document.getElementById('popup-vidstab');
const popupVidstabHeader      = document.getElementById('popup-vidstab-header');
const popupVidstabBody        = document.getElementById('popup-vidstab-body');
const btnClosePopupVidstab    = document.getElementById('btn-close-popup-vidstab');

const popupCinematic           = document.getElementById('popup-cinematic');
const popupCinematicHeader     = document.getElementById('popup-cinematic-header');
const popupCinematicBody       = document.getElementById('popup-cinematic-body');
const btnClosePopupCinematic   = document.getElementById('btn-close-popup-cinematic');

const popupHorizon            = document.getElementById('popup-horizon');
const popupHorizonHeader      = document.getElementById('popup-horizon-header');
const popupHorizonBody        = document.getElementById('popup-horizon-body');
const btnClosePopupHorizon    = document.getElementById('btn-close-popup-horizon');

const popupTraveldir         = document.getElementById('popup-traveldir');
const popupTraveldirHeader   = document.getElementById('popup-traveldir-header');
const popupTraveldirBody     = document.getElementById('popup-traveldir-body');
const btnClosePopupTraveldir = document.getElementById('btn-close-popup-traveldir');

const popupGraph              = document.getElementById('popup-graph');
const popupGraphHeader        = document.getElementById('popup-graph-header');
const popupGraphBody          = document.getElementById('popup-graph-body');
const btnClosePopupGraph      = document.getElementById('btn-close-popup-graph');
const graphImg                = document.getElementById('graph-img');
let currentTelemetryGraphUrl     = '';
let currentKopfGraphUrl          = '';
let currentKabschGraphUrl        = '';
let currentVidstabGraphUrl       = '';
let currentCinematicGraphUrl      = '';
let currentHorizonGraphUrl      = '';
let currentTraveldirGraphUrl = '';

const popupReport              = document.getElementById('popup-report');
const popupReportHeader        = document.getElementById('popup-report-header');
const popupReportBody          = document.getElementById('popup-report-body');
const btnClosePopupReport      = document.getElementById('btn-close-popup-report');
const popupReportText          = document.getElementById('popup-report-text');
const popupReportTitleText     = document.getElementById('popup-report-title-text');
let currentTelemetryReportFile     = '';
let currentKopfReportFile          = '';
let currentKabschReportFile        = '';
let currentVidstabReportFile       = '';
let currentCinematicReportFile      = '';
let currentHorizonReportFile       = '';
let currentTraveldirReportFile = '';

const originalVideo        = document.getElementById('original-video');
const origControlsOverlay    = document.getElementById('orig-controls-overlay');
const origPlayPause        = document.getElementById('orig-play-pause');
const origSeekbar          = document.getElementById('orig-seekbar');
const origTimeDisplay      = document.getElementById('orig-time-display');
const origMute             = document.getElementById('orig-mute');
const origVolume           = document.getElementById('orig-volume');
const origFullscreen       = document.getElementById('orig-fullscreen');
const origFirstFrame       = document.getElementById('orig-first-frame');
const origPrevFrame        = document.getElementById('orig-prev-frame');
const origNextFrame        = document.getElementById('orig-next-frame');
const origLastFrame        = document.getElementById('orig-last-frame');
const playerOrigContainer  = document.getElementById('player-orig-container');
const origToggleVR         = document.getElementById('orig-toggle-vr');
const origToggleVRText     = document.getElementById('orig-toggle-vr-text');


const trimStartFrame   = document.getElementById('trim_start_frame');
const trimEndFrame     = document.getElementById('trim_end_frame');
const trimStartSlider  = document.getElementById('trim_start_slider');
const trimEndSlider    = document.getElementById('trim_end_slider');
const trimStartTimeLbl = document.getElementById('trim_start_time_lbl');
const trimEndTimeLbl   = document.getElementById('trim_end_time_lbl');
const videoMetadataLbl = document.getElementById('video_metadata_lbl');
const croppedOutputName = document.getElementById('cropped_output_name');
const btnCropSave      = document.getElementById('btn-crop-save');
const btnSetStart      = document.getElementById('btn-set-start');
const btnSetEnd        = document.getElementById('btn-set-end');
const btnDetectWarmup  = document.getElementById('btn-detect-warmup');
const warmupDetectStatus = document.getElementById('warmup_detect_status');

let currentVideoMeta = {
    duration: 0,
    fps: 30,
    total_frames: 0
};

let pollInterval         = null;
let lastHandledCompletedOutput = null;
let threeScene, threeCamera, threeRenderer, threeControls, threeSphere, threeTexture;
let isThreeInitialized   = false;

let threeStitchedScene, threeStitchedCamera, threeStitchedRenderer, threeStitchedControls, threeStitchedSphere, threeStitchedTexture;
let isThreeStitchedInitialized = false;

let threeOrigScene, threeOrigCamera, threeOrigRenderer, threeOrigControls, threeOrigSphere, threeOrigTexture;
let isThreeOrigInitialized = false;
let isOrigVRMode = false;

// ── Helper: read checkbox ─────────────────────────────────────────────────────
function cb(id) {
    const el = document.getElementById(id);
    return el && el.checked ? '1' : '0';
}

function readNumberInput(valId, sliderId, fallback = '') {
    const valEl = document.getElementById(valId);
    if (valEl && valEl.value !== undefined && valEl.value !== null && String(valEl.value).trim() !== '') {
        return String(valEl.value).trim();
    }
    const sliderEl = document.getElementById(sliderId);
    if (sliderEl && sliderEl.value !== undefined && sliderEl.value !== null && String(sliderEl.value).trim() !== '') {
        return String(sliderEl.value).trim();
    }
    return fallback;
}

function setSliderAndInputValue(sliderId, inputValId, val, triggerChange = true) {
    if (val === undefined || val === null) return;
    const s = document.getElementById(sliderId);
    const v = document.getElementById(inputValId);
    if (s) s.value = val;
    if (v) v.value = val;
    if (s) {
        s.dispatchEvent(new Event('input', { bubbles: true }));
        if (triggerChange) s.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (v) {
        v.value = val;
        v.dispatchEvent(new Event('input', { bubbles: true }));
        if (triggerChange && !s) v.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

function setElementValue(id, val, triggerEvents = true) {
    const el = document.getElementById(id);
    if (!el || val === undefined || val === null) return;
    if (el.type === 'checkbox') {
        el.checked = Boolean(val === true || val === '1' || val === 1 || val === 'true');
    } else {
        el.value = val;
    }
    if (triggerEvents) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

function syncRawRotationPreset(val) {
    const rawRotationPreset = document.getElementById('raw_rotation_preset');
    if (!rawRotationPreset) return;
    const num = parseFloat(val);
    if (isNaN(num)) return;
    if (Math.abs(num - 0) < 0.01) {
        rawRotationPreset.value = "0";
    } else if (Math.abs(num - 90) < 0.01) {
        rawRotationPreset.value = "90";
    } else if (Math.abs(num - 180) < 0.01 || Math.abs(num + 180) < 0.01) {
        rawRotationPreset.value = "180";
    } else if (Math.abs(num - 270) < 0.01 || Math.abs(num + 90) < 0.01) {
        rawRotationPreset.value = "270";
    } else {
        rawRotationPreset.value = "custom";
    }
}

function updateNadirAlert() {
    const nadirEnabled = document.getElementById('nadir_enabled');
    const nadirLogo = document.getElementById('nadir_logo');
    const nadirAlert = document.getElementById('nadir_alert');
    const nadirVal = document.getElementById('nadir_logo_val');

    const isChecked = Boolean(nadirEnabled && nadirEnabled.checked);
    const val = nadirLogo ? (nadirLogo.value || '').trim() : '';
    const hasFile = Boolean(val.length > 0);

    if (nadirVal) {
        nadirVal.textContent = hasFile ? nadirLogo.value : 'None';
    }

    if (nadirAlert) {
        nadirAlert.style.display = (isChecked && !hasFile) ? 'block' : 'none';
    }
}

// ── Viewport Loading Mask & Loader Management ─────────────────────────────────
function showViewportLoader(loaderId, text, subtext = '') {
    const el = document.getElementById(loaderId);
    if (!el) return;
    if (text) {
        const textEl = el.querySelector('.loader-text');
        if (textEl) textEl.textContent = text;
    }
    let subEl = el.querySelector('.loader-subtext');
    if (!subEl) {
        subEl = document.createElement('div');
        subEl.className = 'loader-subtext';
        el.appendChild(subEl);
    }
    subEl.textContent = subtext || '';
    subEl.style.display = subtext ? 'block' : 'none';
    el.classList.remove('hidden');
}

function hideViewportLoader(loaderId) {
    const el = document.getElementById(loaderId);
    if (!el) return;
    el.classList.add('hidden');
}

function attachVideoLoaderEvents(videoEl, loaderId, defaultText = 'Loading Video...') {
    if (!videoEl) return;
    let loadTimer = null;

    const stopLoading = () => {
        if (loadTimer) { clearTimeout(loadTimer); loadTimer = null; }
        hideViewportLoader(loaderId);
    };

    const startLoading = (msg) => {
        if (!videoEl.src || videoEl.src === window.location.href) return;
        if (loadTimer) clearTimeout(loadTimer);
        loadTimer = setTimeout(() => {
            showViewportLoader(loaderId, msg || defaultText);
        }, 80);
    };

    videoEl.addEventListener('loadstart', () => startLoading('Loading Video Stream...'));
    videoEl.addEventListener('waiting', () => startLoading('Buffering Video...'));
    videoEl.addEventListener('seeking', () => startLoading('Seeking Frame...'));
    videoEl.addEventListener('canplay', stopLoading);
    videoEl.addEventListener('canplaythrough', stopLoading);
    videoEl.addEventListener('loadeddata', stopLoading);
    videoEl.addEventListener('playing', stopLoading);
    videoEl.addEventListener('seeked', stopLoading);
    videoEl.addEventListener('timeupdate', stopLoading);
    videoEl.addEventListener('error', (e) => {
        stopLoading();
        console.warn(`Video load error on #${videoEl.id}:`, videoEl.error);
    });
}

// ── Pipeline Config (Save & Load JSON for Sections 1, 2, 3, 4, 5) ────────────
function collectPipelineConfig() {
    const modeRadio = document.querySelector('input[name="streetview_mode"]:checked');
    const modeVal = modeRadio ? modeRadio.value : 'A';

    return {
        type: 'pipeline_config',
        version: 1,
        // Section 1: Stitching
        enable_stitching: document.getElementById('enable_stitching')?.checked ?? true,

        ih_fov: document.getElementById('ih_fov_val')?.value ?? document.getElementById('ih_fov')?.value ?? '190.00',
        iv_fov: document.getElementById('iv_fov_val')?.value ?? document.getElementById('iv_fov')?.value ?? '190.00',
        raw_rotation: document.getElementById('raw_rotation_val')?.value ?? document.getElementById('raw_rotation')?.value ?? '0',
        left_y_offset: document.getElementById('left_y_offset_val')?.value ?? document.getElementById('left_y_offset')?.value ?? '0.00',
        rear_roll_offset: document.getElementById('rear_roll_offset_val')?.value ?? document.getElementById('rear_roll_offset')?.value ?? '0.00',
        yaw: document.getElementById('yaw_val')?.value ?? document.getElementById('yaw')?.value ?? '-25',
        pitch: document.getElementById('pitch_val')?.value ?? document.getElementById('pitch')?.value ?? '-10',
        roll: document.getElementById('roll_val')?.value ?? document.getElementById('roll')?.value ?? '5',
        blend_seams: document.getElementById('blend_seams')?.checked ?? true,
        blend_width: document.getElementById('blend_width_val')?.value ?? document.getElementById('blend_width')?.value ?? '58',
        anti_vignette: document.getElementById('anti_vignette')?.checked ?? true,
        anti_vignette_angle: document.getElementById('anti_vignette_angle_val')?.value ?? document.getElementById('anti_vignette_angle')?.value ?? '0.5',

        // Section 2: Equirectangular Stabilization
        stabilize: document.getElementById('stabilize')?.checked ?? true,
        stab_telemetry: document.getElementById('stab_telemetry')?.checked ?? true,
        stab_kopf: document.getElementById('stab_kopf')?.checked ?? true,
        stab_kabsch: document.getElementById('stab_kabsch')?.checked ?? true,
        stab_vidstab: document.getElementById('stab_vidstab')?.checked ?? true,
        stab_cinematic: document.getElementById('stab_cinematic')?.checked ?? true,
        stab_horizon: document.getElementById('stab_horizon')?.checked ?? false,
        stab_traveldir: document.getElementById('stab_traveldir')?.checked ?? false,
        horizon_autodetect: document.getElementById('horizon_autodetect')?.checked ?? false,
        horizon_autodetect_source: document.getElementById('horizon_autodetect_source')?.value ?? 'vision',
        horizon_autodetect_density: document.getElementById('horizon_autodetect_density')?.value ?? 'ultra_dense',
        horizon_pitch_prominence: parseFloat(document.getElementById('horizon_pitch_prominence')?.value) || 0.8,
        horizon_roll_damping: parseFloat(document.getElementById('horizon_roll_damping')?.value) || 0.70,
        horizon_apply_yaw: document.getElementById('horizon_apply_yaw')?.checked ?? false,
        stab_quality_mode: document.getElementById('stab_quality_mode')?.value ?? '4',
        prompt_transforms: document.getElementById('prompt_transforms')?.checked ?? true,
        fallback_unstabilized: document.getElementById('fallback_unstabilized')?.checked ?? true,
        stab_order: getOrderedStabMethods(),
        stabilize_methods: getOrderedStabMethods().filter(m => document.getElementById(`stab_${m}`)?.checked).join(','),

        kabsch_smoothing: document.getElementById('kabsch_smoothing_val')?.value ?? document.getElementById('kabsch_smoothing')?.value ?? '5',
        kopf_keyframe_sec: document.getElementById('kopf_keyframe_sec_val')?.value ?? document.getElementById('kopf_keyframe_sec')?.value ?? '2.0',
        kopf_cube_face: document.getElementById('kopf_cube_face_val')?.value ?? document.getElementById('kopf_cube_face')?.value ?? '1024',
        kopf_max_features: document.getElementById('kopf_max_features_val')?.value ?? document.getElementById('kopf_max_features')?.value ?? '400',
        kopf_deformed: document.getElementById('kopf_deformed')?.checked ?? true,
        kopf_reapply: document.getElementById('kopf_reapply')?.checked ?? false,
        vidstab_visual: document.getElementById('vidstab_visual')?.checked ?? false,

        telemetry_source: document.getElementById('telemetry_source')?.value ?? 'auto',
        telemetry_mode: document.getElementById('telemetry_mode')?.value ?? 'smooth',
        telemetry_fusion: document.getElementById('telemetry_fusion')?.value ?? 'mahony',
        telemetry_fusion_gain: document.getElementById('telemetry_fusion_gain_val')?.value ?? document.getElementById('telemetry_fusion_gain')?.value ?? '0.51',
        telemetry_ref_frame: document.getElementById('telemetry_ref_frame_val')?.value ?? document.getElementById('telemetry_ref_frame')?.value ?? '0',
        telemetry_smoothing: document.getElementById('telemetry_smoothing_val')?.value ?? document.getElementById('telemetry_smoothing')?.value ?? '30',
        telemetry_multiplier_roll: document.getElementById('telemetry_multiplier_roll_val')?.value ?? document.getElementById('telemetry_multiplier_roll')?.value ?? '1.0',
        telemetry_multiplier_pitch: document.getElementById('telemetry_multiplier_pitch_val')?.value ?? document.getElementById('telemetry_multiplier_pitch')?.value ?? '1.0',
        telemetry_multiplier_yaw: document.getElementById('telemetry_multiplier_yaw_val')?.value ?? document.getElementById('telemetry_multiplier_yaw')?.value ?? '1.0',

        l1_lambda_acc: document.getElementById('l1_lambda_acc_val')?.value ?? document.getElementById('l1_lambda_acc')?.value ?? '20.0',
        l1_lambda_vel: document.getElementById('l1_lambda_vel_val')?.value ?? document.getElementById('l1_lambda_vel')?.value ?? '2.0',
        cinematic_window: document.getElementById('cinematic_window_val')?.value ?? document.getElementById('cinematic_window')?.value ?? '45',

        traveldir_mode: document.getElementById('traveldir_mode')?.value ?? 'travel_direction',
        traveldir_target_yaw: document.getElementById('traveldir_target_yaw_val')?.value ?? document.getElementById('traveldir_target_yaw')?.value ?? '0.0',
        traveldir_damping: document.getElementById('traveldir_damping_val')?.value ?? document.getElementById('traveldir_damping')?.value ?? '0.90',
        traveldir_deadband: document.getElementById('traveldir_deadband_val')?.value ?? document.getElementById('traveldir_deadband')?.value ?? '1.5',

        vidstab_smoothing: document.getElementById('vidstab_smoothing_val')?.value ?? document.getElementById('vidstab_smoothing')?.value ?? '2',
        vidstab_shakiness: document.getElementById('vidstab_shakiness_val')?.value ?? document.getElementById('vidstab_shakiness')?.value ?? '1',
        vidstab_optalgo: document.getElementById('vidstab_optalgo')?.value ?? 'gauss',
        vidstab_tripod: document.getElementById('vidstab_tripod')?.checked ?? false,

        // Section 3: Add Nadir Logo Overlay
        nadir_enabled: document.getElementById('nadir_enabled')?.checked ?? false,
        nadir_logo: document.getElementById('nadir_logo')?.value ?? 'logo_stei_circle.png',
        nadir_fov: document.getElementById('nadir_fov_val')?.value ?? document.getElementById('nadir_fov')?.value ?? '75',
        nadir_fov_v: document.getElementById('nadir_fov_v_val')?.value ?? document.getElementById('nadir_fov_v')?.value ?? '75',

        // Section 4: Google Street View Export
        streetview_enabled: document.getElementById('streetview_enabled')?.checked ?? false,
        streetview_mode: modeVal,
        streetview_checkpoints: document.getElementById('streetview_checkpoints')?.value ?? '',
        streetview_gpx_path: document.getElementById('streetview_gpx_path')?.value ?? '',
        streetview_start_coord: document.getElementById('streetview_start_coord')?.value?.trim() ?? '',
        streetview_end_coord: document.getElementById('streetview_end_coord')?.value?.trim() ?? '',
        streetview_smooth_gps: document.getElementById('streetview_smooth_gps')?.checked ?? true,
        streetview_time_offset: document.getElementById('streetview_time_offset')?.value ?? '0',
        streetview_start_time: document.getElementById('streetview_start_time')?.value ?? '',
        streetview_auto_pad: document.getElementById('streetview_auto_pad')?.checked ?? true,
        streetview_bitrate: document.getElementById('streetview_bitrate')?.value ?? '45M',
        streetview_strip_audio: document.getElementById('streetview_strip_audio')?.checked ?? true,

        // Metadata Injection
        inject_intermediate_meta: document.getElementById('inject_intermediate_meta')?.checked ?? true,
        inject_final_meta: document.getElementById('inject_final_meta')?.checked ?? true,

        // Output & Encoding Options
        ffmpeg_preset: document.getElementById('ffmpeg_preset')?.value ?? 'medium',
        ffmpeg_crf: document.getElementById('ffmpeg_crf')?.value ?? '18',
        ffmpeg_hwaccel: document.getElementById('ffmpeg_hwaccel')?.checked ?? true,
        v360_backend: document.getElementById('v360_vulkan')?.checked ? 'vulkan' : 'cpu',
        v360_vulkan: document.getElementById('v360_vulkan')?.checked ?? false,
        video_bitrate: (() => {
            const vb = document.getElementById('video_bitrate')?.value ?? '80M';
            const vbc = document.getElementById('video_bitrate_custom')?.value?.trim();
            return (vb === 'custom' && vbc) ? `${vbc}M` : vb;
        })(),
        remove_audio: document.getElementById('remove_audio')?.checked ?? false,

        // Section 5: External Utils
        util_gcsv: document.getElementById('util_gcsv')?.checked ?? false,
        util_bigsh0t: document.getElementById('util_bigsh0t')?.checked ?? false
    };
}

function showConfigStatusBadge(msg, isError = false) {
    const badge = document.getElementById('config_status_badge');
    if (!badge) return;
    badge.textContent = msg;
    badge.style.color = isError ? '#f87171' : '#34d399';
    badge.style.borderColor = isError ? 'rgba(239,68,68,0.3)' : 'rgba(16,185,129,0.3)';
    badge.style.background = isError ? 'rgba(239,68,68,0.15)' : 'rgba(16,185,129,0.15)';
    badge.style.display = 'inline-block';
    setTimeout(() => { badge.style.display = 'none'; }, 3500);
}

let currentLoadedPresetName = 'none';

function updateLoadedPresetDisplay(name) {
    if (!name || typeof name !== 'string') {
        currentLoadedPresetName = 'none';
    } else {
        const clean = name.trim().replace(/^.*[\\\/]/, '');
        currentLoadedPresetName = clean || 'none';
    }
    const el = document.getElementById('current-loaded-preset');
    if (el) {
        el.textContent = currentLoadedPresetName;
        if (currentLoadedPresetName !== 'none') {
            el.style.color = '#38bdf8';
            el.style.fontWeight = '600';
            el.title = currentLoadedPresetName;
        } else {
            el.style.color = '#94a3b8';
            el.style.fontWeight = 'normal';
            el.removeAttribute('title');
        }
    }
}

function applyPipelineConfig(cfg, showToastName = '') {
    if (!cfg || typeof cfg !== 'object') return;
    const d = cfg.config || cfg.data || cfg;

    isApplyingConfig = true;
    try {
    // Section 1
    if (d.enable_stitching !== undefined) setElementValue('enable_stitching', d.enable_stitching);

    if (d.ih_fov !== undefined) setSliderAndInputValue('ih_fov', 'ih_fov_val', d.ih_fov);
    if (d.iv_fov !== undefined) setSliderAndInputValue('iv_fov', 'iv_fov_val', d.iv_fov);
    if (d.raw_rotation !== undefined) {
        const rawValInput = document.getElementById('raw_rotation_val');
        if (rawValInput) rawValInput.value = d.raw_rotation;
        syncRawRotationPreset(d.raw_rotation);
    }
    if (d.left_y_offset !== undefined) setSliderAndInputValue('left_y_offset', 'left_y_offset_val', d.left_y_offset);
    if (d.rear_roll_offset !== undefined) setSliderAndInputValue('rear_roll_offset', 'rear_roll_offset_val', d.rear_roll_offset);
    if (d.yaw !== undefined) setSliderAndInputValue('yaw', 'yaw_val', d.yaw);
    if (d.pitch !== undefined) setSliderAndInputValue('pitch', 'pitch_val', d.pitch);
    if (d.roll !== undefined) setSliderAndInputValue('roll', 'roll_val', d.roll);
    if (d.blend_seams !== undefined) setElementValue('blend_seams', d.blend_seams);
    if (d.blend_width !== undefined) setSliderAndInputValue('blend_width', 'blend_width_val', d.blend_width);
    if (d.anti_vignette !== undefined) setElementValue('anti_vignette', d.anti_vignette);
    if (d.anti_vignette_angle !== undefined) setSliderAndInputValue('anti_vignette_angle', 'anti_vignette_angle_val', d.anti_vignette_angle);

    // Section 2
    if (d.stab_telemetry !== undefined) setElementValue('stab_telemetry', d.stab_telemetry);
    if (d.stab_kopf !== undefined) setElementValue('stab_kopf', d.stab_kopf);
    if (d.stab_kabsch !== undefined) setElementValue('stab_kabsch', d.stab_kabsch);
    if (d.stab_vidstab !== undefined) setElementValue('stab_vidstab', d.stab_vidstab);
    if (d.stab_cinematic !== undefined) setElementValue('stab_cinematic', d.stab_cinematic);
    if (d.stab_horizon !== undefined) setElementValue('stab_horizon', d.stab_horizon);
    if (d.stab_traveldir !== undefined) setElementValue('stab_traveldir', d.stab_traveldir);
    if (d.stabilize !== undefined) setElementValue('stabilize', d.stabilize);
    if (d.horizon_autodetect !== undefined) setElementValue('horizon_autodetect', d.horizon_autodetect);
    if (d.horizon_autodetect_source !== undefined) setElementValue('horizon_autodetect_source', d.horizon_autodetect_source);
    if (d.horizon_autodetect_density !== undefined) setElementValue('horizon_autodetect_density', d.horizon_autodetect_density);
    if (d.horizon_pitch_prominence !== undefined) {
        setElementValue('horizon_pitch_prominence', d.horizon_pitch_prominence);
        setElementValue('rng_horizon_pitch_prominence', d.horizon_pitch_prominence);
        const lbl = document.getElementById('lbl_horizon_pitch_prominence');
        if (lbl) lbl.textContent = parseFloat(d.horizon_pitch_prominence).toFixed(1) + '°';
    }
    if (d.horizon_roll_damping !== undefined) {
        setElementValue('horizon_roll_damping', d.horizon_roll_damping);
        setElementValue('rng_horizon_roll_damping', d.horizon_roll_damping);
        const lblR = document.getElementById('lbl_horizon_roll_damping');
        if (lblR) lblR.textContent = parseFloat(d.horizon_roll_damping).toFixed(2) + '×';
    }
    if (d.horizon_apply_yaw !== undefined) setElementValue('horizon_apply_yaw', d.horizon_apply_yaw);
    if (d.stab_quality_mode !== undefined) setElementValue('stab_quality_mode', d.stab_quality_mode);
    if (d.prompt_transforms !== undefined) setElementValue('prompt_transforms', d.prompt_transforms);
    if (d.fallback_unstabilized !== undefined) setElementValue('fallback_unstabilized', d.fallback_unstabilized);
    const incomingOrder = d.stab_order || d.stabilize_methods || d.stab_methods;
    if (incomingOrder) {
        loadStabOrder(incomingOrder);
    }

    if (d.kabsch_smoothing !== undefined) setSliderAndInputValue('kabsch_smoothing', 'kabsch_smoothing_val', d.kabsch_smoothing);
    if (d.kopf_keyframe_sec !== undefined) setSliderAndInputValue('kopf_keyframe_sec', 'kopf_keyframe_sec_val', d.kopf_keyframe_sec);
    if (d.kopf_cube_face !== undefined) setSliderAndInputValue('kopf_cube_face', 'kopf_cube_face_val', d.kopf_cube_face);
    if (d.kopf_max_features !== undefined) setSliderAndInputValue('kopf_max_features', 'kopf_max_features_val', d.kopf_max_features);
    if (d.kopf_deformed !== undefined) setElementValue('kopf_deformed', d.kopf_deformed);
    if (d.kopf_reapply !== undefined) setElementValue('kopf_reapply', d.kopf_reapply);
    if (d.vidstab_visual !== undefined) setElementValue('vidstab_visual', d.vidstab_visual);

    if (d.telemetry_source !== undefined) setElementValue('telemetry_source', d.telemetry_source);
    if (d.telemetry_mode !== undefined) setElementValue('telemetry_mode', d.telemetry_mode);
    if (d.telemetry_fusion !== undefined) {
        setElementValue('telemetry_fusion', d.telemetry_fusion);
        if (typeof updateTelemetryFusionUI === 'function') updateTelemetryFusionUI();
    }
    if (d.telemetry_fusion_gain !== undefined) setSliderAndInputValue('telemetry_fusion_gain', 'telemetry_fusion_gain_val', d.telemetry_fusion_gain);
    if (d.telemetry_ref_frame !== undefined) setSliderAndInputValue('telemetry_ref_frame', 'telemetry_ref_frame_val', d.telemetry_ref_frame);
    if (d.telemetry_smoothing !== undefined) setSliderAndInputValue('telemetry_smoothing', 'telemetry_smoothing_val', d.telemetry_smoothing);
    if (d.telemetry_multiplier_roll !== undefined) setSliderAndInputValue('telemetry_multiplier_roll', 'telemetry_multiplier_roll_val', d.telemetry_multiplier_roll);
    if (d.telemetry_multiplier_pitch !== undefined) setSliderAndInputValue('telemetry_multiplier_pitch', 'telemetry_multiplier_pitch_val', d.telemetry_multiplier_pitch);
    if (d.telemetry_multiplier_yaw !== undefined) setSliderAndInputValue('telemetry_multiplier_yaw', 'telemetry_multiplier_yaw_val', d.telemetry_multiplier_yaw);

    if (d.l1_lambda_acc !== undefined) setSliderAndInputValue('l1_lambda_acc', 'l1_lambda_acc_val', d.l1_lambda_acc);
    if (d.l1_lambda_vel !== undefined) setSliderAndInputValue('l1_lambda_vel', 'l1_lambda_vel_val', d.l1_lambda_vel);
    if (d.cinematic_window !== undefined) setSliderAndInputValue('cinematic_window', 'cinematic_window_val', d.cinematic_window);

    if (d.traveldir_mode !== undefined) setElementValue('traveldir_mode', d.traveldir_mode);
    if (d.traveldir_target_yaw !== undefined) setSliderAndInputValue('traveldir_target_yaw', 'traveldir_target_yaw_val', d.traveldir_target_yaw);
    if (d.traveldir_damping !== undefined) setSliderAndInputValue('traveldir_damping', 'traveldir_damping_val', d.traveldir_damping);
    if (d.traveldir_deadband !== undefined) setSliderAndInputValue('traveldir_deadband', 'traveldir_deadband_val', d.traveldir_deadband);

    if (d.vidstab_smoothing !== undefined) setSliderAndInputValue('vidstab_smoothing', 'vidstab_smoothing_val', d.vidstab_smoothing);
    if (d.vidstab_shakiness !== undefined) setSliderAndInputValue('vidstab_shakiness', 'vidstab_shakiness_val', d.vidstab_shakiness);
    if (d.vidstab_optalgo !== undefined) setElementValue('vidstab_optalgo', d.vidstab_optalgo);
    if (d.vidstab_tripod !== undefined) setElementValue('vidstab_tripod', d.vidstab_tripod);

    // Section 3
    if (d.nadir_enabled !== undefined) setElementValue('nadir_enabled', d.nadir_enabled);
    if (d.nadir_logo !== undefined) {
        setElementValue('nadir_logo', d.nadir_logo);
        const nadirVal = document.getElementById('nadir_logo_val');
        if (nadirVal) nadirVal.textContent = d.nadir_logo;
    }
    if (d.nadir_fov !== undefined) setSliderAndInputValue('nadir_fov', 'nadir_fov_val', d.nadir_fov);
    if (d.nadir_fov_v !== undefined) setSliderAndInputValue('nadir_fov_v', 'nadir_fov_v_val', d.nadir_fov_v);
    updateNadirAlert();

    // Section 4
    if (d.streetview_enabled !== undefined) setElementValue('streetview_enabled', d.streetview_enabled);
    if (d.streetview_mode !== undefined) {
        const radio = document.querySelector(`input[name="streetview_mode"][value="${d.streetview_mode}"]`);
        if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
    if (d.streetview_checkpoints !== undefined) setElementValue('streetview_checkpoints', d.streetview_checkpoints);
    if (d.streetview_gpx_path !== undefined) setElementValue('streetview_gpx_path', d.streetview_gpx_path);
    if (d.streetview_start_coord !== undefined) setElementValue('streetview_start_coord', d.streetview_start_coord);
    if (d.streetview_end_coord !== undefined) setElementValue('streetview_end_coord', d.streetview_end_coord);
    if (d.streetview_smooth_gps !== undefined) setElementValue('streetview_smooth_gps', d.streetview_smooth_gps);
    if (d.streetview_time_offset !== undefined) setElementValue('streetview_time_offset', d.streetview_time_offset);
    if (d.streetview_start_time !== undefined) setElementValue('streetview_start_time', d.streetview_start_time);
    if (d.streetview_auto_pad !== undefined) setElementValue('streetview_auto_pad', d.streetview_auto_pad);
    if (d.streetview_bitrate !== undefined) setElementValue('streetview_bitrate', d.streetview_bitrate);
    if (d.streetview_strip_audio !== undefined) setElementValue('streetview_strip_audio', d.streetview_strip_audio);

    // Metadata Injection
    if (d.inject_intermediate_meta !== undefined) setElementValue('inject_intermediate_meta', d.inject_intermediate_meta);
    if (d.inject_final_meta !== undefined) setElementValue('inject_final_meta', d.inject_final_meta);

    // Output & Encoding Options
    if (d.ffmpeg_preset !== undefined) setElementValue('ffmpeg_preset', d.ffmpeg_preset);
    if (d.ffmpeg_crf !== undefined) setElementValue('ffmpeg_crf', d.ffmpeg_crf);
    if (d.ffmpeg_hwaccel !== undefined) setElementValue('ffmpeg_hwaccel', d.ffmpeg_hwaccel);
    if (d.v360_backend !== undefined) setElementValue('v360_vulkan', d.v360_backend === 'vulkan');
    else if (d.v360_vulkan !== undefined) setElementValue('v360_vulkan', d.v360_vulkan);
    if (d.video_bitrate !== undefined) {
        const vbSel = document.getElementById('video_bitrate');
        const vbWrap = document.getElementById('video_bitrate_custom_wrap');
        const vbCustom = document.getElementById('video_bitrate_custom');
        if (vbSel) {
            const matchOpt = Array.from(vbSel.options).find(o => o.value === d.video_bitrate);
            if (matchOpt) {
                vbSel.value = d.video_bitrate;
                if (vbWrap) vbWrap.style.display = 'none';
            } else {
                vbSel.value = 'custom';
                if (vbWrap) vbWrap.style.display = 'block';
                if (vbCustom) vbCustom.value = String(d.video_bitrate).replace(/M$/i, '');
            }
        }
    }
    if (d.remove_audio !== undefined) setElementValue('remove_audio', d.remove_audio);

    // Section 5: External Utils
    if (d.util_gcsv !== undefined) setElementValue('util_gcsv', d.util_gcsv);
    if (d.util_bigsh0t !== undefined) setElementValue('util_bigsh0t', d.util_bigsh0t);

    if (typeof updateStabPanels === 'function') updateStabPanels();
    if (typeof updateQualityModeTooltip === 'function') updateQualityModeTooltip();
    if (typeof updateStreetViewVisibility === 'function') updateStreetViewVisibility();
    if (typeof updateStitchingVisibility === 'function') updateStitchingVisibility();
    if (typeof updateStabVisibility === 'function') updateStabVisibility();

    showConfigStatusBadge(showToastName ? `Loaded: ${showToastName}` : 'Config Loaded!');
    if (showToastName && showToastName.toLowerCase().endsWith('.json')) {
        updateLoadedPresetDisplay(showToastName);
    }
    } finally {
        isApplyingConfig = false;
    }

    const inputVal = (options && options.input && options.input.value) || (inputSelect && inputSelect.value) || '';
    if (inputVal.trim()) {
        generatePreview();
    }
}

async function loadServerPresetsDropdown() {
    const select = document.getElementById('server_preset_select');
    if (!select) return;
    try {
        const res = await fetch('api/v1/presets');
        const data = await res.json();
        if (data && data.success && Array.isArray(data.presets)) {
            select.innerHTML = '<option value="">-- Or select saved preset from server --</option>';
            const sorted = data.presets.slice().sort((a, b) => (b.is_pipeline ? 1 : 0) - (a.is_pipeline ? 1 : 0));
            sorted.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.filename;
                opt.textContent = (p.is_pipeline ? '⚙ ' : '') + p.filename;
                select.appendChild(opt);
            });
        }
    } catch (e) {
        console.warn('Could not load presets list from server:', e);
    }
}

function initPipelineConfigManager() {
    const btnSaveConfig = document.getElementById('btn-save-config');
    const btnLoadConfig = document.getElementById('btn-load-config');
    const fileInput = document.getElementById('config_file_input');
    const serverPresetSelect = document.getElementById('server_preset_select');
    const btnLoadServerPreset = document.getElementById('btn-load-server-preset');

    // Save Preset Modal Elements
    const saveModal = document.getElementById('modal-save-config');
    const saveFilenameInput = document.getElementById('save-config-filename-input');
    const saveSearchInput = document.getElementById('save-config-search-input');
    const savePresetsList = document.getElementById('save-config-presets-list');
    const savePresetsCount = document.getElementById('save-config-presets-count');
    const saveOverwriteWarning = document.getElementById('save-config-overwrite-warning');
    const btnConfirmSave = document.getElementById('btn-confirm-save-config');
    const btnCancelSave = document.getElementById('btn-cancel-save-config');
    const btnCancelSaveX = document.getElementById('btn-cancel-save-config-x');

    let cachedServerPresets = [];

    function closeSaveModal() {
        if (saveModal) saveModal.style.display = 'none';
    }

    function updateSaveOverwriteState() {
        if (!saveFilenameInput || !saveOverwriteWarning || !btnConfirmSave) return;
        const val = saveFilenameInput.value.trim();
        if (!val) {
            saveOverwriteWarning.style.display = 'none';
            btnConfirmSave.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle; margin-right:4px;"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg> Save Preset';
            btnConfirmSave.style.background = 'linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%)';
            return;
        }

        let safe = val.replace(/\\/g, '/').split('/').pop();
        if (!safe.toLowerCase().endsWith('.json')) safe += '.json';

        const exists = cachedServerPresets.some(p => (p.filename || '').toLowerCase() === safe.toLowerCase());

        // Update list items active/selected class
        if (savePresetsList) {
            const items = savePresetsList.querySelectorAll('.save-preset-item');
            items.forEach(el => {
                const fname = el.getAttribute('data-filename');
                if (fname && fname.toLowerCase() === safe.toLowerCase()) {
                    el.style.background = 'rgba(59, 130, 246, 0.25)';
                    el.style.borderColor = 'rgba(96, 165, 250, 0.5)';
                } else {
                    el.style.background = 'rgba(255, 255, 255, 0.03)';
                    el.style.borderColor = 'rgba(255, 255, 255, 0.06)';
                }
            });
        }

        if (exists) {
            saveOverwriteWarning.style.display = 'inline-block';
            saveOverwriteWarning.textContent = 'File already exists — will overwrite';
            btnConfirmSave.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle; margin-right:4px;"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg> Overwrite Preset';
            btnConfirmSave.style.background = 'linear-gradient(135deg, #ea580c 0%, #c2410c 100%)';
        } else {
            saveOverwriteWarning.style.display = 'none';
            btnConfirmSave.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle; margin-right:4px;"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg> Save Preset';
            btnConfirmSave.style.background = 'linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%)';
        }
    }

    function renderSavePresetsList(filterText = '') {
        if (!savePresetsList) return;
        const q = filterText.trim().toLowerCase();
        const filtered = cachedServerPresets.filter(p => !q || (p.filename || '').toLowerCase().includes(q));

        if (savePresetsCount) {
            savePresetsCount.textContent = `${cachedServerPresets.length} preset${cachedServerPresets.length === 1 ? '' : 's'}`;
        }

        if (filtered.length === 0) {
            savePresetsList.innerHTML = `<div style="padding: 12px; font-size: 11px; color: #94a3b8; text-align: center;">${q ? 'No matching presets found' : 'No presets found on server'}</div>`;
            return;
        }

        savePresetsList.innerHTML = '';
        const curVal = saveFilenameInput ? saveFilenameInput.value.trim().toLowerCase() : '';
        const curSafe = curVal ? (curVal.endsWith('.json') ? curVal : curVal + '.json') : '';

        filtered.forEach(p => {
            const isSelected = curSafe && (p.filename || '').toLowerCase() === curSafe;
            const item = document.createElement('div');
            item.className = 'save-preset-item';
            item.setAttribute('data-filename', p.filename);
            item.style.cssText = `display: flex; align-items: center; justify-content: space-between; padding: 6px 10px; border-radius: 6px; cursor: pointer; transition: all 0.15s ease; border: 1px solid ${isSelected ? 'rgba(96, 165, 250, 0.5)' : 'rgba(255, 255, 255, 0.06)'}; background: ${isSelected ? 'rgba(59, 130, 246, 0.25)' : 'rgba(255, 255, 255, 0.03)'};`;

            const left = document.createElement('div');
            left.style.cssText = 'display: flex; align-items: center; gap: 8px; overflow: hidden;';

            const icon = document.createElement('span');
            icon.innerHTML = p.is_pipeline ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>' : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>';
            icon.style.fontSize = '12px';
            icon.style.display = 'inline-flex';
            icon.style.alignItems = 'center';

            const name = document.createElement('span');
            name.textContent = p.filename;
            name.style.cssText = 'font-size: 12px; font-family: monospace; color: #e2e8f0; white-space: nowrap; text-overflow: ellipsis; overflow: hidden; font-weight: 500;';

            left.appendChild(icon);
            left.appendChild(name);

            const right = document.createElement('div');
            right.style.cssText = 'display: flex; align-items: center; gap: 6px; flex-shrink: 0;';

            if (p.is_pipeline) {
                const tag = document.createElement('span');
                tag.textContent = 'Pipeline';
                tag.style.cssText = 'font-size: 10px; padding: 1px 5px; border-radius: 3px; background: rgba(139, 92, 246, 0.25); color: #c4b5fd; border: 1px solid rgba(139, 92, 246, 0.35);';
                right.appendChild(tag);
            }

            item.appendChild(left);
            item.appendChild(right);

            item.addEventListener('mouseenter', () => {
                const activeVal = saveFilenameInput ? saveFilenameInput.value.trim().toLowerCase() : '';
                const activeSafe = activeVal ? (activeVal.endsWith('.json') ? activeVal : activeVal + '.json') : '';
                if ((p.filename || '').toLowerCase() !== activeSafe) {
                    item.style.background = 'rgba(255, 255, 255, 0.08)';
                }
            });
            item.addEventListener('mouseleave', () => {
                const activeVal = saveFilenameInput ? saveFilenameInput.value.trim().toLowerCase() : '';
                const activeSafe = activeVal ? (activeVal.endsWith('.json') ? activeVal : activeVal + '.json') : '';
                if ((p.filename || '').toLowerCase() !== activeSafe) {
                    item.style.background = 'rgba(255, 255, 255, 0.03)';
                }
            });

            item.addEventListener('click', () => {
                if (saveFilenameInput) {
                    saveFilenameInput.value = p.filename;
                    updateSaveOverwriteState();
                }
            });

            savePresetsList.appendChild(item);
        });
    }

    async function openSavePresetModal() {
        if (!saveModal) return;

        let defaultName = 'config.json';
        if (options.input && options.input.value) {
            const rawVal = options.input.value.replace(/\\/g, '/');
            const base = rawVal.split('/').pop().replace(/\.[^/.]+$/, '');
            if (base) {
                defaultName = `${base}.json`;
            }
        }

        if (saveFilenameInput) {
            saveFilenameInput.value = defaultName;
        }
        if (saveSearchInput) {
            saveSearchInput.value = '';
        }

        saveModal.style.display = 'flex';

        try {
            const res = await fetch('api/v1/presets');
            const data = await res.json();
            if (data && data.success && Array.isArray(data.presets)) {
                cachedServerPresets = data.presets.slice().sort((a, b) => (b.is_pipeline ? 1 : 0) - (a.is_pipeline ? 1 : 0));
            } else {
                cachedServerPresets = [];
            }
        } catch (e) {
            console.warn('Could not load presets list:', e);
            cachedServerPresets = [];
        }

        renderSavePresetsList('');
        updateSaveOverwriteState();

        setTimeout(() => {
            if (saveFilenameInput) {
                saveFilenameInput.focus();
                saveFilenameInput.select();
            }
        }, 50);
    }

    async function executeSavePreset() {
        if (!saveFilenameInput) return;
        const rawFilename = saveFilenameInput.value.trim();
        if (!rawFilename) {
            saveFilenameInput.focus();
            return;
        }

        let safeFilename = rawFilename.replace(/\\/g, '/').split('/').pop();
        if (!safeFilename.toLowerCase().endsWith('.json')) safeFilename += '.json';

        const configData = collectPipelineConfig();

        // 1. Save to server via PHP / Python API
        try {
            const res = await fetch('api/v1/presets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: safeFilename, config: configData })
            });
            const resData = await res.json();
            if (resData && resData.success) {
                const savedName = resData.filename || safeFilename;
                showConfigStatusBadge(`Saved: presets/${savedName}`);
                updateLoadedPresetDisplay(savedName);
                loadServerPresetsDropdown();
            } else {
                showConfigStatusBadge(resData?.error || 'Save failed on server', true);
            }
        } catch (err) {
            console.error('Server save error:', err);
        }

        // 2. Also trigger browser file download for local convenience
        try {
            const blob = new Blob([JSON.stringify(configData, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = safeFilename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } catch (dlErr) {
            console.error('Download error:', dlErr);
        }

        closeSaveModal();
    }

    if (btnSaveConfig) {
        btnSaveConfig.addEventListener('click', () => {
            openSavePresetModal();
        });
    }

    if (btnConfirmSave) {
        btnConfirmSave.addEventListener('click', executeSavePreset);
    }

    if (btnCancelSave) {
        btnCancelSave.addEventListener('click', closeSaveModal);
    }

    if (btnCancelSaveX) {
        btnCancelSaveX.addEventListener('click', closeSaveModal);
    }

    if (saveFilenameInput) {
        saveFilenameInput.addEventListener('input', updateSaveOverwriteState);
        saveFilenameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                executeSavePreset();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                closeSaveModal();
            }
        });
    }

    if (saveSearchInput) {
        saveSearchInput.addEventListener('input', (e) => {
            renderSavePresetsList(e.target.value);
            updateSaveOverwriteState();
        });
    }

    if (saveModal) {
        saveModal.addEventListener('click', (e) => {
            if (e.target === saveModal) {
                closeSaveModal();
            }
        });
    }

    if (btnLoadConfig && fileInput) {
        btnLoadConfig.addEventListener('click', () => {
            fileInput.click();
        });

        fileInput.addEventListener('change', (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (evt) => {
                try {
                    const parsed = JSON.parse(evt.target.result);
                    applyPipelineConfig(parsed, file.name);
                } catch (parseErr) {
                    alert('Invalid JSON file: ' + parseErr.message);
                    showConfigStatusBadge('Invalid JSON file', true);
                }
                fileInput.value = '';
            };
            reader.readAsText(file);
        });
    }

    async function applyServerPreset(filename) {
        if (!filename) return;
        try {
            const res = await fetch(`api/v1/presets/${encodeURIComponent(filename)}`);
            const data = await res.json();
            if (data && data.success && data.data) {
                const displayName = filename.startsWith('presets/') ? filename : `presets/${filename}`;
                applyPipelineConfig(data.data, displayName);
            } else {
                showConfigStatusBadge(data?.error || 'Failed to load preset', true);
            }
        } catch (err) {
            console.error('Server load error:', err);
            showConfigStatusBadge('Error loading preset from server', true);
        }
    }

    if (btnLoadServerPreset && serverPresetSelect) {
        btnLoadServerPreset.addEventListener('click', () => {
            const val = serverPresetSelect.value;
            if (val) applyServerPreset(val);
        });
    }

    if (serverPresetSelect) {
        serverPresetSelect.addEventListener('change', () => {
            const val = serverPresetSelect.value;
            if (val) applyServerPreset(val);
        });
    }

    loadServerPresetsDropdown();
}

// ── 1. Initialize Event Listeners ─────────────────────────────────────────────
function init() {
    // Attach loading masks to all video elements
    attachVideoLoaderEvents(originalVideo, 'orig-loader', 'Loading Raw Video...');
    attachVideoLoaderEvents(videoStitched, 'stitched-loader', 'Loading Stitched Video...');
    attachVideoLoaderEvents(videoTelemetry, 'telemetry-loader', 'Loading Telemetry Video...');
    attachVideoLoaderEvents(videoKopf, 'kopf-loader', 'Loading Kopf Video...');
    attachVideoLoaderEvents(videoKabsch, 'kabsch-loader', 'Loading Kabsch Video...');
    attachVideoLoaderEvents(videoVidstab, 'vidstab-loader', 'Loading Vidstab Video...');
    attachVideoLoaderEvents(videoCinematic, 'cinematic-loader', 'Loading Cinematic Video...');
    attachVideoLoaderEvents(videoHorizon, 'horizon-loader', 'Loading Horizon Video...');
    attachVideoLoaderEvents(videoTraveldir, 'traveldir-loader', 'Loading Travel-Direction Video...');
    attachVideoLoaderEvents(video360, 'vr-loader', 'Loading 360 VR Video...');

    // Main sliders
    Object.keys(sliders).forEach(key => {
        // Sync slider to number input
        sliders[key].addEventListener('input', () => {
            sliderValues[key].value = sliders[key].value;
            if (key === 'ih_fov' || key === 'iv_fov') updateMaxBlendWidth();
        });
        sliders[key].addEventListener('change', () => {
            generatePreview();
        });
        
        // Sync number input to slider
        sliderValues[key].addEventListener('input', () => {
            sliders[key].value = sliderValues[key].value;
            if (key === 'ih_fov' || key === 'iv_fov') updateMaxBlendWidth();
        });
        sliderValues[key].addEventListener('change', () => {
            generatePreview();
        });
    });

    const rawRotationPreset = document.getElementById('raw_rotation_preset');
    if (rawRotationPreset) {
        rawRotationPreset.addEventListener('change', () => {
            const chosen = rawRotationPreset.value;
            if (chosen !== 'custom') {
                const rawValInput = document.getElementById('raw_rotation_val');
                if (rawValInput) rawValInput.value = chosen;
                generatePreview();
            }
        });
    }

    const rawRotValInput = document.getElementById('raw_rotation_val');
    if (rawRotValInput) {
        rawRotValInput.addEventListener('input', () => {
            syncRawRotationPreset(rawRotValInput.value);
        });
        rawRotValInput.addEventListener('change', () => {
            generatePreview();
        });
    }

    function updateMaxBlendWidth() {
        const ihFov = parseFloat(sliders.ih_fov.value) || parseFloat(sliderValues.ih_fov.value) || 190.00;
        const ivFov = parseFloat(sliders.iv_fov.value) || parseFloat(sliderValues.iv_fov.value) || 190.00;
        const fov = Math.max(ihFov, ivFov);
        const maxOverlap = Math.max(0, Math.floor(((fov - 180) / 360) * 3840));
        
        if (blendWidthSlider) {
            blendWidthSlider.max = Math.max(maxOverlap, 400);
            blendWidthSlider.value = maxOverlap;
            if (blendWidthVal) blendWidthVal.value = maxOverlap;
        }
    }
    
    // Call once on load
    updateMaxBlendWidth();

    // Nadir and VidStab sliders
    if (nadirFovSlider) {
        nadirFovSlider.addEventListener('input', () => { if (nadirFovVal) nadirFovVal.value = nadirFovSlider.value; });
        nadirFovSlider.addEventListener('change', generatePreview);
        if (nadirFovVal) {
            nadirFovVal.addEventListener('input', () => { nadirFovSlider.value = nadirFovVal.value; });
            nadirFovVal.addEventListener('change', generatePreview);
        }
    }
    if (nadirFovVSlider) {
        nadirFovVSlider.addEventListener('input', () => { if (nadirFovVVal) nadirFovVVal.value = nadirFovVSlider.value; });
        nadirFovVSlider.addEventListener('change', generatePreview);
        if (nadirFovVVal) {
            nadirFovVVal.addEventListener('input', () => { nadirFovVSlider.value = nadirFovVVal.value; });
            nadirFovVVal.addEventListener('change', generatePreview);
        }
    }
    if (stabSmoothingSlider) {
        stabSmoothingSlider.addEventListener('input', () => { if (stabSmoothingVal) stabSmoothingVal.value = stabSmoothingSlider.value; });
        if (stabSmoothingVal) stabSmoothingVal.addEventListener('input', () => { stabSmoothingSlider.value = stabSmoothingVal.value; });
    }
    if (stabShakinessSlider) {
        stabShakinessSlider.addEventListener('input', () => { if (stabShakinessVal) stabShakinessVal.value = stabShakinessSlider.value; });
        if (stabShakinessVal) stabShakinessVal.addEventListener('input', () => { stabShakinessSlider.value = stabShakinessVal.value; });
    }
    if (blendWidthSlider) {
        blendWidthSlider.addEventListener('input', () => { if (blendWidthVal) blendWidthVal.value = blendWidthSlider.value; });
        if (blendWidthVal) {
            blendWidthVal.addEventListener('input', () => { blendWidthSlider.value = blendWidthVal.value; });
            blendWidthVal.addEventListener('change', () => { generatePreview(); });
        }
    }
    
    // Anti-Vignette Angle (not in the main sliders list)
    const antiVigAngleSlider = document.getElementById('anti_vignette_angle');
    const antiVigAngleVal = document.getElementById('anti_vignette_angle_val');
    if (antiVigAngleSlider && antiVigAngleVal) {
        antiVigAngleSlider.addEventListener('input', () => { antiVigAngleVal.value = antiVigAngleSlider.value; });
        antiVigAngleSlider.addEventListener('change', () => { generatePreview(); });
        antiVigAngleVal.addEventListener('input', () => { antiVigAngleSlider.value = antiVigAngleVal.value; });
        antiVigAngleVal.addEventListener('change', () => { generatePreview(); });
    }

    // Toggles for settings panes
    const blendSeamsCb  = document.getElementById('blend_seams');
    const blendSettings = document.getElementById('blend-settings');
    if (blendSeamsCb && blendSettings) {
        blendSeamsCb.addEventListener('change', () => {
            blendSettings.style.display = blendSeamsCb.checked ? 'block' : 'none';
        });
        blendSettings.style.display = blendSeamsCb.checked ? 'block' : 'none';
    }

    const nadirEnabledCb = document.getElementById('nadir_enabled');
    const nadirSettings  = document.getElementById('nadir-settings');
    if (nadirEnabledCb && nadirSettings) {
        nadirEnabledCb.addEventListener('change', () => {
            nadirSettings.style.display = nadirEnabledCb.checked ? 'block' : 'none';
            updateNadirAlert();
            updateStitchingVisibility();
            if (nadirEnabledCb.checked && enableStitchingCb && !enableStitchingCb.checked) {
                switchTab('preview', true);
            } else {
                generatePreview();
            }
        });
        nadirSettings.style.display = nadirEnabledCb.checked ? 'block' : 'none';
    }

    const nadirLogoSelect = document.getElementById('nadir_logo');
    if (nadirLogoSelect) {
        nadirLogoSelect.addEventListener('change', () => {
            updateNadirAlert();
            generatePreview();
        });
    }

    updateNadirAlert();

    const antiVignetteCb = document.getElementById('anti_vignette');
    const vignetteSettings = document.getElementById('vignette-settings');
    if (antiVignetteCb && vignetteSettings) {
        antiVignetteCb.addEventListener('change', () => {
            vignetteSettings.style.display = antiVignetteCb.checked ? 'block' : 'none';
            generatePreview();
        });
        vignetteSettings.style.display = antiVignetteCb.checked ? 'block' : 'none';
    }

    const antiVignetteAngleSlider = document.getElementById('anti_vignette_angle');
    const antiVignetteVal = document.getElementById('anti_vignette_angle_val');
    if (antiVignetteAngleSlider) antiVignetteAngleSlider.addEventListener('input', () => { if (antiVignetteVal) antiVignetteVal.textContent = antiVignetteAngleSlider.value; });
    if (antiVignetteAngleSlider) antiVignetteAngleSlider.addEventListener('change', generatePreview);

    const stabilizeCb   = document.getElementById('stabilize');
    const stabilizeType = document.getElementById('stabilize_type');
    const stabilizeTypeContainer = document.getElementById('stabilize-type-container');
    const telemetrySettingsPanel = document.getElementById('telemetry-settings-panel');
    const vidstabSettingsPanel   = document.getElementById('vidstab-settings-panel');
    const kabschSettingsPanel    = document.getElementById('kabsch-settings-panel');
    const kopfSettingsPanel      = document.getElementById('kopf-settings-panel');
    const horizonSettingsPanel   = document.getElementById('horizon-settings-panel');
    const telemetryMode = document.getElementById('telemetry_mode');
    const telemetrySmoothingContainer = document.getElementById('telemetry-smoothing-container');
    
    function updateStabVisibility() {
        if (!stabilizeCb) return;
        const isStabEnabled = stabilizeCb.checked;
        if (stabilizeTypeContainer) {
            stabilizeTypeContainer.style.display = isStabEnabled ? 'block' : 'none';
        }
        const stabTel  = document.getElementById('stab_telemetry');
        const stabVid  = document.getElementById('stab_vidstab');
        const stabKab  = document.getElementById('stab_kabsch');
        const stabKpf  = document.getElementById('stab_kopf');
        const stabL1   = document.getElementById('stab_cinematic');
        const stabHz   = document.getElementById('stab_horizon');
        const stabDl   = document.getElementById('stab_traveldir');

        const isTelemetry     = stabTel ? stabTel.checked : true;
        const isVidstab       = stabVid ? stabVid.checked : false;
        const isKabsch        = stabKab ? stabKab.checked : false;
        const isKopf          = stabKpf ? stabKpf.checked : false;
        const isCinematic      = stabL1  ? stabL1.checked  : false;
        const isHorizon       = stabHz  ? stabHz.checked  : false;
        const isTraveldir = stabDl  ? stabDl.checked  : false;

        if (telemetrySettingsPanel) {
            telemetrySettingsPanel.style.display = (isStabEnabled && isTelemetry) ? 'block' : 'none';
        }
        if (kopfSettingsPanel) {
            kopfSettingsPanel.style.display = (isStabEnabled && isKopf) ? 'block' : 'none';
        }
        if (kabschSettingsPanel) {
            kabschSettingsPanel.style.display = (isStabEnabled && isKabsch) ? 'block' : 'none';
        }
        if (vidstabSettingsPanel) {
            vidstabSettingsPanel.style.display = (isStabEnabled && isVidstab) ? 'block' : 'none';
        }
        const cinematicSettingsPanel = document.getElementById('cinematic-settings-panel');
        if (cinematicSettingsPanel) {
            cinematicSettingsPanel.style.display = (isStabEnabled && isCinematic) ? 'block' : 'none';
        }
        if (horizonSettingsPanel) {
            horizonSettingsPanel.style.display = (isStabEnabled && isHorizon) ? 'block' : 'none';
        }
        const traveldirSettingsPanel = document.getElementById('traveldir-settings-panel');
        if (traveldirSettingsPanel) {
            traveldirSettingsPanel.style.display = (isStabEnabled && isTraveldir) ? 'block' : 'none';
        }
        const telemetryRefFrameContainer = document.getElementById('telemetry-ref-frame-container');
        if (telemetrySmoothingContainer && telemetryMode) {
            telemetrySmoothingContainer.style.display = telemetryMode.value === 'smooth' ? 'block' : 'none';
        }
        if (telemetryRefFrameContainer && telemetryMode) {
            telemetryRefFrameContainer.style.display = telemetryMode.value === 'lock' ? 'block' : 'none';
        }
        updateTelemetryFusionUI();
        updateStabOrderDisplay();
    }

    window.updateTelemetryFusionUI = function() {
        const fusionEl = document.getElementById('telemetry_fusion');
        const gainContainer = document.getElementById('telemetry-fusion-gain-container');
        if (fusionEl && gainContainer) {
            gainContainer.style.display = (fusionEl.value && fusionEl.value !== 'none') ? 'block' : 'none';
        }
    };
    window.updateStabPanels = updateStabVisibility;
    const enableStitchingCb = document.getElementById('enable_stitching');
    const stitchingControls = document.getElementById('stitching-controls');

    function updateStitchingVisibility() {
        const isStitchingEnabled = !enableStitchingCb || enableStitchingCb.checked;
        const isNadirEnabled = !!(nadirEnabledCb && nadirEnabledCb.checked);
        const inputHasStitchedCb = document.getElementById('input_has_stitched');
        const isStitchedInput = !!(inputHasStitchedCb && inputHasStitchedCb.checked);

        if (stitchingControls) {
            stitchingControls.style.display = isStitchingEnabled ? 'block' : 'none';
        }

        if (isStitchedInput) {
            if (tabPreview) {
                tabPreview.classList.remove('hidden');
                tabPreview.textContent = isNadirEnabled ? 'Nadir Preview Frame' : 'Preview Frame';
            }
            const popupPreviewTitle = document.querySelector('#popup-preview-header .vr-popup-drag-handle');
            if (popupPreviewTitle) popupPreviewTitle.innerHTML = isNadirEnabled ? '&#x2630; Nadir Preview Frame' : '&#x2630; Preview Frame';
        } else if (isStitchingEnabled) {
            if (tabPreview) {
                tabPreview.classList.remove('hidden');
                tabPreview.textContent = 'Stitched Preview Frame';
            }
            const popupPreviewTitle = document.querySelector('#popup-preview-header .vr-popup-drag-handle');
            if (popupPreviewTitle) popupPreviewTitle.innerHTML = '&#x2630; Stitched Preview Frame';
        } else if (isNadirEnabled) {
            if (tabPreview) {
                tabPreview.classList.remove('hidden');
                tabPreview.textContent = 'Nadir Preview Frame';
            }
            const popupPreviewTitle = document.querySelector('#popup-preview-header .vr-popup-drag-handle');
            if (popupPreviewTitle) popupPreviewTitle.innerHTML = '&#x2630; Nadir Preview Frame';
        } else {
            if (tabPreview) {
                tabPreview.classList.add('hidden');
            }
            if (tabPreview && tabPreview.classList.contains('active')) {
                switchTab('original');
            }
            const popupPreview = document.getElementById('popup-preview');
            if (popupPreview && popupPreview.style.display !== 'none') {
                const btnClosePopupPreview = document.getElementById('btn-close-popup-preview');
                if (btnClosePopupPreview) btnClosePopupPreview.click();
            }
        }

        updateOutputName();
    }

    if (enableStitchingCb) {
        enableStitchingCb.addEventListener('change', updateStitchingVisibility);
    }
    updateStitchingVisibility();
    
    if (stabilizeCb) {
        stabilizeCb.addEventListener('change', () => {
            if (stabilizeCb.checked) {
                const anyStabChecked = STAB_METHOD_IDS.some(id => document.getElementById(id)?.checked);
                if (!anyStabChecked) {
                    const telCb = document.getElementById('stab_telemetry');
                    if (telCb) {
                        telCb.checked = true;
                        telCb.dispatchEvent(new Event('change'));
                    }
                    const preTel = document.getElementById('input_has_telemetry');
                    if (preTel && preTel.checked) {
                        preTel.checked = false;
                    }
                    updateInputFeaturesBadge();
                }
            }
            updateStabVisibility();
        });
    }
    if (stabilizeType) {
        stabilizeType.addEventListener('change', updateStabVisibility);
    }
    if (telemetryMode) {
        telemetryMode.addEventListener('change', updateStabVisibility);
    }
    loadStabOrder();
    updateStabVisibility();

    const telemetrySmoothingSlider = document.getElementById('telemetry_smoothing');
    const telemetrySmoothingVal    = document.getElementById('telemetry_smoothing_val');
    if (telemetrySmoothingSlider) {
        telemetrySmoothingSlider.addEventListener('input', () => { if (telemetrySmoothingVal) telemetrySmoothingVal.value = telemetrySmoothingSlider.value; });
        if (telemetrySmoothingVal) telemetrySmoothingVal.addEventListener('input', () => { telemetrySmoothingSlider.value = telemetrySmoothingVal.value; });
    }

    const telemetryMultiplierRollSlider = document.getElementById('telemetry_multiplier_roll');
    const telemetryMultiplierRollVal    = document.getElementById('telemetry_multiplier_roll_val');
    if (telemetryMultiplierRollSlider) {
        telemetryMultiplierRollSlider.addEventListener('input', () => { if (telemetryMultiplierRollVal) telemetryMultiplierRollVal.value = telemetryMultiplierRollSlider.value; });
        if (telemetryMultiplierRollVal) telemetryMultiplierRollVal.addEventListener('input', () => { telemetryMultiplierRollSlider.value = telemetryMultiplierRollVal.value; });
    }

    const telemetryMultiplierPitchSlider = document.getElementById('telemetry_multiplier_pitch');
    const telemetryMultiplierPitchVal    = document.getElementById('telemetry_multiplier_pitch_val');
    if (telemetryMultiplierPitchSlider) {
        telemetryMultiplierPitchSlider.addEventListener('input', () => { if (telemetryMultiplierPitchVal) telemetryMultiplierPitchVal.value = telemetryMultiplierPitchSlider.value; });
        if (telemetryMultiplierPitchVal) telemetryMultiplierPitchVal.addEventListener('input', () => { telemetryMultiplierPitchSlider.value = telemetryMultiplierPitchVal.value; });
    }

    const telemetryMultiplierYawSlider = document.getElementById('telemetry_multiplier_yaw');
    const telemetryMultiplierYawVal    = document.getElementById('telemetry_multiplier_yaw_val');
    if (telemetryMultiplierYawSlider) {
        telemetryMultiplierYawSlider.addEventListener('input', () => { if (telemetryMultiplierYawVal) telemetryMultiplierYawVal.value = telemetryMultiplierYawSlider.value; });
        if (telemetryMultiplierYawVal) telemetryMultiplierYawVal.addEventListener('input', () => { telemetryMultiplierYawSlider.value = telemetryMultiplierYawVal.value; });
    }

    // Buttons
    const btnAutoCalibrate = document.getElementById('btn-auto-calibrate');
    if (btnAutoCalibrate) btnAutoCalibrate.addEventListener('click', autoCalibrateStitching);
    const calibFramesSelect = document.getElementById('auto_calibrate_frames');
    const calibCustomInput = document.getElementById('auto_calibrate_frames_custom');
    if (calibFramesSelect && calibCustomInput) {
        calibFramesSelect.addEventListener('change', () => {
            calibCustomInput.style.display = calibFramesSelect.value === 'custom' ? 'inline-block' : 'none';
        });
    }
    if (btnPreview) btnPreview.addEventListener('click', generatePreview);
    btnStitch.addEventListener('click', startStitching);
    document.getElementById('btn-confirm-proceed')?.addEventListener('click', executeStartStitching);
    document.getElementById('btn-cancel-proceed')?.addEventListener('click', hideProceedConfirmation);
    document.getElementById('btn-cancel-proceed-x')?.addEventListener('click', hideProceedConfirmation);
    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('modal-proceed-confirm');
        if (modal && modal.style.display === 'flex') {
            if (e.key === 'Escape') {
                hideProceedConfirmation();
            } else if (e.key === 'Enter') {
                executeStartStitching();
            }
            return;
        }
        const cropModal = document.getElementById('modal-crop-result');
        if (cropModal && cropModal.style.display === 'flex') {
            if (e.key === 'Escape' || e.key === 'Enter') {
                hideCropModal();
                return;
            }
        }
    });
    btnReset.addEventListener('click', resetStitcher);
    initPreviewFrameControls();

    // Blend Seams Checkbox & Slider → refresh preview
    if (blendSeamsCb) blendSeamsCb.addEventListener('change', generatePreview);
    if (blendWidthSlider) blendWidthSlider.addEventListener('change', generatePreview);

    // Tab buttons
    tabOriginal.addEventListener('click', () => switchTab('original', true));
    tabPreview.addEventListener('click', () => switchTab('preview', true));
    if (tabStitched) tabStitched.addEventListener('click', () => switchTab('stitched', true));
    if (tabTelemetry) tabTelemetry.addEventListener('click', () => switchTab('telemetry', true));
    if (tabKabsch)    tabKabsch.addEventListener('click',    () => switchTab('kabsch', true));
    if (tabKopf)      tabKopf.addEventListener('click',      () => switchTab('kopf', true));
    if (tabVidstab)       tabVidstab.addEventListener('click',       () => switchTab('vidstab', true));
    if (tabCinematic)      tabCinematic.addEventListener('click',      () => switchTab('cinematic', true));
    if (tabHorizon)       tabHorizon.addEventListener('click',       () => switchTab('horizon', true));
    if (tabTraveldir) tabTraveldir.addEventListener('click', () => switchTab('traveldir', true));
    tab360.addEventListener('click',     () => switchTab('360', true));

    // Load Test Run by Number Widget
    const btnLoadTest = document.getElementById('btn-load-test');
    const loadTestNumberInput = document.getElementById('load_test_number');

    async function triggerLoadTest() {
        const val = loadTestNumberInput ? loadTestNumberInput.value.trim() : '';
        if (!val) return;
        if (btnLoadTest) btnLoadTest.disabled = true;
        await checkAllIntermediateVideos(val);
        if (btnLoadTest) btnLoadTest.disabled = false;

        // Auto-switch to 360 VR Viewport or Stitched tab if available
        if (tab360 && !tab360.classList.contains('hidden')) {
            switchTab('360', true);
        } else if (tabStitched && !tabStitched.classList.contains('hidden')) {
            switchTab('stitched', true);
        }
    }

    if (btnLoadTest) {
        btnLoadTest.addEventListener('click', triggerLoadTest);
    }
    if (loadTestNumberInput) {
        loadTestNumberInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                triggerLoadTest();
            }
        });
    }

    // Refresh Input Videos Button
    const btnRefreshInputs = document.getElementById('btn-refresh-inputs');
    async function refreshInputVideos(targetSelectValue = null, silent = false) {
        const btnRefresh = document.getElementById('btn-refresh-inputs');
        const inputSelect = options.input || document.getElementById('input_name');
        if (!inputSelect) return;

        if (btnRefresh) {
            btnRefresh.disabled = true;
            const svg = btnRefresh.querySelector('svg');
            if (svg) svg.style.animation = 'spin 0.8s linear infinite';
        }

        try {
            const res = await fetch('api/v1/inputs');
            const data = await res.json();
            if (data && data.status === 'success' && data.items) {
                const previousVal = targetSelectValue || inputSelect.value;
                inputSelect.innerHTML = '';
                if (data.items.length === 0) {
                    const opt = document.createElement('option');
                    opt.value = '';
                    opt.textContent = 'No video files found in data/input/videos/ folder';
                    inputSelect.appendChild(opt);
                } else {
                    data.items.forEach(item => {
                        const opt = document.createElement('option');
                        opt.value = item.value;
                        opt.textContent = item.text;
                        inputSelect.appendChild(opt);
                    });
                }

                let found = false;
                if (previousVal) {
                    for (let i = 0; i < inputSelect.options.length; i++) {
                        if (inputSelect.options[i].value === previousVal || inputSelect.options[i].text === previousVal) {
                            inputSelect.selectedIndex = i;
                            found = true;
                            break;
                        }
                    }
                }
                if (!found && inputSelect.options.length > 0) {
                    inputSelect.selectedIndex = 0;
                }

                detectInputFeatures(inputSelect.value);
                await updateOutputName();
                loadVideoMetadata();
                generatePreview();
                if (typeof populateStreetViewAdditionalVideos === 'function') {
                    populateStreetViewAdditionalVideos();
                }
            }
        } catch (e) {
            console.error('Failed to refresh video files list:', e);
            if (!silent) alert('Failed to refresh video files list.');
        } finally {
            if (btnRefresh) {
                btnRefresh.disabled = false;
                const svg = btnRefresh.querySelector('svg');
                if (svg) svg.style.animation = '';
            }
        }
    }

    if (btnRefreshInputs) {
        btnRefreshInputs.addEventListener('click', () => refreshInputVideos(null, false));
    }

    // Refresh Nadir Logos Button
    const btnRefreshNadir = document.getElementById('btn-refresh-nadir');
    async function refreshNadirLogos(targetSelectValue = null, silent = false) {
        const btnRefresh = document.getElementById('btn-refresh-nadir');
        const nadirSelect = document.getElementById('nadir_logo');
        if (!nadirSelect) return;

        if (btnRefresh) {
            btnRefresh.disabled = true;
            const svg = btnRefresh.querySelector('svg');
            if (svg) svg.style.animation = 'spin 0.8s linear infinite';
        }

        try {
            const res = await fetch('api/v1/logos');
            const data = await res.json();
            if (data && data.status === 'success' && data.items) {
                const previousVal = targetSelectValue || nadirSelect.value;
                nadirSelect.innerHTML = '';
                if (data.items.length === 0) {
                    const opt = document.createElement('option');
                    opt.value = '';
                    opt.textContent = 'No logos found in data/input/nadir/ folder';
                    nadirSelect.appendChild(opt);
                } else {
                    data.items.forEach(item => {
                        const opt = document.createElement('option');
                        opt.value = item.value;
                        opt.textContent = item.text;
                        if (item.selected) {
                            opt.selected = true;
                        }
                        nadirSelect.appendChild(opt);
                    });
                }


                let found = false;
                if (previousVal) {
                    for (let i = 0; i < nadirSelect.options.length; i++) {
                        if (nadirSelect.options[i].value === previousVal || nadirSelect.options[i].text === previousVal) {
                            nadirSelect.selectedIndex = i;
                            found = true;
                            break;
                        }
                    }
                }
                if (!found && nadirSelect.options.length > 0) {
                    nadirSelect.selectedIndex = 0;
                }

                updateNadirAlert();
                generatePreview();
            }
        } catch (e) {
            console.error('Failed to refresh nadir logo files list:', e);
            if (!silent) alert('Failed to refresh nadir logo files list.');
        } finally {
            if (btnRefresh) {
                btnRefresh.disabled = false;
                const svg = btnRefresh.querySelector('svg');
                if (svg) svg.style.animation = '';
            }
        }
    }

    if (btnRefreshNadir) {
        btnRefreshNadir.addEventListener('click', () => refreshNadirLogos(null, false));
    }

    // Refresh GPX Log Files Button
    const btnRefreshGpx = document.getElementById('btn-refresh-gpx');
    async function refreshGpxFiles(targetSelectValue = null, silent = false) {
        const btnRefresh = document.getElementById('btn-refresh-gpx');
        const gpxSelect = document.getElementById('streetview_gpx_path');
        if (!gpxSelect) return;

        if (btnRefresh) {
            btnRefresh.disabled = true;
            const svg = btnRefresh.querySelector('svg');
            if (svg) svg.style.animation = 'spin 0.8s linear infinite';
        }

        try {
            const res = await fetch('api/v1/gpx');
            const data = await res.json();
            if (data && data.status === 'success' && data.items) {
                const previousVal = targetSelectValue || gpxSelect.value;
                gpxSelect.innerHTML = '';
                if (data.items.length === 0) {
                    const opt = document.createElement('option');
                    opt.value = '';
                    opt.textContent = 'No .gpx files found in data/input/gps/ folder';
                    gpxSelect.appendChild(opt);
                } else {
                    data.items.forEach(item => {
                        const opt = document.createElement('option');
                        opt.value = item.value;
                        opt.textContent = item.text;
                        gpxSelect.appendChild(opt);
                    });
                }


                let found = false;
                if (previousVal) {
                    for (let i = 0; i < gpxSelect.options.length; i++) {
                        if (gpxSelect.options[i].value === previousVal || gpxSelect.options[i].text === previousVal) {
                            gpxSelect.selectedIndex = i;
                            found = true;
                            break;
                        }
                    }
                }
                if (!found && gpxSelect.options.length > 0) {
                    gpxSelect.selectedIndex = 0;
                }

                gpxSelect.dispatchEvent(new Event('change'));
            }
        } catch (e) {
            console.error('Failed to refresh GPX files list:', e);
            if (!silent) alert('Failed to refresh GPX files list.');
        } finally {
            if (btnRefresh) {
                btnRefresh.disabled = false;
                const svg = btnRefresh.querySelector('svg');
                if (svg) svg.style.animation = '';
            }
        }
    }

    if (btnRefreshGpx) {
        btnRefreshGpx.addEventListener('click', () => refreshGpxFiles(null, false));
    }

    // ── Pipeline Config Manager Initialization ────────────
    initPipelineConfigManager();

    // Helper to toggle native browser fullscreen
    function toggleRealFullscreen(element) {
        if (!element) return;
        if (!document.fullscreenElement && !document.webkitFullscreenElement) {
            if (element.requestFullscreen) {
                element.requestFullscreen();
            } else if (element.webkitRequestFullscreen) {
                element.webkitRequestFullscreen();
            }
        } else {
            if (document.exitFullscreen) {
                document.exitFullscreen();
            } else if (document.webkitExitFullscreen) {
                document.webkitExitFullscreen();
            }
        }
    }

    // Real Native Fullscreen Buttons
    const vrRealFs         = document.getElementById('vr-real-fullscreen');
    const vrStitchedRealFs = document.getElementById('vr-stitched-real-fullscreen');
    const origRealFs       = document.getElementById('orig-real-fullscreen');
    const telemetryRealFs  = document.getElementById('vr-telemetry-real-fullscreen');
    const kopfRealFs       = document.getElementById('vr-kopf-real-fullscreen');
    const kabschRealFs     = document.getElementById('vr-kabsch-real-fullscreen');
    const vidstabRealFs    = document.getElementById('vr-vidstab-real-fullscreen');
    const cinematicRealFs  = document.getElementById('vr-cinematic-real-fullscreen');
    const horizonRealFs    = document.getElementById('vr-horizon-real-fullscreen');
    const traveldirRealFs  = document.getElementById('vr-traveldir-real-fullscreen');

    if (vrRealFs)         vrRealFs.addEventListener('click', () => toggleRealFullscreen(player360Container || document.getElementById('360-container')));
    if (vrStitchedRealFs) vrStitchedRealFs.addEventListener('click', () => toggleRealFullscreen(playerStitchedContainer || document.getElementById('stitched-container')));
    if (origRealFs)       origRealFs.addEventListener('click', () => toggleRealFullscreen(document.getElementById('original-container')));
    if (telemetryRealFs)  telemetryRealFs.addEventListener('click', () => toggleRealFullscreen(document.getElementById('telemetry-container')));
    if (kopfRealFs)       kopfRealFs.addEventListener('click', () => toggleRealFullscreen(document.getElementById('kopf-container')));
    if (kabschRealFs)     kabschRealFs.addEventListener('click', () => toggleRealFullscreen(document.getElementById('kabsch-container')));
    if (vidstabRealFs)    vidstabRealFs.addEventListener('click', () => toggleRealFullscreen(document.getElementById('vidstab-container')));
    if (cinematicRealFs)  cinematicRealFs.addEventListener('click', () => toggleRealFullscreen(document.getElementById('cinematic-container')));
    if (horizonRealFs)    horizonRealFs.addEventListener('click', () => toggleRealFullscreen(document.getElementById('horizon-container')));
    if (traveldirRealFs)  traveldirRealFs.addEventListener('click', () => toggleRealFullscreen(document.getElementById('traveldir-container')));

    initTelemetryVRControls();
    initKopfVRControls();
    initKabschVRControls();
    initVidstabVRControls();
    initHorizonVRControls();
    initCinematicVRControls();
    initTraveldirVRControls();

    const injectMetaChk = document.getElementById('inject_intermediate_meta');
    if (injectMetaChk) {
        injectMetaChk.addEventListener('change', () => {
            checkAllIntermediateVideos();
        });
    }

    // Movable Pop-up Fullscreen Buttons
    if (vrFullscreen) vrFullscreen.addEventListener('click', togglePopup360);
    if (vrStitchedFullscreen) vrStitchedFullscreen.addEventListener('click', togglePopupStitched);
    if (origFullscreen) origFullscreen.addEventListener('click', togglePopupOriginal);
    const btnFullscreenOrig = document.getElementById('btn-fullscreen-original');
    if (btnFullscreenOrig) btnFullscreenOrig.addEventListener('click', togglePopupOriginal);

    const btnFullscreenTelemetry = document.getElementById('btn-fullscreen-telemetry');
    const vrTelemetryFullscreen  = document.getElementById('vr-telemetry-fullscreen');
    if (btnFullscreenTelemetry) btnFullscreenTelemetry.addEventListener('click', togglePopupTelemetry);
    if (vrTelemetryFullscreen)  vrTelemetryFullscreen.addEventListener('click', togglePopupTelemetry);

    const btnFullscreenKopf  = document.getElementById('btn-fullscreen-kopf');
    const vrKopfFullscreen   = document.getElementById('vr-kopf-fullscreen');
    if (btnFullscreenKopf) btnFullscreenKopf.addEventListener('click', togglePopupKopf);
    if (vrKopfFullscreen)  vrKopfFullscreen.addEventListener('click', togglePopupKopf);

    const btnFullscreenKabsch = document.getElementById('btn-fullscreen-kabsch');
    const vrKabschFullscreen  = document.getElementById('vr-kabsch-fullscreen');
    if (btnFullscreenKabsch) btnFullscreenKabsch.addEventListener('click', togglePopupKabsch);
    if (vrKabschFullscreen)  vrKabschFullscreen.addEventListener('click', togglePopupKabsch);

    const btnFullscreenVidstab  = document.getElementById('btn-fullscreen-vidstab');
    const vrVidstabFullscreen   = document.getElementById('vr-vidstab-fullscreen');
    if (btnFullscreenVidstab) btnFullscreenVidstab.addEventListener('click', togglePopupVidstab);
    if (vrVidstabFullscreen)  vrVidstabFullscreen.addEventListener('click', togglePopupVidstab);

    const btnFullscreenCinematic = document.getElementById('btn-fullscreen-cinematic');
    const vrCinematicFullscreen  = document.getElementById('vr-cinematic-fullscreen');
    if (btnFullscreenCinematic) btnFullscreenCinematic.addEventListener('click', togglePopupCinematic);
    if (vrCinematicFullscreen)  vrCinematicFullscreen.addEventListener('click', togglePopupCinematic);

    const btnFullscreenHorizon = document.getElementById('btn-fullscreen-horizon');
    const vrHorizonFullscreen  = document.getElementById('vr-horizon-fullscreen');
    if (btnFullscreenHorizon) btnFullscreenHorizon.addEventListener('click', togglePopupHorizon);
    if (vrHorizonFullscreen)  vrHorizonFullscreen.addEventListener('click', togglePopupHorizon);

    const btnFullscreenTraveldir = document.getElementById('btn-fullscreen-traveldir');
    const vrTraveldirFullscreen  = document.getElementById('vr-traveldir-fullscreen');
    if (btnFullscreenTraveldir) btnFullscreenTraveldir.addEventListener('click', togglePopupTraveldir);
    if (vrTraveldirFullscreen)  vrTraveldirFullscreen.addEventListener('click', togglePopupTraveldir);

    const btnFullscreenPrev = document.getElementById('btn-fullscreen-preview');
    if (btnFullscreenPrev) btnFullscreenPrev.addEventListener('click', () => toggleRealFullscreen(document.getElementById('preview-container')));

    if (btnClosePopup360) btnClosePopup360.addEventListener('click', closePopup360);
    if (btnClosePopupStitched) btnClosePopupStitched.addEventListener('click', closePopupStitched);
    if (btnClosePopupOriginal) btnClosePopupOriginal.addEventListener('click', closePopupOriginal);
    if (btnClosePopupTelemetry) btnClosePopupTelemetry.addEventListener('click', closePopupTelemetry);
    if (btnClosePopupKopf)      btnClosePopupKopf.addEventListener('click', closePopupKopf);
    if (btnClosePopupKabsch)    btnClosePopupKabsch.addEventListener('click', closePopupKabsch);
    if (btnClosePopupVidstab)   btnClosePopupVidstab.addEventListener('click', closePopupVidstab);
    if (btnClosePopupCinematic)  btnClosePopupCinematic.addEventListener('click', closePopupCinematic);
    if (btnClosePopupHorizon)   btnClosePopupHorizon.addEventListener('click', closePopupHorizon);
    if (btnClosePopupTraveldir) btnClosePopupTraveldir.addEventListener('click', closePopupTraveldir);
    if (btnClosePopupPreview) btnClosePopupPreview.addEventListener('click', closePopupPreview);

    const btnSyncStitched      = document.getElementById('btn-sync-play-stitched');
    const btnSync360           = document.getElementById('btn-sync-play-360');
    const btnSyncOrig          = document.getElementById('btn-sync-play-original');
    const btnSyncTelemetry     = document.getElementById('btn-sync-play-telemetry');
    const btnSyncKopf          = document.getElementById('btn-sync-play-kopf');
    const btnSyncKabsch        = document.getElementById('btn-sync-play-kabsch');
    const btnSyncVidstab       = document.getElementById('btn-sync-play-vidstab');
    const btnSyncCinematic      = document.getElementById('btn-sync-play-cinematic');
    const btnSyncHorizon       = document.getElementById('btn-sync-play-horizon');
    const btnSyncTraveldir = document.getElementById('btn-sync-play-traveldir');
    if (btnSyncStitched)      btnSyncStitched.addEventListener('click', toggleSyncPlay);
    if (btnSync360)           btnSync360.addEventListener('click', toggleSyncPlay);
    if (btnSyncOrig)          btnSyncOrig.addEventListener('click', toggleSyncPlay);
    if (btnSyncTelemetry)     btnSyncTelemetry.addEventListener('click', toggleSyncPlay);
    if (btnSyncKopf)          btnSyncKopf.addEventListener('click', toggleSyncPlay);
    if (btnSyncKabsch)        btnSyncKabsch.addEventListener('click', toggleSyncPlay);
    if (btnSyncVidstab)       btnSyncVidstab.addEventListener('click', toggleSyncPlay);
    if (btnSyncCinematic)      btnSyncCinematic.addEventListener('click', toggleSyncPlay);
    if (btnSyncHorizon)       btnSyncHorizon.addEventListener('click', toggleSyncPlay);
    if (btnSyncTraveldir) btnSyncTraveldir.addEventListener('click', toggleSyncPlay);

    const btnCopyLinkStitched      = document.getElementById('btn-copy-link-stitched');
    const btnCopyLink360           = document.getElementById('btn-copy-link-360');
    const btnCopyLinkOrig          = document.getElementById('btn-copy-link-original');
    const btnCopyLinkTelemetry     = document.getElementById('btn-copy-link-telemetry');
    const btnCopyLinkKopf          = document.getElementById('btn-copy-link-kopf');
    const btnCopyLinkKabsch        = document.getElementById('btn-copy-link-kabsch');
    const btnCopyLinkVidstab       = document.getElementById('btn-copy-link-vidstab');
    const btnCopyLinkCinematic      = document.getElementById('btn-copy-link-cinematic');
    const btnCopyLinkHorizon       = document.getElementById('btn-copy-link-horizon');
    const btnCopyLinkTraveldir = document.getElementById('btn-copy-link-traveldir');
    if (btnCopyLinkStitched)      btnCopyLinkStitched.addEventListener('click', (e) => copyVideoLink(videoStitched, e.currentTarget));
    if (btnCopyLink360)           btnCopyLink360.addEventListener('click', (e) => copyVideoLink(video360, e.currentTarget));
    if (btnCopyLinkOrig)          btnCopyLinkOrig.addEventListener('click', (e) => copyVideoLink(originalVideo, e.currentTarget));
    if (btnCopyLinkTelemetry)     btnCopyLinkTelemetry.addEventListener('click', (e) => copyVideoLink(videoTelemetry, e.currentTarget));
    if (btnCopyLinkKopf)          btnCopyLinkKopf.addEventListener('click', (e) => copyVideoLink(videoKopf, e.currentTarget));
    if (btnCopyLinkKabsch)        btnCopyLinkKabsch.addEventListener('click', (e) => copyVideoLink(videoKabsch, e.currentTarget));
    if (btnCopyLinkVidstab)       btnCopyLinkVidstab.addEventListener('click', (e) => copyVideoLink(videoVidstab, e.currentTarget));
    if (btnCopyLinkCinematic)      btnCopyLinkCinematic.addEventListener('click', (e) => copyVideoLink(videoCinematic, e.currentTarget));
    if (btnCopyLinkHorizon)       btnCopyLinkHorizon.addEventListener('click', (e) => copyVideoLink(videoHorizon, e.currentTarget));
    if (btnCopyLinkTraveldir) btnCopyLinkTraveldir.addEventListener('click', (e) => copyVideoLink(videoTraveldir, e.currentTarget));

    const origCopyLink        = document.getElementById('orig-copy-link');
    const vrStitchedCopyLink  = document.getElementById('vr-stitched-copy-link');
    const vrCopyLink          = document.getElementById('vr-copy-link');
    if (origCopyLink && !origCopyLink._hasCopyListener) {
        origCopyLink._hasCopyListener = true;
        origCopyLink.addEventListener('click', (e) => copyVideoLink(originalVideo, e.currentTarget));
    }
    if (vrStitchedCopyLink && !vrStitchedCopyLink._hasCopyListener) {
        vrStitchedCopyLink._hasCopyListener = true;
        vrStitchedCopyLink.addEventListener('click', (e) => copyVideoLink(videoStitched, e.currentTarget));
    }
    if (vrCopyLink && !vrCopyLink._hasCopyListener) {
        vrCopyLink._hasCopyListener = true;
        vrCopyLink.addEventListener('click', (e) => copyVideoLink(video360, e.currentTarget));
    }

    document.addEventListener('keydown', (e) => {
        const tag = e.target.tagName ? e.target.tagName.toLowerCase() : '';
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

        const isAnyPopupOpen = [popup360, popupStitched, popupOriginal, popupTelemetry, popupKopf, popupKabsch, popupVidstab, popupCinematic, popupHorizon, popupTraveldir, popupPreview].some(p => p && p.style.display !== 'none');

        if (e.code === 'Space' || e.key === 'p' || e.key === 'P') {
            if (isAnyPopupOpen) {
                e.preventDefault();
                toggleSyncPlay();
            }
        } else if (e.key === 'ArrowLeft') {
            if (isAnyPopupOpen) {
                e.preventDefault();
                syncPrevFrame();
            }
        } else if (e.key === 'ArrowRight') {
            if (isAnyPopupOpen) {
                e.preventDefault();
                syncNextFrame();
            }
        } else if (e.key === 'Home') {
            if (isAnyPopupOpen) {
                e.preventDefault();
                syncFirstFrame();
            }
        } else if (e.key === 'End') {
            if (isAnyPopupOpen) {
                e.preventDefault();
                syncLastFrame();
            }
        }
    });

    const vrTelemetryGraphBtn     = document.getElementById('vr-telemetry-graph-btn');
    const vrKopfGraphBtn          = document.getElementById('vr-kopf-graph-btn');
    const vrKabschGraphBtn        = document.getElementById('vr-kabsch-graph-btn');
    const vrVidstabGraphBtn       = document.getElementById('vr-vidstab-graph-btn');
    const vrCinematicGraphBtn      = document.getElementById('vr-cinematic-graph-btn');
    const vrHorizonGraphBtn       = document.getElementById('vr-horizon-graph-btn');
    const vrTraveldirGraphBtn = document.getElementById('vr-traveldir-graph-btn');
    if (vrTelemetryGraphBtn)     vrTelemetryGraphBtn.addEventListener('click', () => openPopupGraph('telemetry'));
    if (vrKopfGraphBtn)          vrKopfGraphBtn.addEventListener('click', () => openPopupGraph('kopf'));
    if (vrKabschGraphBtn)        vrKabschGraphBtn.addEventListener('click', () => openPopupGraph('kabsch'));
    if (vrVidstabGraphBtn)       vrVidstabGraphBtn.addEventListener('click', () => openPopupGraph('vidstab'));
    if (vrCinematicGraphBtn)      vrCinematicGraphBtn.addEventListener('click', () => openPopupGraph('cinematic'));
    if (vrHorizonGraphBtn)       vrHorizonGraphBtn.addEventListener('click', () => openPopupGraph('horizon'));
    if (vrTraveldirGraphBtn) vrTraveldirGraphBtn.addEventListener('click', () => openPopupGraph('traveldir'));
    if (btnClosePopupGraph)      btnClosePopupGraph.addEventListener('click', closePopupGraph);

    const vrTelemetryReportBtn     = document.getElementById('vr-telemetry-report-btn');
    const vrKopfReportBtn          = document.getElementById('vr-kopf-report-btn');
    const vrKabschReportBtn        = document.getElementById('vr-kabsch-report-btn');
    const vrVidstabReportBtn       = document.getElementById('vr-vidstab-report-btn');
    const vrCinematicReportBtn      = document.getElementById('vr-cinematic-report-btn');
    const vrHorizonReportBtn       = document.getElementById('vr-horizon-report-btn');
    const vrTraveldirReportBtn = document.getElementById('vr-traveldir-report-btn');
    if (vrTelemetryReportBtn)     vrTelemetryReportBtn.addEventListener('click', () => openPopupReport('telemetry'));
    if (vrKopfReportBtn)          vrKopfReportBtn.addEventListener('click', () => openPopupReport('kopf'));
    if (vrKabschReportBtn)        vrKabschReportBtn.addEventListener('click', () => openPopupReport('kabsch'));
    if (vrVidstabReportBtn)       vrVidstabReportBtn.addEventListener('click', () => openPopupReport('vidstab'));
    if (vrCinematicReportBtn)      vrCinematicReportBtn.addEventListener('click', () => openPopupReport('cinematic'));
    if (vrHorizonReportBtn)       vrHorizonReportBtn.addEventListener('click', () => openPopupReport('horizon'));
    if (vrTraveldirReportBtn) vrTraveldirReportBtn.addEventListener('click', () => openPopupReport('traveldir'));
    if (btnClosePopupReport)      btnClosePopupReport.addEventListener('click', closePopupReport);

    makeDraggable(popup360, popup360Header);
    makeDraggable(popupStitched, popupStitchedHeader);
    makeDraggable(popupOriginal, popupOriginalHeader);
    makeDraggable(popupTelemetry, popupTelemetryHeader);
    makeDraggable(popupKabsch, popupKabschHeader);
    makeDraggable(popupKopf, popupKopfHeader);
    makeDraggable(popupVidstab, popupVidstabHeader);
    makeDraggable(popupCinematic, popupCinematicHeader);
    makeDraggable(popupHorizon, popupHorizonHeader);
    makeDraggable(popupTraveldir, popupTraveldirHeader);
    makeDraggable(popupGraph, popupGraphHeader);
    makeDraggable(popupReport, popupReportHeader);
    makeDraggable(popupPreview, popupPreviewHeader);

    const btnPreviewVid = document.getElementById('btn-preview-vid');
    if (btnPreviewVid) btnPreviewVid.addEventListener('click', generateVideoPreview);

    const btnClearConsole = document.getElementById('btn-clear-console');
    if (btnClearConsole) {
        btnClearConsole.addEventListener('click', () => {
            const consoleOutput = document.getElementById('console-output');
            if (consoleOutput) consoleOutput.textContent = 'Waiting for commands...';
        });
    }

    const btnCopyConsole = document.getElementById('btn-copy-console');
    if (btnCopyConsole) {
        btnCopyConsole.addEventListener('click', () => {
            const consoleOutput = document.getElementById('console-output');
            if (consoleOutput) {
                const text = consoleOutput.innerText || consoleOutput.textContent || '';
                navigator.clipboard.writeText(text).then(() => {
                    const origHtml = btnCopyConsole.innerHTML;
                    btnCopyConsole.innerHTML = '&#10003; Copied!';
                    setTimeout(() => {
                        btnCopyConsole.innerHTML = origHtml;
                    }, 1500);
                }).catch(err => {
                    console.error("Copy failed:", err);
                });
            }
        });
    }

    const btnClearLog = document.getElementById('btn-clear-log');
    if (btnClearLog) {
        btnClearLog.addEventListener('click', () => {
            userClearedLog = true;
            const logOutput = document.getElementById('log-output');
            if (logOutput) logOutput.textContent = 'Waiting for process to start...';
        });
    }

    const btnCopyLog = document.getElementById('btn-copy-log');
    if (btnCopyLog) {
        btnCopyLog.addEventListener('click', () => {
            const logOutput = document.getElementById('log-output');
            if (logOutput) {
                const text = logOutput.innerText || logOutput.textContent || '';
                navigator.clipboard.writeText(text).then(() => {
                    const origHtml = btnCopyLog.innerHTML;
                    btnCopyLog.innerHTML = '&#10003; Copied!';
                    setTimeout(() => {
                        btnCopyLog.innerHTML = origHtml;
                    }, 1500);
                }).catch(err => {
                    console.error("Copy failed:", err);
                });
            }
        });
    }

    const btnEmptyLog = document.getElementById('btn-empty-log');
    if (btnEmptyLog) {
        btnEmptyLog.addEventListener('click', async () => {
            if (!confirm('Are you sure you want to empty the server log file?')) return;
            try {
                const clearUrl = currentJobId ? `api/v1/jobs/${encodeURIComponent(currentJobId)}/log` : 'api/v1/jobs/_/log';
                const response = await fetch(clearUrl, { method: 'DELETE' });
                const data = await response.json();
                if (data.status === 'success') {
                    userClearedLog = true;
                    const logOutput = document.getElementById('log-output');
                    if (logOutput) logOutput.textContent = 'Waiting for process to start...';
                    const origHtml = btnEmptyLog.innerHTML;
                    btnEmptyLog.innerHTML = '&#10003; Emptied!';
                    setTimeout(() => {
                        btnEmptyLog.innerHTML = origHtml;
                    }, 1500);
                }
            } catch (e) {
                console.error("Failed to empty log file:", e);
            }
        });
    }

    // Video Controls
    if (vrPlayPause) vrPlayPause.addEventListener('click', togglePlay360);
    if (vrFirstFrame) vrFirstFrame.addEventListener('click', syncFirstFrame);
    if (vrPrevFrame) vrPrevFrame.addEventListener('click', syncPrevFrame);
    if (vrNextFrame) vrNextFrame.addEventListener('click', syncNextFrame);
    if (vrLastFrame) vrLastFrame.addEventListener('click', syncLastFrame);

    // VR Playback Listeners
    if (video360) {
        video360.addEventListener('play', () => {
            if (vrPlayPause) vrPlayPause.innerHTML = '&#10074;&#10074;';
            if (threeTexture) threeTexture.needsUpdate = true;
        });
        video360.addEventListener('pause', () => {
            if (vrPlayPause) vrPlayPause.innerHTML = '&#9654;';
        });
        video360.addEventListener('loadedmetadata', () => {
            if (vrSeekbar) vrSeekbar.max = video360.duration;
            updateVRTimeDisplay();
            // Force browser to decode the first frame so it is not black
            if (video360.currentTime === 0) {
                video360.currentTime = 0.05;
            }
            if (threeTexture) threeTexture.needsUpdate = true;
        });
        video360.addEventListener('durationchange', () => {
            if (vrSeekbar) vrSeekbar.max = video360.duration;
            updateVRTimeDisplay();
        });
        video360.addEventListener('timeupdate', () => {
            if (vrSeekbar && !vrSeekbar.dataset.dragging) {
                vrSeekbar.value = video360.currentTime;
            }
            updateVRTimeDisplay();
            if (threeTexture) threeTexture.needsUpdate = true;
        });
        video360.addEventListener('seeked', () => {
            if (threeTexture) threeTexture.needsUpdate = true;
        });
    }

    if (vrSeekbar) {
        vrSeekbar.addEventListener('input', () => {
            vrSeekbar.dataset.dragging = 'true';
            syncSeekToTime(parseFloat(vrSeekbar.value));
        });
        vrSeekbar.addEventListener('change', () => {
            delete vrSeekbar.dataset.dragging;
        });
    }

    if (vrVolume) {
        vrVolume.addEventListener('input', () => {
            video360.volume = parseFloat(vrVolume.value);
            if (vrMute) {
                if (video360.volume === 0) {
                    vrMute.innerHTML = '&#128263;';
                } else {
                    vrMute.innerHTML = '&#128266;';
                }
            }
        });
    }

    if (vrMute) {
        vrMute.addEventListener('click', () => {
            if (video360.muted) {
                video360.muted = false;
                vrMute.innerHTML = '&#128266;';
                if (vrVolume) vrVolume.value = video360.volume;
            } else {
                video360.muted = true;
                vrMute.innerHTML = '&#128263;';
                if (vrVolume) vrVolume.value = 0;
            }
        });
    }

    const vrResetView = document.getElementById('vr-reset-view');
    if (vrResetView) {
        vrResetView.addEventListener('click', () => handleResetView('360'));
    }

    const vrZoomIn  = document.getElementById('vr-zoom-in');
    const vrZoomOut = document.getElementById('vr-zoom-out');
    const vrYaw     = document.getElementById('vr-yaw');
    const vrPitch   = document.getElementById('vr-pitch');
    const vrRoll    = document.getElementById('vr-roll');
    const vrFov     = document.getElementById('vr-fov');

    function updateVRRotationAndFov() {
        updateVRPlayerOrientation('360');
    }

    [vrYaw, vrPitch, vrRoll, vrFov].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateVRRotationAndFov);
            inp.addEventListener('change', updateVRRotationAndFov);
        }
    });
    
    if (vrZoomIn) {
        vrZoomIn.addEventListener('click', () => zoomContainer(threeCamera, vrFov, -5));
    }
    if (vrZoomOut) {
        vrZoomOut.addEventListener('click', () => zoomContainer(threeCamera, vrFov, 5));
    }

    if (player360Container) {
        player360Container.addEventListener('wheel', (e) => {
            if (!isThreeInitialized || !threeCamera) return;
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeCamera, vrFov, delta);
        }, { passive: false });
    }

    // Original Video Controls
    if (origPlayPause) origPlayPause.addEventListener('click', togglePlayOrig);
    if (origToggleVR) {
        origToggleVR.addEventListener('click', () => {
            setOrigVRMode(!isOrigVRMode);
        });
    }

    if (origFirstFrame) {
        origFirstFrame.addEventListener('click', () => {
            if (originalVideo) {
                originalVideo.currentTime = 0;
                updateOrigTimeDisplay();
            }
        });
    }

    if (origPrevFrame) {
        origPrevFrame.addEventListener('click', () => {
            if (originalVideo) {
                const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 30.0;
                const cur = isFinite(originalVideo.currentTime) ? originalVideo.currentTime : 0;
                const prevTime = Math.max(0, cur - (1.0 / fps));
                if (isFinite(prevTime)) originalVideo.currentTime = prevTime;
                updateOrigTimeDisplay();
            }
        });
    }

    if (origNextFrame) {
        origNextFrame.addEventListener('click', () => {
            if (originalVideo) {
                const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 30.0;
                const dur = isFinite(originalVideo.duration) && originalVideo.duration > 0 ? originalVideo.duration : (currentVideoMeta.duration || 0);
                const cur = isFinite(originalVideo.currentTime) ? originalVideo.currentTime : 0;
                const nextTime = dur > 0 ? Math.min(dur, cur + (1.0 / fps)) : cur + (1.0 / fps);
                if (isFinite(nextTime)) originalVideo.currentTime = nextTime;
                updateOrigTimeDisplay();
            }
        });
    }

    if (origLastFrame) {
        origLastFrame.addEventListener('click', () => {
            if (originalVideo) {
                const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 30.0;
                const dur = isFinite(originalVideo.duration) && originalVideo.duration > 0 ? originalVideo.duration : (currentVideoMeta.duration || 0);
                if (dur > 0) {
                    const lastTime = Math.max(0, dur - (1.0 / fps));
                    if (isFinite(lastTime)) originalVideo.currentTime = lastTime;
                }
                updateOrigTimeDisplay();
            }
        });
    }

    // Original Video Playback Listeners
    if (originalVideo) {
        originalVideo.addEventListener('play', () => {
            if (origPlayPause) origPlayPause.innerHTML = '&#10074;&#10074;';
        });
        originalVideo.addEventListener('pause', () => {
            if (origPlayPause) origPlayPause.innerHTML = '&#9654;';
        });
        originalVideo.addEventListener('loadedmetadata', () => {
            if (origSeekbar && isFinite(originalVideo.duration) && originalVideo.duration > 0) {
                origSeekbar.max = originalVideo.duration;
            }
            if (originalVideo.videoWidth > 0 && originalVideo.videoHeight > 0) {
                setOrigVRMode(false);
            }
            updateOrigTimeDisplay();
        });
        originalVideo.addEventListener('loadeddata', () => {
            if (originalImg) originalImg.style.display = 'none';
            if (!isOrigVRMode) {
                originalVideo.style.display = 'block';
            } else {
                originalVideo.style.display = 'none';
                if (playerOrigContainer) playerOrigContainer.style.display = 'block';
            }
            updateOrigTimeDisplay();
        });
        originalVideo.addEventListener('canplay', () => {
            if (originalImg) originalImg.style.display = 'none';
            if (!isOrigVRMode) {
                originalVideo.style.display = 'block';
            } else {
                originalVideo.style.display = 'none';
                if (playerOrigContainer) playerOrigContainer.style.display = 'block';
            }
            updateOrigTimeDisplay();
        });
        originalVideo.addEventListener('durationchange', () => {
            if (origSeekbar && isFinite(originalVideo.duration) && originalVideo.duration > 0) {
                origSeekbar.max = originalVideo.duration;
            }
            updateOrigTimeDisplay();
        });
        originalVideo.addEventListener('timeupdate', () => {
            if (origSeekbar && !origSeekbar.dataset.dragging && isFinite(originalVideo.currentTime)) {
                origSeekbar.value = originalVideo.currentTime;
            }
            updateOrigTimeDisplay();
        });
    }

    if (origSeekbar) {
        origSeekbar.addEventListener('input', () => {
            origSeekbar.dataset.dragging = 'true';
            const val = parseFloat(origSeekbar.value);
            if (isFinite(val) && originalVideo) {
                originalVideo.currentTime = val;
            }
            updateOrigTimeDisplay();
        });
        origSeekbar.addEventListener('change', () => {
            delete origSeekbar.dataset.dragging;
        });
    }

    if (origVolume) {
        origVolume.addEventListener('input', () => {
            originalVideo.volume = parseFloat(origVolume.value);
            if (origMute) {
                if (originalVideo.volume === 0) {
                    origMute.innerHTML = '&#128263;';
                } else {
                    origMute.innerHTML = '&#128266;';
                }
            }
        });
    }

    if (origMute) {
        origMute.addEventListener('click', () => {
            if (originalVideo.muted) {
                originalVideo.muted = false;
                origMute.innerHTML = '&#128266;';
                if (origVolume) origVolume.value = originalVideo.volume;
            } else {
                originalVideo.muted = true;
                origMute.innerHTML = '&#128263;';
                if (origVolume) origVolume.value = 0;
            }
        });
    }



    // Input selection change
    options.input.addEventListener('change', async () => {
        const loadTestNumberInput = document.getElementById('load_test_number');
        if (loadTestNumberInput) loadTestNumberInput.value = '';

        currentJobId = '';
        sessionStorage.removeItem('current_tab_job_id');
        lastHandledCompletedOutput = null;

        [videoStitched, videoTelemetry, videoKopf, videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, video360].forEach(v => {
            if (v) {
                v.removeAttribute('data-loaded-src');
                v.removeAttribute('data-pending-src');
                v.removeAttribute('src');
                try { v.pause(); } catch(e) {}
            }
        });
        [tabStitched, tabTelemetry, tabKopf, tabKabsch, tabVidstab, tabCinematic, tabHorizon, tabTraveldir, tab360].forEach(tab => {
            if (tab) tab.classList.add('hidden');
        });

        detectInputFeatures(options.input.value);
        await updateOutputName();
        loadVideoMetadata();   // fire-and-forget: do NOT await — ffprobe can run in background
        generatePreview();

        const activeTab = document.querySelector('.viewport-tabs .tab-btn.active');
        if (activeTab && activeTab.classList.contains('hidden')) {
            switchTab('preview', true);
        }
    });
    updateOutputName();

    // Sync start frame inputs & sliders
    if (trimStartFrame && trimStartSlider) {
        trimStartFrame.addEventListener('input', () => {
            let val = parseInt(trimStartFrame.value) || 0;
            val = Math.max(0, Math.min(val, currentVideoMeta.total_frames));
            trimStartSlider.value = val;
            updateTrimTimeLabels();
        });
        trimStartSlider.addEventListener('input', () => {
            trimStartFrame.value = trimStartSlider.value;
            updateTrimTimeLabels();
        });
    }

    // Sync end frame inputs & sliders
    if (trimEndFrame && trimEndSlider) {
        trimEndFrame.addEventListener('input', () => {
            let val = parseInt(trimEndFrame.value) || 0;
            val = Math.max(0, Math.min(val, currentVideoMeta.total_frames));
            trimEndSlider.value = val;
            updateTrimTimeLabels();
        });
        trimEndSlider.addEventListener('input', () => {
            trimEndFrame.value = trimEndSlider.value;
            updateTrimTimeLabels();
        });
    }

    // Set start / end to playhead buttons
    if (btnSetStart && originalVideo) {
        btnSetStart.addEventListener('click', () => {
            const curTime = originalVideo.currentTime || 0;
            const frame = Math.round(curTime * currentVideoMeta.fps);
            trimStartFrame.value = frame;
            trimStartSlider.value = frame;
            updateTrimTimeLabels();
        });
    }
    if (btnSetEnd && originalVideo) {
        btnSetEnd.addEventListener('click', () => {
            const curTime = originalVideo.currentTime || 0;
            const frame = Math.round(curTime * currentVideoMeta.fps);
            trimEndFrame.value = frame;
            trimEndSlider.value = frame;
            updateTrimTimeLabels();
        });
    }

    // Auto-detect calibration / warm-up period button
    if (btnDetectWarmup) {
        btnDetectWarmup.addEventListener('click', async () => {
            const videoFile = options.input.value;
            if (!videoFile) {
                alert('Please select an input video first.');
                return;
            }

            const origHtml = btnDetectWarmup.innerHTML;
            btnDetectWarmup.disabled = true;
            btnDetectWarmup.innerHTML = '<span class="spinner" style="width:12px; height:12px; border-width:2px; display:inline-block; vertical-align:middle; margin-right:4px;"></span> Inspecting...';
            if (warmupDetectStatus) {
                warmupDetectStatus.style.display = 'block';
                warmupDetectStatus.style.borderLeftColor = '#818cf8';
                warmupDetectStatus.innerHTML = '<span style="color:#818cf8;">⏳ Inspecting video telemetry and initial frames for calibration/warm-up...</span>';
            }

            try {
                const response = await fetch(`api/v1/videos/detect-warmup?input=${encodeURIComponent(videoFile)}`);
                const data = await response.json();

                if (data.status === 'success') {
                    if (data.has_warmup && data.start_frame > 0) {
                        const frame = Math.max(0, Math.min(data.start_frame, currentVideoMeta.total_frames || data.total_frames));
                        if (trimStartFrame) trimStartFrame.value = frame;
                        if (trimStartSlider) trimStartSlider.value = frame;
                        updateTrimTimeLabels();
                        updateCroppedOutputName();

                        if (originalVideo) {
                            originalVideo.currentTime = data.start_time || (frame / (currentVideoMeta.fps || data.fps || 30));
                        }

                        if (warmupDetectStatus) {
                            warmupDetectStatus.style.display = 'block';
                            warmupDetectStatus.style.borderLeftColor = '#34d399';
                            warmupDetectStatus.innerHTML = `<b style="color:#34d399;">✅ Warm-up Detected:</b> Frame <b>${frame}</b> (${(data.start_time || 0).toFixed(2)}s). Prefilled Start Frame.<br><span style="color:#cbd5e1; font-size:10px;">${escapeHtml(data.reason || '')}</span>`;
                        }
                    } else {
                        if (warmupDetectStatus) {
                            warmupDetectStatus.style.display = 'block';
                            warmupDetectStatus.style.borderLeftColor = '#38bdf8';
                            warmupDetectStatus.innerHTML = `<span style="color:#38bdf8;">ℹ️ No calibration/warm-up period detected</span> (video starts immediately). Start frame remains 0.`;
                        }
                    }
                } else {
                    if (warmupDetectStatus) {
                        warmupDetectStatus.style.display = 'block';
                        warmupDetectStatus.style.borderLeftColor = '#f87171';
                        warmupDetectStatus.innerHTML = `<span style="color:#f87171;">❌ Inspection error:</span> ${escapeHtml(data.error || 'Failed to detect warmup')}`;
                    }
                }
            } catch (err) {
                console.error('Error auto-detecting warmup:', err);
                if (warmupDetectStatus) {
                    warmupDetectStatus.style.display = 'block';
                    warmupDetectStatus.style.borderLeftColor = '#f87171';
                    warmupDetectStatus.innerHTML = `<span style="color:#f87171;">❌ Error communicating with server:</span> ${escapeHtml(err.message)}`;
                }

            } finally {
                btnDetectWarmup.disabled = false;
                btnDetectWarmup.innerHTML = origHtml;
            }
        });
    }

    // Video Crop Result Modal handlers
    function showCropModal({ type = 'success', message = '', filePath = '' } = {}) {
        const modal = document.getElementById('modal-crop-result');
        const card = document.getElementById('crop-modal-card');
        const titleEl = document.getElementById('crop-modal-title');
        const headingEl = document.getElementById('crop-modal-heading');
        const iconEl = document.getElementById('crop-modal-icon');
        const messageEl = document.getElementById('crop-modal-message');
        const pathBox = document.getElementById('crop-modal-path-box');
        const pathText = document.getElementById('crop-modal-path-text');
        const hintBox = document.getElementById('crop-modal-hint');
        const hintIcon = document.getElementById('crop-modal-hint-icon');
        const hintText = document.getElementById('crop-modal-hint-text');
        const okBtn = document.getElementById('btn-ok-crop-modal');

        if (!modal) return;

        if (messageEl) messageEl.textContent = message;

        if (type === 'success') {
            if (card) card.style.borderColor = 'rgba(16, 185, 129, 0.5)';
            if (titleEl) titleEl.style.color = '#10b981';
            if (headingEl) headingEl.textContent = 'Video Cropped Successfully';
            if (iconEl) {
                iconEl.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
            }
            if (pathBox) {
                if (filePath) {
                    pathBox.style.display = 'flex';
                    if (pathText) pathText.textContent = filePath;
                } else {
                    pathBox.style.display = 'none';
                }
            }
            if (hintBox) {
                hintBox.style.display = 'flex';
                hintBox.style.background = 'rgba(16, 185, 129, 0.1)';
                hintBox.style.borderColor = 'rgba(16, 185, 129, 0.25)';
                if (hintIcon) {
                    hintIcon.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 14 14"></polyline></svg>`;
                }
                if (hintText) hintText.textContent = 'Trimmed video frames and embedded telemetry metadata (atoms) have been preserved.';
            }
            if (okBtn) {
                okBtn.style.background = '#10b981';
                okBtn.textContent = 'OK';
            }
        } else if (type === 'error') {
            if (card) card.style.borderColor = 'rgba(239, 68, 68, 0.5)';
            if (titleEl) titleEl.style.color = '#ef4444';
            if (headingEl) headingEl.textContent = 'Cropping Error';
            if (iconEl) {
                iconEl.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
            }
            if (pathBox) pathBox.style.display = 'none';
            if (hintBox) {
                hintBox.style.display = 'flex';
                hintBox.style.background = 'rgba(239, 68, 68, 0.1)';
                hintBox.style.borderColor = 'rgba(239, 68, 68, 0.25)';
                if (hintIcon) {
                    hintIcon.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"></polygon><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
                }
                if (hintText) hintText.textContent = 'Please choose a different output filename or check your start/end frame range.';
            }
            if (okBtn) {
                okBtn.style.background = '#ef4444';
                okBtn.textContent = 'Dismiss';
            }
        } else {
            if (card) card.style.borderColor = 'rgba(245, 158, 11, 0.5)';
            if (titleEl) titleEl.style.color = '#f59e0b';
            if (headingEl) headingEl.textContent = 'Input Required';
            if (iconEl) {
                iconEl.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`;
            }
            if (pathBox) pathBox.style.display = 'none';
            if (hintBox) hintBox.style.display = 'none';
            if (okBtn) {
                okBtn.style.background = '#f59e0b';
                okBtn.textContent = 'OK';
            }
        }

        modal.style.display = 'flex';
    }

    function hideCropModal() {
        const modal = document.getElementById('modal-crop-result');
        if (modal) modal.style.display = 'none';
    }

    document.getElementById('btn-close-crop-modal-x')?.addEventListener('click', hideCropModal);
    document.getElementById('btn-ok-crop-modal')?.addEventListener('click', hideCropModal);
    document.getElementById('modal-crop-result')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-crop-result') hideCropModal();
    });
    document.getElementById('btn-copy-crop-path')?.addEventListener('click', async () => {
        const pathText = document.getElementById('crop-modal-path-text')?.textContent;
        if (!pathText) return;
        try {
            await navigator.clipboard.writeText(pathText);
            const btn = document.getElementById('btn-copy-crop-path');
            if (btn) {
                const origHtml = btn.innerHTML;
                btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg> <span>Copied!</span>`;
                btn.style.color = '#10b981';
                setTimeout(() => {
                    btn.innerHTML = origHtml;
                    btn.style.color = '';
                }, 2000);
            }
        } catch (err) {
            console.warn('Failed to copy to clipboard:', err);
        }
    });

    // Crop and Save Button
    if (btnCropSave) {
        btnCropSave.addEventListener('click', async () => {
            const videoFile = options.input.value;
            const outFile   = croppedOutputName.value;
            if (!videoFile || !outFile) {
                showCropModal({ type: 'warning', message: 'Please select an input video and provide a cropped filename.' });
                return;
            }

            const startF = parseInt(trimStartFrame.value) || 0;
            const endF   = parseInt(trimEndFrame.value) || 0;

            btnCropSave.disabled = true;
            btnCropSave.innerHTML = '<span class="spinner" style="margin-right:8px;"></span> Cropping...';

            const cmdStr = `python -B crop_camera_video.py --input "${videoFile}" --output "${outFile}" --start_frame ${startF} --end_frame ${endF}`;
            logCommand(cmdStr);

            const logOutput = document.getElementById('log-output');
            if (logOutput) {
                logOutput.textContent = `[Cropping & Telemetry Patching Started]\n$ ${cmdStr}\n\n`;
            }

            const formData = new FormData();
            /* action:crop_original → api/v1/videos/crop */
            formData.append('input', videoFile);
            formData.append('output', outFile);
            formData.append('start_frame', startF);
            formData.append('end_frame', endF);

            try {
                const response = await fetch('api/v1/videos/crop', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                if (data.output) {
                    const lines = data.output.split('\n');
                    lines.forEach(line => {
                        if (line.includes('Running FFmpeg:')) {
                            const ffmpegCmd = line.replace('Running FFmpeg:', '').trim();
                            logCommand(ffmpegCmd);
                        }
                    });
                }
                if (data.status === 'success') {
                    if (logOutput && data.output) {
                        logOutput.textContent = data.output;
                    }
                    const savedFile = data.output_file || (data.filename ? (data.filename.startsWith('data/input/videos/') ? data.filename : `data/input/videos/${data.filename}`) : outFile);
                    showCropModal({ type: 'success', message: data.message, filePath: savedFile });
                    await refreshInputVideos(savedFile, true);
                } else {
                    if (logOutput && (data.details || data.error)) {
                        logOutput.textContent = data.details || data.error;
                    }
                    showCropModal({ type: 'error', message: 'Cropping error: ' + (data.error || 'Unknown error') });
                }
            } catch (e) {
                console.error(e);
                showCropModal({ type: 'error', message: 'Cropping request failed. Please check network connection and server status.' });
            } finally {
                btnCropSave.disabled = false;
                btnCropSave.innerHTML = 'Crop &amp; Save Original';
            }
        });
    }

    // Initial status check + polling (handled dynamically on job start)
    checkStatus();
    
    // Clear polling on unload
    window.addEventListener('beforeunload', () => {
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
    });

    // Initial metadata load
    loadVideoMetadata();

    // Initial Preview
    generatePreview();

    // Default to Stitched Preview Frame tab on load
    switchTab('preview');
}

function updateStatusHeaderText(status) {
    const statusHeaderText = document.getElementById('status-header-text');
    if (!statusHeaderText) return;
    switch (status) {
        case 'stitching':
            statusHeaderText.textContent = 'Stitching Status:';
            break;
        case 'stabilizing':
            statusHeaderText.textContent = 'Stabilization Status:';
            break;
        case 'nadiring':
            statusHeaderText.textContent = 'Nadir Logo Status:';
            break;
        case 'injecting':
            statusHeaderText.textContent = 'Metadata Injection Status:';
            break;
        case 'reporting':
            statusHeaderText.textContent = 'Report Generation Status:';
            break;
        case 'exporting':
            statusHeaderText.textContent = 'Street View Export Status:';
            break;
        case 'awaiting_transforms':
            statusHeaderText.textContent = 'Transform Selection Status:';
            break;
        case 'completed':
        case 'failed':
        case 'idle':
            statusHeaderText.textContent = 'Processing Status:';
            break;
        default:
            statusHeaderText.textContent = 'Processing Status:';
            break;
    }
}

let _autoOutputBase = '';
let _autoOutputSuffix = 0;

async function updateOutputName() {
    const val = options.input ? options.input.value : '';
    if (val) {
        const dotIndex = val.lastIndexOf('.');
        const base = dotIndex === -1 ? val : val.substring(0, dotIndex);
        let cleanBase = base.includes('/') ? base.substring(base.lastIndexOf('/') + 1) : base;
        cleanBase = cleanBase.includes('\\') ? cleanBase.substring(cleanBase.lastIndexOf('\\') + 1) : cleanBase;
        if (cleanBase !== _autoOutputBase) {
            _autoOutputBase = cleanBase;
            try {
                const resp = await fetch('api/v1/jobs/next-prefix');
                const data = await resp.json();
                if (data && data.status === 'success' && data.prefix) {
                    _autoOutputSuffix = data.prefix;
                } else {
                    _autoOutputSuffix = 1001;
                }
            } catch (e) {
                _autoOutputSuffix = 1001;
            }
        }
        const tag = '_out_';
        if (options.output) {
            const isPhoto = isPhotoFile(val);
            const ext = isPhoto ? (val.substring(dotIndex).toLowerCase() || '.jpg') : '.MP4';
            options.output.value = 'data/runtime/work/' + cleanBase + tag + _autoOutputSuffix + ext;
        }
    }
    updateStatusHeaderText();
}

let userSelectedTab = null;
let activatedIntermediateSteps = new Set();
let _generic360PlaceholderCanvas = null;
let _generic360PlaceholderTexture = null;

function createGeneric360PlaceholderCanvas() {
    const canvas = document.createElement('canvas');
    canvas.width = 2048;
    canvas.height = 1024;
    const ctx = canvas.getContext('2d');
    if (!ctx) return canvas;

    const w = canvas.width;
    const h = canvas.height;

    // Background gradient: Deep modern dark cyber-grid theme
    const bgGrad = ctx.createLinearGradient(0, 0, 0, h);
    bgGrad.addColorStop(0.0, '#040711');
    bgGrad.addColorStop(0.5, '#0b1329');
    bgGrad.addColorStop(1.0, '#040711');
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, w, h);

    // Subtle Radial Glow at Center (Equator / Prime Meridian)
    const radGrad = ctx.createRadialGradient(w / 2, h / 2, 20, w / 2, h / 2, w / 2);
    radGrad.addColorStop(0, 'rgba(56, 189, 248, 0.12)');
    radGrad.addColorStop(0.7, 'rgba(14, 165, 233, 0.03)');
    radGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
    ctx.fillStyle = radGrad;
    ctx.fillRect(0, 0, w, h);

    // Longitude lines (every 15 degrees)
    for (let deg = 0; deg < 360; deg += 15) {
        const x = (deg / 360) * w;
        ctx.beginPath();
        ctx.strokeStyle = (deg % 90 === 0) ? 'rgba(56, 189, 248, 0.45)' : ((deg % 45 === 0) ? 'rgba(56, 189, 248, 0.22)' : 'rgba(56, 189, 248, 0.09)');
        ctx.lineWidth = (deg % 90 === 0) ? 2.5 : 1;
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
    }

    // Latitude lines (every 15 degrees: from -75 to +75)
    for (let deg = -75; deg <= 75; deg += 15) {
        const y = ((90 - deg) / 180) * h;
        ctx.beginPath();
        ctx.strokeStyle = (deg === 0) ? 'rgba(245, 158, 11, 0.65)' : ((deg % 45 === 0) ? 'rgba(56, 189, 248, 0.28)' : 'rgba(56, 189, 248, 0.1)');
        ctx.lineWidth = (deg === 0) ? 3 : 1;
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
    }

    // Direction Headings along Equator (y = h/2)
    const eqY = h / 2;
    const cardinals = [
        { label: 'BACK [180°]', deg: 0, color: '#ef4444' },
        { label: 'LEFT [-90°]', deg: 90, color: '#38bdf8' },
        { label: 'FRONT [0°]', deg: 180, color: '#a78bfa' },
        { label: 'RIGHT [+90°]', deg: 270, color: '#38bdf8' },
        { label: 'BACK [180°]', deg: 360, color: '#ef4444' }
    ];

    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    for (const card of cardinals) {
        const x = (card.deg / 360) * w;
        ctx.fillStyle = card.color;
        ctx.font = 'bold 24px "Segoe UI", Inter, sans-serif';
        ctx.fillText(card.label, x, eqY - 20);

        ctx.beginPath();
        ctx.arc(x, eqY, 6, 0, Math.PI * 2);
        ctx.fillStyle = card.color;
        ctx.fill();
    }

    // Zenith (+90°) & Nadir (-90°) labels
    ctx.font = 'bold 20px "Segoe UI", Inter, sans-serif';
    ctx.fillStyle = '#64748b';
    ctx.fillText('ZENITH [+90°]', w / 2, 45);
    ctx.fillText('NADIR [-90°]', w / 2, h - 30);

    // Modern Glassmorphism Center Info Box across each viewing quadrant
    const boxPositions = [w * 0.25, w * 0.5, w * 0.75];
    for (const cx of boxPositions) {
        const bw = 520;
        const bh = 150;
        const bx = cx - bw / 2;
        const by = eqY + 30;

        ctx.fillStyle = 'rgba(15, 23, 42, 0.88)';
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.4)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        if (typeof ctx.roundRect === 'function') {
            ctx.roundRect(bx, by, bw, bh, 14);
        } else {
            ctx.rect(bx, by, bw, bh);
        }
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = '#38bdf8';
        ctx.font = 'bold 24px "Segoe UI", Inter, sans-serif';
        ctx.fillText('🎬 360° VIDEO PROCESSING', cx, by + 48);

        ctx.fillStyle = '#cbd5e1';
        ctx.font = '16px "Segoe UI", Inter, sans-serif';
        ctx.fillText('Video stage output not ready yet', cx, by + 86);

        ctx.fillStyle = '#f59e0b';
        ctx.font = 'italic 15px "Segoe UI", Inter, sans-serif';
        ctx.fillText('⏳ Awaiting pipeline completion...', cx, by + 120);
    }

    return canvas;
}

function getGeneric360PlaceholderTexture() {
    if (!_generic360PlaceholderTexture) {
        if (!_generic360PlaceholderCanvas) {
            _generic360PlaceholderCanvas = createGeneric360PlaceholderCanvas();
        }
        if (typeof THREE !== 'undefined' && THREE.CanvasTexture) {
            _generic360PlaceholderTexture = new THREE.CanvasTexture(_generic360PlaceholderCanvas);
            if (THREE.SRGBColorSpace) _generic360PlaceholderTexture.colorSpace = THREE.SRGBColorSpace;
            _generic360PlaceholderTexture.needsUpdate = true;
        }
    }
    return _generic360PlaceholderTexture;
}

function updateThreeSphereTexture(sphereMesh, videoEl, videoTexture) {
    if (!sphereMesh || !sphereMesh.material) return;
    const hasReadyVideo = Boolean(
        videoEl &&
        videoEl.getAttribute('data-loaded-src') &&
        videoEl.src &&
        !videoEl.src.endsWith('/') &&
        videoEl.readyState >= 1 &&
        !videoEl.error
    );

    if (hasReadyVideo && videoTexture) {
        if (sphereMesh.material.map !== videoTexture) {
            sphereMesh.material.map = videoTexture;
            sphereMesh.material.needsUpdate = true;
        }
        videoTexture.needsUpdate = true;
    } else {
        const placeholderTex = getGeneric360PlaceholderTexture();
        if (placeholderTex && sphereMesh.material.map !== placeholderTex) {
            sphereMesh.material.map = placeholderTex;
            sphereMesh.material.needsUpdate = true;
        }
    }
}

function getSphereAndTextureForTab(tab) {
    if (tab === 'stitched')  return { sphere: typeof threeStitchedSphere !== 'undefined' ? threeStitchedSphere : null, texture: typeof threeStitchedTexture !== 'undefined' ? threeStitchedTexture : null };
    if (tab === 'telemetry') return { sphere: typeof threeTelemetrySphere !== 'undefined' ? threeTelemetrySphere : null, texture: typeof threeTelemetryTexture !== 'undefined' ? threeTelemetryTexture : null };
    if (tab === 'kabsch')    return { sphere: typeof threeKabschSphere !== 'undefined' ? threeKabschSphere : null, texture: typeof threeKabschTexture !== 'undefined' ? threeKabschTexture : null };
    if (tab === 'kopf')      return { sphere: typeof threeKopfSphere !== 'undefined' ? threeKopfSphere : null, texture: typeof threeKopfTexture !== 'undefined' ? threeKopfTexture : null };
    if (tab === 'vidstab')        return { sphere: typeof threeVidstabSphere !== 'undefined' ? threeVidstabSphere : null, texture: typeof threeVidstabTexture !== 'undefined' ? threeVidstabTexture : null };
    if (tab === 'cinematic')      return { sphere: typeof threeCinematicSphere !== 'undefined' ? threeCinematicSphere : null, texture: typeof threeCinematicTexture !== 'undefined' ? threeCinematicTexture : null };
    if (tab === 'horizon')        return { sphere: typeof threeHorizonSphere !== 'undefined' ? threeHorizonSphere : null, texture: typeof threeHorizonTexture !== 'undefined' ? threeHorizonTexture : null };
    if (tab === 'traveldir') return { sphere: typeof threeTraveldirSphere !== 'undefined' ? threeTraveldirSphere : null, texture: typeof threeTraveldirTexture !== 'undefined' ? threeTraveldirTexture : null };
    if (tab === '360')            return { sphere: typeof threeSphere !== 'undefined' ? threeSphere : null, texture: typeof threeTexture !== 'undefined' ? threeTexture : null };
    return { sphere: null, texture: null };
}

function resetAllIntermediateVideoSlots() {
    const slots = [
        { video: videoStitched,      tab: tabStitched,      sphere: typeof threeStitchedSphere !== 'undefined' ? threeStitchedSphere : null },
        { video: videoTelemetry,     tab: tabTelemetry,     sphere: typeof threeTelemetrySphere !== 'undefined' ? threeTelemetrySphere : null },
        { video: videoKabsch,        tab: tabKabsch,        sphere: typeof threeKabschSphere !== 'undefined' ? threeKabschSphere : null },
        { video: videoKopf,          tab: tabKopf,          sphere: typeof threeKopfSphere !== 'undefined' ? threeKopfSphere : null },
        { video: videoVidstab,       tab: tabVidstab,       sphere: typeof threeVidstabSphere !== 'undefined' ? threeVidstabSphere : null },
        { video: videoCinematic,      tab: tabCinematic,      sphere: typeof threeCinematicSphere !== 'undefined' ? threeCinematicSphere : null },
        { video: videoHorizon,       tab: tabHorizon,       sphere: typeof threeHorizonSphere !== 'undefined' ? threeHorizonSphere : null },
        { video: videoTraveldir, tab: tabTraveldir, sphere: typeof threeTraveldirSphere !== 'undefined' ? threeTraveldirSphere : null },
        { video: video360,           tab: tab360,           sphere: typeof threeSphere !== 'undefined' ? threeSphere : null }
    ];

    slots.forEach(slot => {
        if (slot.video) {
            slot.video.pause();
            slot.video.removeAttribute('data-pending-src');
            slot.video.removeAttribute('data-loaded-src');
            slot.video.removeAttribute('data-file-path');
            slot.video.removeAttribute('src');
            try { slot.video.load(); } catch (e) {}
        }
        if (slot.tab) {
            slot.tab.classList.add('hidden');
        }
        if (slot.sphere) {
            updateThreeSphereTexture(slot.sphere, null, null);
        }
    });

    activatedIntermediateSteps.clear();
    const cpCompletedBox = document.getElementById('horizon-completed-link-container');
    if (cpCompletedBox) cpCompletedBox.style.display = 'none';
    const vrHorizonEditorBtn = document.getElementById('vr-horizon-editor-btn');
    if (vrHorizonEditorBtn) vrHorizonEditorBtn.style.display = 'none';
    updateActiveVideoFilenameLabel();
    ['orig-loader', 'stitched-loader', 'telemetry-loader', 'vidstab-loader', 'kabsch-loader', 'kopf-loader', 'cinematic-loader', 'horizon-loader', 'traveldir-loader', 'vr-loader'].forEach(hideViewportLoader);
}

let _lastAutoSwitchedStage = null;

function getActivePipelineStage(status, phase) {
    const p = (phase || '').toLowerCase();
    const st = (status || '').toLowerCase();
    if (p.includes('traveldir') || p.includes('subject_lock')) return 'traveldir';
    if (p.includes('horizon') || p.includes('checkpoint')) return 'horizon';
    if (p.includes('cinematic')) return 'cinematic';
    if (p.includes('vidstab') || p.includes('optical')) return 'vidstab';
    if (p.includes('kabsch')) return 'kabsch';
    if (p.includes('kopf')) return 'kopf';
    if (p.includes('telemetry')) return 'telemetry';
    if (st === 'stitching' || p.includes('stitch')) return 'stitched';
    return '';
}

function syncPipelineTabsVisibility(data) {
    if (!data) return;

    const activeStatuses = ['stitching', 'stabilizing', 'nadiring', 'injecting', 'reporting', 'exporting', 'processing', 'awaiting_transforms'];
    const isJobActive = activeStatuses.includes(data.status);
    const isCompleted = (data.status === 'completed');

    if (!isJobActive && !isCompleted) return;
    if (!currentJobId && !isJobActive) return;

    // Reveal tab dynamically ONLY for the currently active processing stage
    const currentActiveStage = getActivePipelineStage(data.status, data.phase);

    if (isJobActive && currentActiveStage) {
        let activeTabEl = null;
        if (currentActiveStage === 'stitched') activeTabEl = tabStitched;
        else if (currentActiveStage === 'telemetry') activeTabEl = tabTelemetry;
        else if (currentActiveStage === 'kopf') activeTabEl = tabKopf;
        else if (currentActiveStage === 'kabsch') activeTabEl = tabKabsch;
        else if (currentActiveStage === 'vidstab') activeTabEl = tabVidstab;
        else if (currentActiveStage === 'cinematic') activeTabEl = tabCinematic;
        else if (currentActiveStage === 'horizon') activeTabEl = tabHorizon;
        else if (currentActiveStage === 'traveldir') activeTabEl = tabTraveldir;

        if (activeTabEl) {
            activeTabEl.classList.remove('hidden');
        }

        if (userSelectedTab === null && _lastAutoSwitchedStage !== currentActiveStage) {
            _lastAutoSwitchedStage = currentActiveStage;
            switchTab(currentActiveStage);
        }
    }

    // tab360 (Final Viewport) MUST ONLY appear when status is 'completed'
    if (!isCompleted && tab360) {
        tab360.classList.add('hidden');
    }
}

let _isBatchLoadingIntermediates = false;
const _activeVideoLRU = [];
const MAX_ACTIVE_VIDEOS = 2; // Active tab + at most 1 previously viewed tab

function touchVideoInLRU(videoEl) {
    if (!videoEl) return;
    const idx = _activeVideoLRU.indexOf(videoEl);
    if (idx !== -1) {
        _activeVideoLRU.splice(idx, 1);
    }
    _activeVideoLRU.push(videoEl);

    while (_activeVideoLRU.length > MAX_ACTIVE_VIDEOS) {
        const victim = _activeVideoLRU.shift();
        if (victim && victim !== videoEl) {
            evictVideoElement(victim);
        }
    }
}

function evictVideoElement(videoEl) {
    if (!videoEl) return;
    try {
        if (!videoEl.paused) videoEl.pause();
        if (isFinite(videoEl.currentTime) && videoEl.currentTime > 0) {
            videoEl.setAttribute('data-last-time', videoEl.currentTime);
        }
        // Release hardware decoder session
        videoEl.removeAttribute('src');
        videoEl.removeAttribute('data-loaded-src');
        videoEl.load();
    } catch (e) {
        console.warn("Error evicting video element:", e);
    }
}

function getActiveTabVideo(activeTabId) {
    if (activeTabId === 'tab-stitched') return videoStitched;
    if (activeTabId === 'tab-telemetry') return videoTelemetry;
    if (activeTabId === 'tab-kabsch') return videoKabsch;
    if (activeTabId === 'tab-kopf') return videoKopf;
    if (activeTabId === 'tab-vidstab') return videoVidstab;
    if (activeTabId === 'tab-cinematic') return videoCinematic;
    if (activeTabId === 'tab-horizon') return videoHorizon;
    if (activeTabId === 'tab-traveldir') return videoTraveldir;
    if (activeTabId === 'tab-original') return originalVideo;
    if (activeTabId === 'tab-360') return video360;
    return null;
}

function updateIntermediateVideoSlot(videoEl, tabEl, filePath, stepKey, t) {
    if (!videoEl || !tabEl) return;
    if (filePath) {
        tabEl.classList.remove('hidden');
        const canonicalPath = resolveVideoSrc(filePath);
        const prevPath = videoEl.getAttribute('data-file-path');
        if (prevPath !== canonicalPath) {
            videoEl.setAttribute('data-file-path', canonicalPath);
            const pendingUrl = `${canonicalPath}?t=${t}`;
            videoEl.setAttribute('data-pending-src', pendingUrl);

            if (userSelectedTab === null && stepKey && !_isBatchLoadingIntermediates) {
                const activeTabBtn = document.querySelector('.viewport-tabs .tab-btn.active');
                const activeTabId = activeTabBtn ? activeTabBtn.id : '';
                if (activeTabId === 'tab-stitched' && stepKey === 'telemetry') {
                    if (userSelectedTab === null) {
                        switchTab(stepKey);
                    }
                }
                if (stepKey && !activatedIntermediateSteps.has(stepKey)) {
                    activatedIntermediateSteps.add(stepKey);
                    if (userSelectedTab === null) {
                        switchTab(stepKey);
                    }
                }
            }
            const activeTab = document.querySelector('.viewport-tabs .tab-btn.active');
            if (activeTab && activeTab.id === tabEl.id) {
                ensureTabVideoLoaded(stepKey || '360');
            }
        }
    } else {
        videoEl.removeAttribute('data-pending-src');
        videoEl.removeAttribute('data-file-path');
        videoEl.removeAttribute('data-loaded-src');
        videoEl.removeAttribute('src');
        videoEl.pause();
        try { videoEl.load(); } catch (e) {}

        const activeStatuses = ['stitching', 'stabilizing', 'nadiring', 'injecting', 'reporting', 'exporting', 'processing', 'awaiting_transforms'];
        const isJobActive = lastStatusData && activeStatuses.includes(lastStatusData.status);
        const currentActiveStage = isJobActive ? getActivePipelineStage(lastStatusData.status, lastStatusData.phase) : '';
        const isCurrentStageProcessing = isJobActive && stepKey && (currentActiveStage === stepKey);

        if (!isCurrentStageProcessing) {
            tabEl.classList.add('hidden');
        }

        const { sphere } = getSphereAndTextureForTab(stepKey || '360');
        if (sphere) updateThreeSphereTexture(sphere, null, null);
    }
}

function ensureTabVideoLoaded(tab) {
    let videoEl = null;
    let loaderId = null;
    let loaderText = '';
    let initFunc = null;

    if (tab === 'original') {
        videoEl = originalVideo;
        loaderId = 'orig-loader';
        loaderText = 'Loading Original Video...';
        initFunc = (typeof initThreeOrigJS === 'function' && isOrigVRMode) ? initThreeOrigJS : null;
    } else if (tab === 'stitched') {
        videoEl = videoStitched;
        loaderId = 'stitched-loader';
        loaderText = 'Loading 360 Stitched Video...';
        initFunc = (typeof initThreeStitchedJS === 'function') ? initThreeStitchedJS : null;
    } else if (tab === 'telemetry') {
        videoEl = videoTelemetry;
        loaderId = 'telemetry-loader';
        loaderText = 'Loading 360 Telemetry Video...';
        initFunc = (typeof initThreeTelemetryJS === 'function') ? initThreeTelemetryJS : null;
    } else if (tab === 'kabsch') {
        videoEl = videoKabsch;
        loaderId = 'kabsch-loader';
        loaderText = 'Loading 360 Kabsch Video...';
        initFunc = (typeof initThreeKabschJS === 'function') ? initThreeKabschJS : null;
    } else if (tab === 'kopf') {
        videoEl = videoKopf;
        loaderId = 'kopf-loader';
        loaderText = 'Loading 360 Kopf Video...';
        initFunc = (typeof initThreeKopfJS === 'function') ? initThreeKopfJS : null;
    } else if (tab === 'vidstab') {
        videoEl = videoVidstab;
        loaderId = 'vidstab-loader';
        loaderText = 'Loading 360 Vidstab Video...';
        initFunc = (typeof initThreeVidstabJS === 'function') ? initThreeVidstabJS : null;
    } else if (tab === 'cinematic') {
        videoEl = videoCinematic;
        loaderId = 'cinematic-loader';
        loaderText = 'Loading 360 Cinematic Video...';
        initFunc = (typeof initThreeCinematicJS === 'function') ? initThreeCinematicJS : null;
    } else if (tab === 'horizon') {
        videoEl = videoHorizon;
        loaderId = 'horizon-loader';
        loaderText = 'Loading 360 Horizon Video...';
        initFunc = (typeof initThreeHorizonJS === 'function') ? initThreeHorizonJS : null;
    } else if (tab === 'traveldir') {
        videoEl = videoTraveldir;
        loaderId = 'traveldir-loader';
        loaderText = 'Loading 360 Travel-Direction Video...';
        initFunc = (typeof initThreeTraveldirJS === 'function') ? initThreeTraveldirJS : null;
    } else if (tab === '360') {
        videoEl = video360;
        loaderId = 'vr-loader';
        loaderText = 'Loading 360 VR Video...';
        initFunc = (typeof initThreeJS === 'function') ? initThreeJS : null;
    }

    if (!videoEl) return;

    if (typeof initFunc === 'function') {
        initFunc();
    }

    const pendingRaw = videoEl.getAttribute('data-pending-src') || (videoEl === originalVideo && options.input && options.input.value ? options.input.value : null);
    const pendingSrc = resolveVideoSrc(pendingRaw);
    const loadedSrc = videoEl.getAttribute('data-loaded-src');

    touchVideoInLRU(videoEl);

    if (pendingSrc && (loadedSrc !== pendingSrc || !videoEl.src || videoEl.src === '' || videoEl.error)) {
        showViewportLoader(loaderId, loaderText);
        videoEl.src = pendingSrc;
        videoEl.loop = true;
        videoEl.setAttribute('data-loaded-src', pendingSrc);
        videoEl.load();

        const onReady = () => {
            hideViewportLoader(loaderId);
            const lastTime = parseFloat(videoEl.getAttribute('data-last-time') || '0');
            if (isFinite(lastTime) && lastTime > 0 && Math.abs(videoEl.currentTime - lastTime) > 0.05) {
                try { videoEl.currentTime = lastTime; } catch (e) {}
            }
            const { sphere, texture } = getSphereAndTextureForTab(tab);
            if (sphere && texture) {
                updateThreeSphereTexture(sphere, videoEl, texture);
            }
        };

        if (videoEl.readyState >= 1) {
            onReady();
        } else {
            videoEl.addEventListener('canplay', onReady, { once: true });
            videoEl.addEventListener('loadeddata', onReady, { once: true });
        }

        const { texture } = getSphereAndTextureForTab(tab);
        if (texture) texture.needsUpdate = true;
    } else if (loadedSrc) {
        hideViewportLoader(loaderId);
        const lastTime = parseFloat(videoEl.getAttribute('data-last-time') || '0');
        if (isFinite(lastTime) && lastTime > 0 && Math.abs(videoEl.currentTime - lastTime) > 0.05) {
            try { videoEl.currentTime = lastTime; } catch (e) {}
        }
        const { sphere, texture } = getSphereAndTextureForTab(tab);
        if (sphere && texture) {
            updateThreeSphereTexture(sphere, videoEl, texture);
        }
    } else if (!loadedSrc && !pendingSrc) {
        hideViewportLoader(loaderId);
        const { sphere } = getSphereAndTextureForTab(tab);
        if (sphere) {
            updateThreeSphereTexture(sphere, null, null);
        }
    }
}

let lastStatusData = null;

function updateInProgressViewport(data) {
    if (data) {
        lastStatusData = data;
    }
}

function switchTab(tab, isUserAction = false) {
    if (isUserAction) {
        userSelectedTab = tab;
    }
    ['orig-loader', 'stitched-loader', 'telemetry-loader', 'vidstab-loader', 'kabsch-loader', 'kopf-loader', 'cinematic-loader', 'horizon-loader', 'traveldir-loader', 'vr-loader'].forEach(hideViewportLoader);
    if (!isGeneratingPreview) hideViewportLoader('preview-loader');

    const allTabs   = [tabOriginal, tabPreview, tabStitched, tabTelemetry, tabKopf, tabKabsch, tabVidstab, tabCinematic, tabHorizon, tabTraveldir, tab360];
    const allViews  = [viewOriginal, viewPreview, viewStitched, viewTelemetry, viewKopf, viewKabsch, viewVidstab, viewCinematic, viewHorizon, viewTraveldir, view360];
    const allVideos = [originalVideo, videoStitched, videoTelemetry, videoKopf, videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, video360];

    let currentTimeToSync = null;
    const previouslyActiveVideo = allVideos.find(v => v && (v.src || v.currentSrc) && isFinite(v.currentTime) && v.currentTime > 0);
    if (previouslyActiveVideo) {
        currentTimeToSync = previouslyActiveVideo.currentTime;
    }

    allTabs.forEach(t => { if (t) t.classList.remove('active'); });
    allViews.forEach(v => { if (v) v.classList.remove('active'); });
    allVideos.forEach(v => { if (v && !v.paused) v.pause(); });

    ensureTabVideoLoaded(tab);

    const activeTabVideo = getActiveTabVideo('tab-' + tab);
    if (activeTabVideo && currentTimeToSync !== null) {
        if (activeTabVideo.readyState >= 1) {
            try { activeTabVideo.currentTime = currentTimeToSync; } catch (e) {}
        } else {
            activeTabVideo.setAttribute('data-last-time', currentTimeToSync);
        }
    }

    if (tab === 'original') {
        tabOriginal.classList.add('active');
        viewOriginal.classList.add('active');
        if (originalVideo) updateOrigTimeDisplay();
        if (isOrigVRMode) {
            if (!isThreeOrigInitialized) {
                initThreeOrigJS();
            } else if (threeOrigTexture) {
                threeOrigTexture.needsUpdate = true;
            }
            onWindowResizeOrig();
            setTimeout(onWindowResizeOrig, 100);
            setTimeout(onWindowResizeOrig, 300);
        }
    } else if (tab === 'preview') {
        tabPreview.classList.add('active');
        viewPreview.classList.add('active');
    } else if (tab === 'stitched') {
        if (tabStitched) tabStitched.classList.add('active');
        if (viewStitched) viewStitched.classList.add('active');
        if (!isThreeStitchedInitialized) {
            initThreeStitchedJS();
        } else if (threeStitchedTexture) {
            threeStitchedTexture.needsUpdate = true;
        }
        onWindowResizeStitched();
        setTimeout(onWindowResizeStitched, 100);
        setTimeout(onWindowResizeStitched, 300);
    } else if (tab === 'telemetry') {
        if (tabTelemetry) tabTelemetry.classList.add('active');
        if (viewTelemetry) viewTelemetry.classList.add('active');
        if (!isThreeTelemetryInitialized) {
            initThreeTelemetryJS();
        } else if (threeTelemetryTexture) {
            threeTelemetryTexture.needsUpdate = true;
        }
        onWindowResizeTelemetry();
        setTimeout(onWindowResizeTelemetry, 100);
        setTimeout(onWindowResizeTelemetry, 300);
    } else if (tab === 'kopf') {
        if (tabKopf) tabKopf.classList.add('active');
        if (viewKopf) viewKopf.classList.add('active');
        if (!isThreeKopfInitialized) {
            initThreeKopfJS();
        } else if (threeKopfTexture) {
            threeKopfTexture.needsUpdate = true;
        }
        onWindowResizeKopf();
        setTimeout(onWindowResizeKopf, 100);
        setTimeout(onWindowResizeKopf, 300);
    } else if (tab === 'kabsch') {
        if (tabKabsch) tabKabsch.classList.add('active');
        if (viewKabsch) viewKabsch.classList.add('active');
        if (!isThreeKabschInitialized) {
            initThreeKabschJS();
        } else if (threeKabschTexture) {
            threeKabschTexture.needsUpdate = true;
        }
        onWindowResizeKabsch();
        setTimeout(onWindowResizeKabsch, 100);
        setTimeout(onWindowResizeKabsch, 300);
    } else if (tab === 'vidstab') {
        if (tabVidstab) tabVidstab.classList.add('active');
        if (viewVidstab) viewVidstab.classList.add('active');
        if (!isThreeVidstabInitialized) {
            initThreeVidstabJS();
        } else if (threeVidstabTexture) {
            threeVidstabTexture.needsUpdate = true;
        }
        onWindowResizeVidstab();
        setTimeout(onWindowResizeVidstab, 100);
        setTimeout(onWindowResizeVidstab, 300);
    } else if (tab === 'cinematic') {
        if (tabCinematic) tabCinematic.classList.add('active');
        if (viewCinematic) viewCinematic.classList.add('active');
        if (!isThreeCinematicInitialized) {
            initThreeCinematicJS();
        } else if (threeCinematicTexture) {
            threeCinematicTexture.needsUpdate = true;
        }
        onWindowResizeCinematic();
        setTimeout(onWindowResizeCinematic, 100);
        setTimeout(onWindowResizeCinematic, 300);
    } else if (tab === 'horizon') {
        if (tabHorizon) tabHorizon.classList.add('active');
        if (viewHorizon) viewHorizon.classList.add('active');
        if (!isThreeHorizonInitialized) {
            initThreeHorizonJS();
        } else if (threeHorizonTexture) {
            threeHorizonTexture.needsUpdate = true;
        }
        onWindowResizeHorizon();
        setTimeout(onWindowResizeHorizon, 100);
        setTimeout(onWindowResizeHorizon, 300);
    } else if (tab === 'traveldir') {
        if (tabTraveldir) tabTraveldir.classList.add('active');
        if (viewTraveldir) viewTraveldir.classList.add('active');
        if (!isThreeTraveldirInitialized) {
            initThreeTraveldirJS();
        } else if (threeTraveldirTexture) {
            threeTraveldirTexture.needsUpdate = true;
        }
        onWindowResizeTraveldir();
        setTimeout(onWindowResizeTraveldir, 100);
        setTimeout(onWindowResizeTraveldir, 300);
    } else if (tab === '360') {
        tab360.classList.add('active');
        view360.classList.add('active');
        if (!isThreeInitialized) {
            initThreeJS();
        } else if (threeTexture) {
            threeTexture.needsUpdate = true;
        }
        onWindowResize();
        setTimeout(onWindowResize, 100);
        setTimeout(onWindowResize, 300);
    }

    setTimeout(() => {
        if (tab === 'kabsch' && typeof onWindowResizeKabsch === 'function') onWindowResizeKabsch();
        if (tab === 'kopf' && typeof onWindowResizeKopf === 'function') onWindowResizeKopf();
        if (tab === 'cinematic' && typeof onWindowResizeCinematic === 'function') onWindowResizeCinematic();
        if (tab === 'traveldir' && typeof onWindowResizeTraveldir === 'function') onWindowResizeTraveldir();
        if (tab === 'checkpoints' && typeof onWindowResizeCheckpoints === 'function') onWindowResizeCheckpoints();
    }, 50);

    updateActiveVideoFilenameLabel();
    updateInProgressViewport(lastStatusData);
}

function updateActiveVideoFilenameLabel() {
    const labelEl = document.getElementById('active-video-filename');
    if (!labelEl) return;

    const activeTab = document.querySelector('.viewport-tabs .tab-btn.active');
    if (!activeTab) {
        labelEl.style.display = 'none';
        return;
    }

    const tabId = activeTab.id;
    let videoEl = null;

    if (tabId === 'tab-original')            videoEl = originalVideo;
    else if (tabId === 'tab-stitched')       videoEl = videoStitched;
    else if (tabId === 'tab-telemetry')      videoEl = videoTelemetry;
    else if (tabId === 'tab-kopf')           videoEl = videoKopf;
    else if (tabId === 'tab-kabsch')         videoEl = videoKabsch;
    else if (tabId === 'tab-vidstab')        videoEl = videoVidstab;
    else if (tabId === 'tab-cinematic')      videoEl = videoCinematic;
    else if (tabId === 'tab-horizon')        videoEl = videoHorizon;
    else if (tabId === 'tab-traveldir') videoEl = videoTraveldir;
    else if (tabId === 'tab-360')            videoEl = video360;

    const activeStatuses = ['stitching', 'stabilizing', 'nadiring', 'injecting', 'reporting', 'exporting', 'processing', 'awaiting_transforms'];
    const isJobActive = lastStatusData && activeStatuses.includes(lastStatusData.status);

    let src = '';
    if (videoEl) {
        src = videoEl.getAttribute('data-file-path') || videoEl.getAttribute('data-loaded-src') || videoEl.getAttribute('data-pending-src') || videoEl.currentSrc || videoEl.src || '';
        src = src.replace(/\?t=\d+$/, '');
    }

    if (isJobActive && tabId !== 'tab-original' && tabId !== 'tab-preview' && (!src || !videoEl || !videoEl.getAttribute('data-file-path'))) {
        labelEl.style.display = 'block';
        labelEl.innerHTML = `&#9203; <span style="color: #a1a1aa;">Active Video File:</span> <strong style="color: #f59e0b;">[Processing &bull; Video Not Ready]</strong>`;
    } else if (src) {
        labelEl.style.display = 'block';
        labelEl.innerHTML = `&#127916; <span style="color: #a1a1aa;">Active Video File:</span> <strong style="color: #d4d4d8;">${escapeHtml(src)}</strong>`;
    } else {
        labelEl.style.display = 'none';
    }
}

let calibTimer = null;
let calibAbortController = null;

// ── 3. API Handlers ───────────────────────────────────────────────────────────
async function autoCalibrateStitching() {
    const btnAutoCalibrate = document.getElementById('btn-auto-calibrate');
    const statusLabel = document.getElementById('auto-calibrate-status');
    const inputVal = options.input ? options.input.value.trim() : (document.getElementById('input')?.value?.trim() || '');
    if (!inputVal) {
        if (statusLabel) {
            statusLabel.textContent = '❌ Please select an Input Video first.';
            statusLabel.style.color = '#f87171';
        }
        return;
    }

    if (calibAbortController) {
        calibAbortController.abort();
    }
    calibAbortController = new AbortController();
    const currentSignal = calibAbortController.signal;

    if (calibTimer) {
        clearInterval(calibTimer);
        calibTimer = null;
    }

    const calibMode = document.getElementById('auto_calibrate_mode')?.value || 'balanced';
    const calibFramesSelectEl = document.getElementById('auto_calibrate_frames');
    const calibFramesVal = calibFramesSelectEl?.value || '5';
    const calibCustomInputEl = document.getElementById('auto_calibrate_frames_custom');
    const videoDur = parseFloat(currentVideoMeta.duration) || (originalVideo && originalVideo.duration) || 0;

    let numFrames = 5;
    if (calibFramesVal === 'custom') {
        numFrames = parseInt(calibCustomInputEl?.value, 10) || 15;
    } else if (calibFramesVal === 'auto_10s') {
        numFrames = videoDur > 0 ? Math.max(1, Math.round(videoDur / 10)) : 10;
    } else if (calibFramesVal === 'auto_30s') {
        numFrames = videoDur > 0 ? Math.max(1, Math.round(videoDur / 30)) : 10;
    } else if (calibFramesVal === 'auto_60s') {
        numFrames = videoDur > 0 ? Math.max(1, Math.round(videoDur / 60)) : 5;
    } else {
        numFrames = parseInt(calibFramesVal, 10) || 5;
    }
    numFrames = Math.max(1, Math.min(200, numFrames));
    const estSec = Math.max(4, numFrames * 4 + 2);
    let elapsedSec = 0;

    if (btnAutoCalibrate) {
        btnAutoCalibrate.disabled = true;
        btnAutoCalibrate.innerHTML = `<span>Calibrating...</span>`;
    }
    if (statusLabel) {
        statusLabel.textContent = `⏳ Calibrating ${numFrames} frames (0s / ETA ~${estSec}s)...`;
        statusLabel.style.color = '#a78bfa';
    }

    calibTimer = setInterval(() => {
        elapsedSec += 1;
        if (statusLabel) {
            if (elapsedSec <= estSec) {
                statusLabel.textContent = `⏳ Calibrating ${numFrames} frames (${elapsedSec}s / ETA ~${estSec}s)...`;
            } else {
                statusLabel.textContent = `⏳ Calibrating ${numFrames} frames (${elapsedSec}s - finishing up)...`;
            }
            statusLabel.style.color = '#a78bfa';
        }
    }, 1000);

    const fps = currentVideoMeta.fps || 30.0;
    const previewFrameInput = document.getElementById('preview-frame-input');
    const previewGotoFrame = document.getElementById('preview-goto-frame');
    let targetFrame = 0;
    if (previewFrameInput && previewFrameInput.value !== '' && !isNaN(parseInt(previewFrameInput.value))) {
        targetFrame = Math.max(0, parseInt(previewFrameInput.value));
    } else if (previewGotoFrame && previewGotoFrame.value !== '' && !isNaN(parseInt(previewGotoFrame.value))) {
        targetFrame = Math.max(0, parseInt(previewGotoFrame.value));
    } else if (originalVideo && originalVideo.currentTime > 0) {
        targetFrame = Math.round(originalVideo.currentTime * fps);
    }
    const playheadTime = targetFrame / fps;

    const formData = new FormData();
    /* action:auto_calibrate → api/v1/videos/auto-calibrate */
    formData.append('input', inputVal);
    formData.append('preview_time', playheadTime);
    formData.append('calib_mode', calibMode);
    formData.append('num_frames', String(numFrames));

    const timeoutMs = Math.max(180000, numFrames * 8000);
    const timeoutId = setTimeout(() => {
        if (calibAbortController) {
            calibAbortController.abort();
        }
    }, timeoutMs);

    try {
        const response = await fetch('api/v1/videos/auto-calibrate', {
            method: 'POST',
            body: formData,
            signal: currentSignal
        });
        clearTimeout(timeoutId);
        const text = await response.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch (parseErr) {
            let cleanMsg = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
            if (cleanMsg.length > 200) cleanMsg = cleanMsg.substring(0, 200) + '...';
            throw new Error(cleanMsg || `Server error (${response.status})`);
        }
        if (data.status === 'success') {
            if (data.ih_fov !== undefined) setSliderAndInputValue('ih_fov', 'ih_fov_val', data.ih_fov, false);
            if (data.iv_fov !== undefined) setSliderAndInputValue('iv_fov', 'iv_fov_val', data.iv_fov, false);
            if (data.left_y_offset !== undefined) setSliderAndInputValue('left_y_offset', 'left_y_offset_val', data.left_y_offset, false);
            if (data.rear_roll_offset !== undefined) setSliderAndInputValue('rear_roll_offset', 'rear_roll_offset_val', data.rear_roll_offset, false);
            if (data.yaw !== undefined) setSliderAndInputValue('yaw', 'yaw_val', data.yaw, false);
            if (data.pitch !== undefined) setSliderAndInputValue('pitch', 'pitch_val', data.pitch, false);
            if (data.roll !== undefined) setSliderAndInputValue('roll', 'roll_val', data.roll, false);

            if (statusLabel) {
                statusLabel.textContent = `✅ Done (${elapsedSec}s)`;
                statusLabel.style.color = '#34d399';
            }
            // Trigger preview refresh once with updated parameters
            generatePreview();
        } else {
            if (statusLabel) {
                statusLabel.textContent = `❌ ${data.error || 'Calibration failed'}`;
                statusLabel.style.color = '#f87171';
            }
        }
    } catch (err) {
        clearTimeout(timeoutId);
        if (err.name === 'AbortError') {
            if (statusLabel) {
                statusLabel.textContent = `⏱️ Timed out or cancelled`;
                statusLabel.style.color = '#f87171';
            }
            return;
        }
        if (statusLabel) {
            statusLabel.textContent = `❌ Error: ${err.message}`;
            statusLabel.style.color = '#f87171';
        }
    } finally {
        clearTimeout(timeoutId);
        if (calibTimer) {
            clearInterval(calibTimer);
            calibTimer = null;
        }
        if (btnAutoCalibrate) {
            btnAutoCalibrate.disabled = false;
            btnAutoCalibrate.innerHTML = 'Auto-Calibrate';
        }
    }
}

let isGeneratingPreview = false;
let previewTimer = null;
let previewAbortController = null;
async function generatePreview() {
    if (isApplyingConfig) return;
    const inputVal = (options && options.input && options.input.value) || (inputSelect && inputSelect.value) || '';
    if (!inputVal.trim()) {
        alert('⚠️ Please select an Input Video File before generating a preview.');
        if (options && options.input) options.input.focus();
        else if (inputSelect) inputSelect.focus();
        if (btnPreview) btnPreview.disabled = false;
        return;
    }

    const enableStitchingCb = document.getElementById('enable_stitching');
    const inputHasStitchedCb = document.getElementById('input_has_stitched');
    const isStitchedInput = !!(inputHasStitchedCb && inputHasStitchedCb.checked);
    const isStitchingEnabled = !enableStitchingCb || enableStitchingCb.checked;
    const nadirEnabled = document.getElementById('nadir_enabled');
    const isNadir = !!(nadirEnabled && nadirEnabled.checked);

    if (!isStitchingEnabled && !isStitchedInput && !isNadir) {
        if (btnPreview) btnPreview.disabled = false;
        return;
    }

    if (btnPreview) btnPreview.disabled = true;
    if (previewAbortController) {
        previewAbortController.abort();
    }
    previewAbortController = new AbortController();
    const currentSignal = previewAbortController.signal;

    if (previewTimer) { clearInterval(previewTimer); previewTimer = null; }
    let elapsedSec = 0;
    const loaderTitle = (isStitchedInput && isNadir) ? 'Rendering Nadir Preview...' : (isStitchedInput ? 'Extracting Preview Frame...' : 'Stitching Calibration Preview...');
    const initialSubtext = (isStitchedInput && isNadir) ? 'Applying Nadir logo overlay...' : (isStitchedInput ? 'Extracting frame from stitched video...' : 'Applying warp & seam blends. Please be patient (ETA ~5–20s)...');
    showViewportLoader('preview-loader', loaderTitle, initialSubtext);
    previewTimer = setInterval(() => {
        if (!isGeneratingPreview) {
            if (previewTimer) { clearInterval(previewTimer); previewTimer = null; }
            return;
        }
        elapsedSec += 1;
        let subtext = initialSubtext;
        if (elapsedSec >= 10 && elapsedSec < 25) {
            subtext = `Processing high-resolution frame (${elapsedSec}s elapsed)... Please wait.`;
        } else if (elapsedSec >= 25) {
            subtext = `Processing frame (${elapsedSec}s elapsed)... Still working, thank you for your patience.`;
        }
        showViewportLoader('preview-loader', loaderTitle, subtext);
    }, 1000);

    if (previewSpinner) previewSpinner.classList.remove('hidden');
    if (previewImg) previewImg.style.opacity = '0.5';
    isGeneratingPreview = true;

    const antiVignetteCb = document.getElementById('anti_vignette');

    const formData = new FormData();
    /* action:preview → api/v1/videos/preview */
    formData.append('input', options.input ? options.input.value : '');
    formData.append('skip_stitching', isStitchedInput ? '1' : '0');
    
    const fps = currentVideoMeta.fps || 30.0;
    const previewGotoFrame = document.getElementById('preview-goto-frame');
    let targetFrame = 0;
    if (previewGotoFrame && previewGotoFrame.value !== '' && !isNaN(parseInt(previewGotoFrame.value))) {
        targetFrame = Math.max(0, parseInt(previewGotoFrame.value));
    } else if (originalVideo && originalVideo.currentTime > 0) {
        targetFrame = Math.round(originalVideo.currentTime * fps);
    }
    const playheadTime = targetFrame / fps;
    updatePreviewFrameDisplay(targetFrame, playheadTime);
    formData.append('preview_time', String(playheadTime));
    formData.append('ih_fov', readNumberInput('ih_fov_val', 'ih_fov', String(APP_CONFIG.pipeline_defaults.ih_fov)));
    formData.append('iv_fov', readNumberInput('iv_fov_val', 'iv_fov', String(APP_CONFIG.pipeline_defaults.iv_fov)));
    formData.append('raw_rotation', readNumberInput('raw_rotation_val', 'raw_rotation', String(APP_CONFIG.pipeline_defaults.raw_rotation)));
    formData.append('yaw', readNumberInput('yaw_val', 'yaw', String(APP_CONFIG.pipeline_defaults.yaw)));
    formData.append('pitch', readNumberInput('pitch_val', 'pitch', String(APP_CONFIG.pipeline_defaults.pitch)));
    formData.append('roll', readNumberInput('roll_val', 'roll', String(APP_CONFIG.pipeline_defaults.roll)));
    formData.append('left_y_offset', readNumberInput('left_y_offset_val', 'left_y_offset', String(APP_CONFIG.pipeline_defaults.left_y_offset)));
    formData.append('rear_roll_offset', readNumberInput('rear_roll_offset_val', 'rear_roll_offset', String(APP_CONFIG.pipeline_defaults.rear_roll_offset)));
    formData.append('blend_seams', cb('blend_seams'));
    formData.append('blend_width', readNumberInput('blend_width_val', 'blend_width', String(APP_CONFIG.pipeline_defaults.blend_width)));
    formData.append('anti_vignette', antiVignetteCb && antiVignetteCb.checked ? '1' : '0');
    formData.append('anti_vignette_angle', readNumberInput('anti_vignette_angle_val', 'anti_vignette_angle', String(APP_CONFIG.pipeline_defaults.anti_vignette_angle)));
    
    const nadirLogo = document.getElementById('nadir_logo');
    formData.append('nadir_enabled', isNadir ? '1' : '0');
    if (nadirLogo) formData.append('nadir_logo', nadirLogo.value);
    formData.append('nadir_fov', readNumberInput('nadir_fov_val', 'nadir_fov', String(APP_CONFIG.pipeline_defaults.nadir_fov)));
    formData.append('nadir_fov_v', readNumberInput('nadir_fov_v_val', 'nadir_fov_v', String(APP_CONFIG.pipeline_defaults.nadir_fov_v)));

    try {
        const response = await fetch('api/v1/videos/preview', {
            method: 'POST',
            body: formData,
            signal: currentSignal
        });
        const data = await response.json();
        if (previewSpinner) previewSpinner.classList.add('hidden');
        if (data.command) logCommand(data.command);
        if (data.status === 'success') {
            if (previewImg) {
                let finished = false;
                const finishPreview = () => {
                    if (finished) return;
                    finished = true;
                    if (previewTimer) { clearInterval(previewTimer); previewTimer = null; }
                    hideViewportLoader('preview-loader');
                    previewImg.style.opacity = '1';
                };
                previewImg.onload = finishPreview;
                previewImg.onerror = finishPreview;
                previewImg.src = data.preview;
                if (previewImg.complete) {
                    finishPreview();
                } else {
                    if (typeof previewImg.decode === 'function') {
                        previewImg.decode().then(finishPreview).catch(finishPreview);
                    }
                    setTimeout(finishPreview, 1500);
                }
            } else {
                if (previewTimer) { clearInterval(previewTimer); previewTimer = null; }
                hideViewportLoader('preview-loader');
            }
            if (data.original && originalImg) {
                originalImg.src = data.original;
            }
        } else {
            if (previewTimer) { clearInterval(previewTimer); previewTimer = null; }
            hideViewportLoader('preview-loader');
            if (previewImg) previewImg.style.opacity = '1';
            alert('Preview error: ' + (data.error || 'Unknown error'));
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            return;
        }
        console.error(e);
        if (previewTimer) { clearInterval(previewTimer); previewTimer = null; }
        hideViewportLoader('preview-loader');
        if (previewImg) previewImg.style.opacity = '1';
    } finally {
        if (currentSignal === previewAbortController?.signal) {
            if (previewTimer) { clearInterval(previewTimer); previewTimer = null; }
            isGeneratingPreview = false;
            if (btnPreview) btnPreview.disabled = false;
            if (previewSpinner) previewSpinner.classList.add('hidden');
        }
    }
}

function formatDateTime(d = new Date()) {
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function formatLogHtml(text) {
    if (!text) return '';
    const lines = text.split('\n');
    const formattedLines = lines.map(line => {
        const lower = line.toLowerCase();
        const escaped = escapeHtml(line);
        if (lower.includes('error') || lower.includes('failed') || lower.includes('failure') || lower.includes('fatal')) {
            return `<span style="color: #f87171; font-weight: 600;">${escaped}</span>`;
        } else if (lower.includes('warning') || lower.includes('warn')) {
            return `<span style="color: #facc15; font-weight: 600;">${escaped}</span>`;
        }
        return escaped;
    });
    return formattedLines.join('\n');
}

function logCommand(cmd) {
    const consoleOutput = document.getElementById('console-output');
    if (consoleOutput && cmd) {
        const time  = formatDateTime();
        const entry = `[${time}] ${cmd}`;
        const formattedEntry = formatLogHtml(entry);
        if (consoleOutput.textContent === 'Waiting for commands...') {
            consoleOutput.innerHTML = formattedEntry + '\n';
        } else {
            consoleOutput.innerHTML = consoleOutput.innerHTML + '\n' + formattedEntry + '\n';
        }
        setTimeout(() => {
            consoleOutput.scrollTop = consoleOutput.scrollHeight;
        }, 10);
    }
}

function buildStitchFormData(outputOverride, durationOverride) {
    const bodyData = new FormData();
    /* action:start → api/v1/jobs */
    bodyData.append('input',         options.input.value);
    const enableStitchingCb = document.getElementById('enable_stitching');
    bodyData.append('skip_stitching', enableStitchingCb && !enableStitchingCb.checked ? '1' : '0');

    bodyData.append('ih_fov',        readNumberInput('ih_fov_val', 'ih_fov', String(APP_CONFIG.pipeline_defaults.ih_fov)));
    bodyData.append('iv_fov',        readNumberInput('iv_fov_val', 'iv_fov', String(APP_CONFIG.pipeline_defaults.iv_fov)));
    bodyData.append('raw_rotation',  String(APP_CONFIG.pipeline_defaults.raw_rotation));
    bodyData.append('yaw',           readNumberInput('yaw_val', 'yaw', String(APP_CONFIG.pipeline_defaults.yaw)));
    bodyData.append('pitch',         readNumberInput('pitch_val', 'pitch', String(APP_CONFIG.pipeline_defaults.pitch)));
    bodyData.append('roll',          readNumberInput('roll_val', 'roll', String(APP_CONFIG.pipeline_defaults.roll)));
    bodyData.append('left_y_offset', readNumberInput('left_y_offset_val', 'left_y_offset', String(APP_CONFIG.pipeline_defaults.left_y_offset)));
    bodyData.append('rear_roll_offset', readNumberInput('rear_roll_offset_val', 'rear_roll_offset', String(APP_CONFIG.pipeline_defaults.rear_roll_offset)));
    bodyData.append('ffmpeg_preset', options.ffmpeg_preset.value);
    bodyData.append('ffmpeg_crf',     options.ffmpeg_crf.value);
    bodyData.append('ffmpeg_hwaccel', options.ffmpeg_hwaccel.checked ? '1' : '0');
    bodyData.append('v360_backend', (options.v360_vulkan && options.v360_vulkan.checked) ? 'vulkan' : 'cpu');
    bodyData.append('blend_seams',   cb('blend_seams'));
    bodyData.append('blend_width',   readNumberInput('blend_width_val', 'blend_width', String(APP_CONFIG.pipeline_defaults.blend_width)));
    
    const antiVignetteCb = document.getElementById('anti_vignette');
    bodyData.append('anti_vignette', antiVignetteCb && antiVignetteCb.checked ? '1' : '0');
    bodyData.append('anti_vignette_angle', readNumberInput('anti_vignette_angle_val', 'anti_vignette_angle', String(APP_CONFIG.pipeline_defaults.anti_vignette_angle)));



    bodyData.append('stabilize',     cb('stabilize'));
    const stabMethods = [];
    const orderedItems = document.querySelectorAll('#stab-order-list .stab-order-item');
    if (orderedItems && orderedItems.length > 0) {
        orderedItems.forEach(item => {
            const m = item.getAttribute('data-method');
            const chk = item.querySelector('input[type="checkbox"]');
            if (chk && chk.checked && m) {
                stabMethods.push(m);
            }
        });
    } else {
        if (document.getElementById('stab_telemetry')?.checked)    stabMethods.push('telemetry');
        if (document.getElementById('stab_kopf')?.checked)         stabMethods.push('kopf');
        if (document.getElementById('stab_kabsch')?.checked)       stabMethods.push('kabsch');
        if (document.getElementById('stab_vidstab')?.checked)      stabMethods.push('vidstab');
        if (document.getElementById('stab_cinematic')?.checked)    stabMethods.push('cinematic');
        if (document.getElementById('stab_horizon')?.checked)      stabMethods.push('horizon');
        if (document.getElementById('stab_traveldir')?.checked) stabMethods.push('traveldir');
    }
    bodyData.append('stabilize_methods', stabMethods.join(','));
    bodyData.append('stab_quality_mode', document.getElementById('stab_quality_mode')?.value || String(APP_CONFIG.pipeline_defaults.stab_quality_mode));
    bodyData.append('prompt_transforms', document.getElementById('prompt_transforms')?.checked ? '1' : '0');
    bodyData.append('fallback_unstabilized', document.getElementById('fallback_unstabilized')?.checked ? '1' : '0');

    // Input pre-applied features
    bodyData.append('input_has_stitched',    cb('input_has_stitched'));
    bodyData.append('input_has_telemetry',   cb('input_has_telemetry'));
    bodyData.append('input_has_kopf',        cb('input_has_kopf'));
    bodyData.append('input_has_kabsch',      cb('input_has_kabsch'));
    bodyData.append('input_has_vidstab',     cb('input_has_vidstab'));
    bodyData.append('input_has_cinematic',   cb('input_has_cinematic'));
    bodyData.append('input_has_horizon',     cb('input_has_horizon'));
    bodyData.append('input_has_traveldir', cb('input_has_traveldir'));
    bodyData.append('input_has_nadir',       cb('input_has_nadir'));
    
    const telemetrySourceEl = document.getElementById('telemetry_source');
    if (telemetrySourceEl) bodyData.append('telemetry_source', telemetrySourceEl.value);
    const telemetryModeEl = document.getElementById('telemetry_mode');
    if (telemetryModeEl) bodyData.append('telemetry_mode', telemetryModeEl.value);
    const telemetryFusionEl = document.getElementById('telemetry_fusion');
    if (telemetryFusionEl) bodyData.append('telemetry_fusion', telemetryFusionEl.value);
    bodyData.append('telemetry_fusion_gain', readNumberInput('telemetry_fusion_gain_val', 'telemetry_fusion_gain', '0.51'));
    bodyData.append('telemetry_smoothing', readNumberInput('telemetry_smoothing_val', 'telemetry_smoothing', String(APP_CONFIG.pipeline_defaults.telemetry_smoothing)));
    bodyData.append('telemetry_ref_frame', readNumberInput('telemetry_ref_frame_val', 'telemetry_ref_frame', String(APP_CONFIG.pipeline_defaults.telemetry_ref_frame)));
    bodyData.append('telemetry_multiplier_roll', readNumberInput('telemetry_multiplier_roll_val', 'telemetry_multiplier_roll', String(APP_CONFIG.pipeline_defaults.telemetry_multiplier_roll)));
    bodyData.append('telemetry_multiplier_pitch', readNumberInput('telemetry_multiplier_pitch_val', 'telemetry_multiplier_pitch', String(APP_CONFIG.pipeline_defaults.telemetry_multiplier_pitch)));
    bodyData.append('telemetry_multiplier_yaw', readNumberInput('telemetry_multiplier_yaw_val', 'telemetry_multiplier_yaw', String(APP_CONFIG.pipeline_defaults.telemetry_multiplier_yaw)));

    bodyData.append('l1_lambda_acc', readNumberInput('l1_lambda_acc_val', 'l1_lambda_acc', '20.0'));
    bodyData.append('l1_lambda_vel', readNumberInput('l1_lambda_vel_val', 'l1_lambda_vel', '2.0'));
    bodyData.append('cinematic_window', readNumberInput('cinematic_window_val', 'cinematic_window', '45'));

    const traveldirModeEl = document.getElementById('traveldir_mode');
    if (traveldirModeEl) bodyData.append('traveldir_mode', traveldirModeEl.value);
    bodyData.append('traveldir_target_yaw', readNumberInput('traveldir_target_yaw_val', 'traveldir_target_yaw', '0.0'));
    bodyData.append('traveldir_damping', readNumberInput('traveldir_damping_val', 'traveldir_damping', '0.90'));
    bodyData.append('traveldir_deadband', readNumberInput('traveldir_deadband_val', 'traveldir_deadband', '1.5'));

    bodyData.append('inject_intermediate_meta', cb('inject_intermediate_meta'));
    bodyData.append('inject_final_meta', cb('inject_final_meta'));
    bodyData.append('util_gcsv', cb('util_gcsv'));
    bodyData.append('util_bigsh0t', cb('util_bigsh0t'));
    
    bodyData.append('vidstab_smoothing', readNumberInput('vidstab_smoothing_val', 'vidstab_smoothing', String(APP_CONFIG.pipeline_defaults.vidstab_smoothing)));
    bodyData.append('vidstab_shakiness', readNumberInput('vidstab_shakiness_val', 'vidstab_shakiness', String(APP_CONFIG.pipeline_defaults.vidstab_shakiness)));
    bodyData.append('kabsch_smoothing',  readNumberInput('kabsch_smoothing_val',  'kabsch_smoothing',  String(APP_CONFIG.pipeline_defaults.kabsch_smoothing)));

    const vidstabOptalgoEl = document.getElementById('vidstab_optalgo');
    if (vidstabOptalgoEl) bodyData.append('vidstab_optalgo', vidstabOptalgoEl.value);

    bodyData.append('vidstab_tripod', cb('vidstab_tripod'));
    bodyData.append('vidstab_visual', cb('vidstab_visual'));

    // Kopf 3D-2D params
    const kopfKfEl   = document.getElementById('kopf_keyframe_sec_val');
    const kopfCfEl   = document.getElementById('kopf_cube_face_val');
    const kopfMfEl   = document.getElementById('kopf_max_features_val');
    if (kopfKfEl) bodyData.append('kopf_keyframe_sec', kopfKfEl.value || String(APP_CONFIG.pipeline_defaults.kopf_keyframe_sec));
    if (kopfCfEl) bodyData.append('kopf_cube_face',    kopfCfEl.value || String(APP_CONFIG.pipeline_defaults.kopf_cube_face));
    if (kopfMfEl) bodyData.append('kopf_max_features', kopfMfEl.value || String(APP_CONFIG.pipeline_defaults.kopf_max_features));
    bodyData.append('kopf_deformed', cb('kopf_deformed'));
    bodyData.append('kopf_reapply',  cb('kopf_reapply'));

    // Horizon Checkpoints auto-detect params
    bodyData.append('horizon_autodetect', cb('horizon_autodetect'));
    const horizonSourceEl = document.getElementById('horizon_autodetect_source');
    if (horizonSourceEl) bodyData.append('horizon_autodetect_source', horizonSourceEl.value || 'vision');
    const horizonDensityEl = document.getElementById('horizon_autodetect_density');
    if (horizonDensityEl) bodyData.append('horizon_autodetect_density', horizonDensityEl.value || 'ultra_dense');
    const horizonPromEl = document.getElementById('horizon_pitch_prominence');
    if (horizonPromEl) bodyData.append('horizon_pitch_prominence', horizonPromEl.value || '0.8');
    const horizonRollEl = document.getElementById('horizon_roll_damping');
    if (horizonRollEl) bodyData.append('horizon_roll_damping', horizonRollEl.value || '0.70');
    bodyData.append('horizon_apply_yaw', cb('horizon_apply_yaw'));

    bodyData.append('nadir_enabled', cb('nadir_enabled'));

    const nadirLogo = document.getElementById('nadir_logo');
    if (nadirLogo) bodyData.append('nadir_logo', nadirLogo.value);
    bodyData.append('nadir_fov',   readNumberInput('nadir_fov_val', 'nadir_fov', String(APP_CONFIG.pipeline_defaults.nadir_fov)));
    bodyData.append('nadir_fov_v', readNumberInput('nadir_fov_v_val', 'nadir_fov_v', String(APP_CONFIG.pipeline_defaults.nadir_fov_v)));

    const svEnabled = document.getElementById('streetview_enabled');
    bodyData.append('streetview_enabled', svEnabled && svEnabled.checked ? '1' : '0');
    if (svEnabled && svEnabled.checked) {
        const modeRadio = document.querySelector('input[name="streetview_mode"]:checked');
        const modeVal = modeRadio ? modeRadio.value : 'A';
        bodyData.append('streetview_mode', modeVal);
        bodyData.append('streetview_checkpoints', document.getElementById('streetview_checkpoints')?.value || '');
        bodyData.append('streetview_gpx_path', document.getElementById('streetview_gpx_path')?.value || '');
        bodyData.append('streetview_start_coord', document.getElementById('streetview_start_coord')?.value?.trim() || '');
        bodyData.append('streetview_end_coord', document.getElementById('streetview_end_coord')?.value?.trim() || '');
        const svSmoothGps = document.getElementById('streetview_smooth_gps');
        bodyData.append('streetview_smooth_gps', svSmoothGps && svSmoothGps.checked ? '1' : '0');
        bodyData.append('streetview_start_time', document.getElementById('streetview_start_time')?.value || '');
        bodyData.append('streetview_time_offset', document.getElementById('streetview_time_offset')?.value || '0');
        const autoPad = document.getElementById('streetview_auto_pad');
        bodyData.append('streetview_auto_pad', autoPad && autoPad.checked ? '1' : '0');
        const svBitrate = document.getElementById('streetview_bitrate');
        if (svBitrate) bodyData.append('streetview_bitrate', svBitrate.value);
        const svStripAudio = document.getElementById('streetview_strip_audio');
        bodyData.append('streetview_strip_audio', svStripAudio && svStripAudio.checked ? '1' : '0');
    }

    const vbSelect = document.getElementById('video_bitrate')?.value ?? APP_CONFIG.pipeline_defaults.video_bitrate;
    const vbCustom = document.getElementById('video_bitrate_custom')?.value?.trim();
    const finalVb = (vbSelect === 'custom' && vbCustom) ? `${vbCustom}M` : vbSelect;
    bodyData.append('video_bitrate', finalVb);
    bodyData.append('remove_audio', document.getElementById('remove_audio')?.checked ? '1' : '0');

    const outVal = outputOverride || (options.output ? options.output.value : '');
    bodyData.append('output', outVal);
    if (durationOverride) bodyData.append('duration', String(durationOverride));

    if (isPhotoFile(options.input ? options.input.value : '')) {
        bodyData.set('stabilize', '0');
        bodyData.set('streetview_enabled', '0');
        bodyData.set('skip_stitching', '0');
    }

    const pipelineConfig = collectPipelineConfig();
    bodyData.append('pipeline_config', JSON.stringify(pipelineConfig));

    const matchTest = outVal.match(/_out_([0-9]+)/i);
    const testNum = matchTest ? matchTest[1] : (_autoOutputSuffix || '0000');
    let tabPfx = sessionStorage.getItem('current_tab_pfx');
    if (!tabPfx) {
        tabPfx = String(_autoOutputSuffix || Math.floor(1000 + Math.random() * 9000));
        sessionStorage.setItem('current_tab_pfx', tabPfx);
    }
    currentJobId = `job_${testNum}_${tabPfx}_${Date.now()}`;
    sessionStorage.setItem('current_tab_job_id', currentJobId);
    bodyData.append('job_id', currentJobId);

    return bodyData;
}

function estimatePipelineDuration() {
    let dur = 0;
    if (typeof currentVideoMeta !== 'undefined' && isFinite(currentVideoMeta.duration) && currentVideoMeta.duration > 0) {
        dur = currentVideoMeta.duration;
    } else if (typeof originalVideo !== 'undefined' && isFinite(originalVideo.duration) && originalVideo.duration > 0) {
        dur = originalVideo.duration;
    }

    if (!dur || dur <= 0) {
        return { text: 'N/A (Loading...)', totalSec: 0, videoDur: 0, durFormatted: 'Loading...' };
    }

    const isHwaccel = (options?.ffmpeg_hwaccel && options.ffmpeg_hwaccel.checked) || false;
    const isVulkanRequested = (options?.v360_vulkan && options.v360_vulkan.checked) || document.getElementById('v360_vulkan')?.checked || false;
    const enableStitchingCb = document.getElementById('enable_stitching');
    const skipStitching = enableStitchingCb && !enableStitchingCb.checked;
    const isVulkanApplicable = !skipStitching && isVulkanRequested;
    const isStabilize = cb('stabilize') === '1';
    const isBlend = cb('blend_seams') === '1';
    const isAntiVignette = cb('anti_vignette') === '1';
    const isNadir = cb('nadir_enabled') === '1';
    const isStreetView = document.getElementById('streetview_enabled')?.checked || false;
    const streetViewAutoPad = document.getElementById('streetview_auto_pad')?.checked || false;
    const qMode = document.getElementById('stab_quality_mode')?.value || '4';

    const ffmpegPreset = (options?.ffmpeg_preset && options.ffmpeg_preset.value) || 'medium';
    let presetMult = 1.0;
    switch (ffmpegPreset) {
        case 'ultrafast': presetMult = 0.5; break;
        case 'superfast': presetMult = 0.65; break;
        case 'veryfast':  presetMult = 0.8; break;
        case 'faster':    presetMult = 0.9; break;
        case 'fast':      presetMult = 1.0; break;
        case 'medium':    presetMult = 1.25; break;
        case 'slow':      presetMult = 1.75; break;
        case 'slower':    presetMult = 2.5; break;
        case 'veryslow':  presetMult = 4.0; break;
    }

    let totalEstSec = 0;

    // 1. Base dual-fisheye stitching pass + telemetry embed + immediate spatial metadata
    if (!skipStitching) {
        let baseStitchSecPerSec = 5.5 * presetMult;
        if (isVulkanApplicable && qMode !== '3') {
            baseStitchSecPerSec = 1.6;
        } else if (isHwaccel) {
            baseStitchSecPerSec = 2.6;
        }
        let stitchTime = dur * baseStitchSecPerSec;
        if (isBlend) stitchTime *= 1.15;
        if (isAntiVignette) stitchTime *= 1.10;
        totalEstSec += 6.0 + stitchTime;
    }

    // 2. Multi-stage stabilization passes
    if (isStabilize) {
        const activeStabList = [];
        if (document.getElementById('stab_telemetry')?.checked) activeStabList.push('telemetry');
        if (document.getElementById('stab_kopf')?.checked)      activeStabList.push('kopf');
        if (document.getElementById('stab_kabsch')?.checked)    activeStabList.push('kabsch');
        if (document.getElementById('stab_vidstab')?.checked)   activeStabList.push('vidstab');
        if (document.getElementById('stab_cinematic')?.checked) activeStabList.push('cinematic');
        if (document.getElementById('stab_horizon')?.checked)   activeStabList.push('horizon');
        if (document.getElementById('stab_traveldir')?.checked) activeStabList.push('traveldir');

        // Python feature extraction & tracking analysis passes
        let stabAnalysisSec = 0;
        if (activeStabList.includes('telemetry')) stabAnalysisSec += 2.0 + dur * 0.05;
        if (activeStabList.includes('kopf'))      stabAnalysisSec += 5.0 + dur * 1.9;  // 6 cubemap face optical flow
        if (activeStabList.includes('kabsch'))    stabAnalysisSec += 3.0 + dur * 1.2;  // Spherical grid SVD solve
        if (activeStabList.includes('vidstab'))   stabAnalysisSec += 6.0 + dur * 4.5;  // Vidstab optical flow
        if (activeStabList.includes('cinematic')) stabAnalysisSec += 2.0 + dur * 0.15; // L1-norm SO(3) optimization
        if (activeStabList.includes('horizon'))   stabAnalysisSec += 2.0 + dur * 0.1;  // Horizon auto-detection
        if (activeStabList.includes('traveldir')) stabAnalysisSec += 1.5 + dur * 0.08; // Direction lock dampening
        totalEstSec += stabAnalysisSec;

        // In Mode 4 (Hybrid) or Mode 0/2: Each active stabilization method performs an intermediate video encode + 360 metadata injection
        // Accelerated by RAM disk temporary storage and NVENC P1 draft encoding
        const hasIntermediateRenders = (qMode === '4' || qMode === '0' || qMode === '2');
        if (hasIntermediateRenders && activeStabList.length > 0) {
            const intermediateRenderSecPerSec = isHwaccel ? 19.5 : (38.0 * presetMult);
            const numIntermediateRenders = activeStabList.length;
            totalEstSec += (numIntermediateRenders * 8.0) + (numIntermediateRenders * dur * intermediateRenderSecPerSec);
        }

        if (activeStabList.length > 0) {
            // Master Composition Render (with Lanczos resampling & P7 NVENC / high quality)
            const isLanczos = (qMode === '4' || qMode === '3');
            const masterBaseSec = isLanczos ? 40.0 : 15.0;
            const masterRenderSecPerSec = isHwaccel
                ? (isLanczos ? 40.0 : 18.0)
                : ((isLanczos ? 100.0 : 45.0) * presetMult);
            totalEstSec += masterBaseSec + (dur * masterRenderSecPerSec);
        }
    }

    // 3. Nadir overlay (standalone pass only if stitching was skipped or stabilization disabled)
    if (isNadir && !isStabilize && skipStitching) {
        totalEstSec += 5.0 + (isHwaccel ? (dur * 0.3) : (dur * 0.8));
    }

    // 4. Street View Export
    if (isStreetView) {
        if (streetViewAutoPad && dur < 120) {
            // Padded to 126.0s (minimum 2 minutes for Street View)
            totalEstSec += 45.0;
        } else {
            totalEstSec += 10.0 + (dur * 0.35);
        }
    }

    // 5. Performance Report & Final Copies
    if (isStabilize) {
        totalEstSec += 6.0;
    }

    totalEstSec = Math.max(1, Math.round(totalEstSec));
    let estFormatted = '';
    if (totalEstSec < 60) {
        estFormatted = `~${totalEstSec}s`;
    } else if (totalEstSec < 3600) {
        const mins = Math.floor(totalEstSec / 60);
        const secs = totalEstSec % 60;
        estFormatted = secs > 0 ? `~${mins}m ${secs}s` : `~${mins}m`;
    } else {
        const hrs = Math.floor(totalEstSec / 3600);
        const mins = Math.floor((totalEstSec % 3600) / 60);
        estFormatted = mins > 0 ? `~${hrs}h ${mins}m` : `~${hrs}h`;
    }

    const durFormatted = dur < 60 ? `${dur.toFixed(1)}s` : `${Math.floor(dur / 60)}m ${Math.round(dur % 60)}s`;

    return {
        text: estFormatted,
        totalSec: totalEstSec,
        videoDur: dur,
        durFormatted: durFormatted
    };
}

function validateStreetViewOptions(isStandalone = false) {
    const svEnabled = document.getElementById('streetview_enabled');
    if (!isStandalone && (!svEnabled || !svEnabled.checked)) {
        return true;
    }

    const modeRadio = document.querySelector('input[name="streetview_mode"]:checked');
    const mode = modeRadio ? modeRadio.value : 'A';
    const svSettings = document.getElementById('streetview-settings');
    const modeAPanel = document.getElementById('streetview-mode-a-panel');
    const modeBPanel = document.getElementById('streetview-mode-b-panel');

    function highlightField(el) {
        if (!el) return;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.focus();
        const origBorder = el.style.border;
        const origOutline = el.style.outline;
        el.style.border = '2px solid #ef4444';
        el.style.outline = '2px solid rgba(239, 68, 68, 0.4)';
        setTimeout(() => {
            el.style.border = origBorder;
            el.style.outline = origOutline;
        }, 3500);
    }

    function openSvSection(activePanel) {
        if (svSettings) svSettings.style.display = 'block';
        if (activePanel === 'A') {
            if (modeAPanel) modeAPanel.style.display = 'block';
            if (modeBPanel) modeBPanel.style.display = 'none';
        } else if (activePanel === 'B') {
            if (modeAPanel) modeAPanel.style.display = 'none';
            if (modeBPanel) modeBPanel.style.display = 'block';
        }
    }

    if (mode === 'A') {
        const cpTextEl = document.getElementById('streetview_checkpoints');
        const rawText = cpTextEl ? cpTextEl.value.trim() : '';
        if (!rawText) {
            openSvSection('A');
            highlightField(cpTextEl);
            alert('⚠️ Street View Export is enabled in Mode A (Map Checkpoints), but no route checkpoints were provided.\n\nPlease enter coordinates in the Map Checkpoints box (e.g. 40.781200, -73.966500), use the Interactive Route Builder, or switch to Mode B (Real GPX Log File).');
            return false;
        }

        const validLines = rawText.split('\n')
            .map(l => l.trim())
            .filter(l => l && !l.startsWith('#') && !l.startsWith('//'));
        if (validLines.length === 0) {
            openSvSection('A');
            highlightField(cpTextEl);
            alert('⚠️ Street View Checkpoints box is empty. Please enter coordinates before starting.');
            return false;
        }

        const coordRegex = /[-+]?\d{1,3}\.\d+/;
        const hasCoords = validLines.some(line => coordRegex.test(line) || line.includes('google.com/maps'));
        if (!hasCoords) {
            openSvSection('A');
            highlightField(cpTextEl);
            alert('⚠️ Street View Checkpoints do not contain valid coordinates (latitude, longitude).\n\nPlease enter decimal coordinates (e.g. "40.781200, -73.966500" or "00:00:00, 40.781200, -73.966500").');
            return false;
        }
    } else if (mode === 'B') {
        const gpxPathEl = document.getElementById('streetview_gpx_path');
        const gpxVal = gpxPathEl ? gpxPathEl.value.trim() : '';
        if (!gpxVal || gpxVal === '-- Select GPX File --') {
            openSvSection('B');
            highlightField(gpxPathEl);
            alert('⚠️ Street View Export is enabled in Mode B (Real GPX Log File), but no GPX log file is selected.\n\nPlease select a GPX file from the dropdown (or place a .gpx file into data/input/gps and click refresh).');
            return false;
        }
    }

    return true;
}

async function checkTestIdExistsOnDisc(testId, outputName) {
    if (!testId && !outputName) return false;
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1500);
        const resp = await fetch(`api/v1/jobs/check-test?test_num=${encodeURIComponent(testId || '')}&output=${encodeURIComponent(outputName || '')}`, {
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        if (resp.ok) {
            const data = await resp.json();
            return Boolean(data && data.exists);
        }
    } catch (e) {
        // Fetch failed or timed out
    }
    return false;
}

async function showProceedConfirmation() {
    const modal = document.getElementById('modal-proceed-confirm');
    const valuesContainer = document.getElementById('proceed-confirm-values');
    if (!modal || !valuesContainer) {
        executeStartStitching();
        return;
    }

    const enableStitchingCb = document.getElementById('enable_stitching');
    const isStitchingEnabled = !enableStitchingCb || enableStitchingCb.checked;

    const activeStabList = [];
    const orderedItemsForModal = document.querySelectorAll('#stab-order-list .stab-order-item');
    if (orderedItemsForModal && orderedItemsForModal.length > 0) {
        orderedItemsForModal.forEach(item => {
            const m = item.getAttribute('data-method');
            const chk = item.querySelector('input[type="checkbox"]');
            if (chk && chk.checked && STAB_METHOD_LABELS[m]) {
                activeStabList.push(STAB_METHOD_LABELS[m]);
            }
        });
    } else {
        if (document.getElementById('stab_telemetry')?.checked) activeStabList.push('Telemetry');
        if (document.getElementById('stab_kopf')?.checked)      activeStabList.push('Kopf');
        if (document.getElementById('stab_kabsch')?.checked)    activeStabList.push('Kabsch');
        if (document.getElementById('stab_vidstab')?.checked)        activeStabList.push('Vidstab');
        if (document.getElementById('stab_cinematic')?.checked)      activeStabList.push('Cinematic');
        if (document.getElementById('stab_horizon')?.checked)        activeStabList.push('Horizon');
        if (document.getElementById('stab_traveldir')?.checked) activeStabList.push('Travel-Dir');
    }
    const stabSummaryText = (cb('stabilize') === '1' && activeStabList.length > 0) ? activeStabList.join(', ') : 'Disabled';

    const inputName = options?.input?.value || document.getElementById('input_file')?.value || '';
    const cleanInputName = inputName.replace(/^.*[\\\/]/, '') || inputName || '-';

    const outputName = (options && options.output && options.output.value) ? options.output.value.trim() : (document.getElementById('output_name')?.value?.trim() || '');
    const cleanOutputName = outputName.replace(/^.*[\\\/]/, '') || outputName || '-';

    let testId = '';
    const mOut = cleanOutputName.match(/_out_([0-9]+)/i);
    if (mOut) {
        testId = mOut[1];
    } else {
        const mNum = cleanOutputName.match(/(?:^|_)(\d{4,})(?:\.[a-zA-Z0-9]+)?$/i);
        if (mNum) testId = mNum[1];
    }

    let testIdExists = false;
    if (testId || outputName) {
        testIdExists = await checkTestIdExistsOnDisc(testId, outputName);
    }

    const isHwaccel = (options?.ffmpeg_hwaccel && options.ffmpeg_hwaccel.checked) || false;
    const isVulkanRequested = (options?.v360_vulkan && options.v360_vulkan.checked) || false;
    const isVulkanApplicable = isStitchingEnabled && isVulkanRequested;
    const stabQualityMode = (options?.stab_quality_mode && options.stab_quality_mode.value) ? String(options.stab_quality_mode.value) : '4';
    const isMode3Lanczos = isVulkanApplicable && (stabQualityMode === '3');
    const ffmpegPreset = (options?.ffmpeg_preset && options.ffmpeg_preset.value) || String(APP_CONFIG.pipeline_defaults.ffmpeg_preset);

    let health = null;
    if (isHwaccel || isVulkanApplicable) {
        try {
            if (typeof getSystemHealth === 'function') {
                health = await getSystemHealth();
            } else if (window.__systemHealthData) {
                health = window.__systemHealthData;
            }
        } catch (_) {
            health = window.__systemHealthData || null;
        }
    }

    let hwText = `CPU (${ffmpegPreset})`;
    let hwColor = '#fb923c';
    let hwWarningAmber = false;
    let hwTitle = '';

    const activeAccels = [];
    const titles = [];

    if (isHwaccel) {
        const nvencOperational = health?.ffmpeg?.nvenc_operational;
        if (nvencOperational === false) {
            activeAccels.push('NVENC ⚠️ (CPU fallback)');
            hwWarningAmber = true;
            const reason = health?.ffmpeg?.nvenc_reason;
            titles.push(reason
                ? `NVENC unavailable: ${reason}. Pipeline will automatically fall back to CPU (libx264).`
                : 'NVENC hardware acceleration is unavailable on this system/container. Pipeline will automatically fall back to CPU (libx264).');
        } else {
            activeAccels.push('NVENC');
            titles.push('NVIDIA NVENC hardware video encoding operational.');
        }
    }

    if (isVulkanApplicable) {
        const vulkanOperational = health?.ffmpeg?.vulkan_operational;
        if (isMode3Lanczos) {
            activeAccels.push('Vulkan ℹ️ (Mode 3 CPU)');
            titles.push('Vulkan compute acceleration bypassed for Phase 1 stitching because Quality Mode 3 (Lanczos) requires CPU v360.');
        } else if (vulkanOperational === false) {
            activeAccels.push('Vulkan ⚠️ (CPU fallback)');
            hwWarningAmber = true;
            const reason = health?.ffmpeg?.vulkan_reason;
            titles.push(reason
                ? `Vulkan unavailable: ${reason}. Stitching will automatically fall back to CPU v360.`
                : 'Vulkan hardware acceleration is unavailable. Stitching will automatically fall back to CPU v360.');
        } else {
            activeAccels.push('Vulkan');
            titles.push('Vulkan compute shader acceleration (v360_vulkan) active for Phase 1 stitching.');
        }
    }

    if (activeAccels.length > 0) {
        const hasGpu = activeAccels.some(a => !a.includes('CPU fallback') && !a.includes('Mode 3 CPU'));
        hwText = hasGpu ? `GPU (${activeAccels.join(' + ')})` : activeAccels.join(' + ');
        if (!isHwaccel) {
            hwText += ` | CPU (${ffmpegPreset})`;
        }
        hwColor = hwWarningAmber ? '#fbbf24' : '#34d399';
        hwTitle = titles.join(' ');
    } else {
        hwText = `CPU (${ffmpegPreset})`;
        hwColor = '#fb923c';
        hwTitle = 'Full CPU processing (libx264 encoding and CPU v360 projection).';
    }
    const est = estimatePipelineDuration();

    const outputDisplay = testIdExists
        ? `${cleanOutputName} (exists on disc)`
        : cleanOutputName;

    const items = [
        { label: 'Input Video', value: cleanInputName, color: '#60a5fa' },
        {
            label: 'Output Video',
            value: outputDisplay,
            color: testIdExists ? '#ef4444' : '#f4f4f5',
            labelColor: testIdExists ? '#f87171' : undefined,
            isWarning: testIdExists,
            title: testIdExists ? (testId ? `Test ID ${testId} already exists in data/runtime/work/` : 'Output file already exists on disc') : ''
        },
        { label: 'Video Duration', value: est.durFormatted, color: '#f4f4f5' }
    ];

    if (isStitchingEnabled) {
        const ihFov = readNumberInput('ih_fov_val', 'ih_fov', String(APP_CONFIG.pipeline_defaults.ih_fov));
        const leftY = readNumberInput('left_y_offset_val', 'left_y_offset', String(APP_CONFIG.pipeline_defaults.left_y_offset));
        const rearRoll = readNumberInput('rear_roll_offset_val', 'rear_roll_offset', String(APP_CONFIG.pipeline_defaults.rear_roll_offset));
        const yaw = readNumberInput('yaw_val', 'yaw', String(APP_CONFIG.pipeline_defaults.yaw));
        const pitch = readNumberInput('pitch_val', 'pitch', String(APP_CONFIG.pipeline_defaults.pitch));
        const roll = readNumberInput('roll_val', 'roll', String(APP_CONFIG.pipeline_defaults.roll));
        const blendSeams = cb('blend_seams');
        const blendWidth = readNumberInput('blend_width_val', 'blend_width', String(APP_CONFIG.pipeline_defaults.blend_width));
        const seamBlendingText = (blendSeams === '1') ? `Enabled (Width: ${blendWidth}px)` : 'Disabled';

        items.push(
            { label: 'Stitching', value: 'Dual-Fisheye', color: '#34d399' },
            { label: 'FOV', value: `${ihFov}°`, color: '#a78bfa' },
            { label: 'Left Lens Offset', value: `${leftY} px`, color: '#f4f4f5' },
            { label: 'Rear Lens Offset', value: `${rearRoll}°`, color: '#c084fc' },
            { label: 'Corrections', value: `Yaw: ${yaw}° | Pitch: ${pitch}° | Roll: ${roll}°`, color: '#facc15' },
            { label: 'Blending', value: seamBlendingText, color: (blendSeams === '1') ? '#34d399' : '#94a3b8' }
        );
    } else {
        items.push(
            { label: 'Stitching', value: 'Skipped (Pre-stitched)', color: '#94a3b8' }
        );
    }

    items.push(
        { label: 'Stabilization', value: stabSummaryText, color: (cb('stabilize') === '1' && activeStabList.length > 0) ? '#38bdf8' : '#94a3b8' }
    );

    const isNadir = cb('nadir_enabled') === '1';
    if (isNadir) {
        items.push({ label: 'Nadir Logo', value: 'Enabled', color: '#34d399' });
    }

    const svEnabled = document.getElementById('streetview_enabled');
    if (svEnabled && svEnabled.checked) {
        const modeRadio = document.querySelector('input[name="streetview_mode"]:checked');
        const mode = modeRadio ? modeRadio.value : 'A';
        let svDetail = 'Enabled';
        if (mode === 'A') {
            const cpVal = document.getElementById('streetview_checkpoints')?.value?.trim() || '';
            const count = cpVal ? cpVal.split('\n').filter(l => l.trim() && !l.trim().startsWith('#')).length : 0;
            svDetail = `Mode A (${count} checkpoint${count === 1 ? '' : 's'})`;
        } else {
            const gpxFile = document.getElementById('streetview_gpx_path')?.value || '';
            const gpxName = gpxFile.replace(/^.*[\\\/]/, '') || 'GPX file';
            svDetail = `Mode B (${gpxName})`;
        }
        items.push({ label: 'Street View', value: svDetail, color: '#38bdf8' });
    }

    items.push(
        {
            label: 'Acceleration',
            value: hwText,
            color: hwColor,
            isWarningAmber: hwWarningAmber,
            title: hwTitle
        },
        { label: '⏱️ Estimated Duration', value: est.text, color: '#10b981', highlight: true }
    );

    valuesContainer.innerHTML = items.map(item => {
        let bg = 'rgba(255,255,255,0.03)';
        let border = '';
        if (item.isWarning) {
            bg = 'rgba(239, 68, 68, 0.12)';
            border = 'border: 1px solid rgba(239, 68, 68, 0.35);';
        } else if (item.isWarningAmber) {
            bg = 'rgba(245, 158, 11, 0.12)';
            border = 'border: 1px solid rgba(245, 158, 11, 0.35);';
        } else if (item.highlight) {
            bg = 'rgba(16, 185, 129, 0.12)';
            border = 'border: 1px solid rgba(16, 185, 129, 0.3); margin-top: 2px;';
        }

        const labelColor = item.labelColor || (item.isWarningAmber ? '#fbbf24' : (item.highlight ? '#6ee7b7' : '#a1a1aa'));
        const labelWeight = (item.highlight || item.isWarningAmber) ? '600' : 'normal';

        return `
        <div style="display: flex; justify-content: space-between; align-items: center; padding: ${item.highlight ? '0.45rem 0.5rem' : '0.35rem 0.5rem'}; border-radius: ${item.highlight ? '6px' : '4px'}; background: ${bg}; ${border}">
            <span style="color: ${labelColor}; font-weight: ${labelWeight}; flex-shrink: 0; margin-right: 8px;">${escapeHtml(item.label)}</span>
            <span style="color: ${item.color}; font-weight: ${item.highlight ? '700' : '600'}; font-family: monospace; font-size: ${item.fontSize || (item.highlight ? '1.0rem' : '0.92rem')}; text-align: right; word-break: break-all;" title="${escapeHtml(item.title || '')}">${escapeHtml(item.value)}</span>
        </div>
        `;
    }).join('');

    modal.style.display = 'flex';
}

function hideProceedConfirmation() {
    const modal = document.getElementById('modal-proceed-confirm');
    if (modal) modal.style.display = 'none';
    if (btnStitch) btnStitch.disabled = false;
}

async function startStitching() {
    const inputName = (options && options.input && options.input.value) || (inputSelect && inputSelect.value) || '';
    if (!inputName.trim()) {
        alert('⚠️ Please select an Input Video File before starting the pipeline.');
        if (options && options.input) {
            options.input.focus();
        } else if (inputSelect) {
            inputSelect.focus();
        }
        return;
    }
    const outputName = (options && options.output && options.output.value) ? options.output.value.trim() : '';
    if (!outputName) {
        alert('⚠️ Please enter an Output Filename before starting the pipeline.');
        if (options && options.output) {
            options.output.focus();
        }
        return;
    }
    const ihFov = readNumberInput('ih_fov_val', 'ih_fov', '');
    if (!ihFov) {
        alert('⚠️ Please enter a valid Input Horizontal FOV (ih_fov).');
        document.getElementById('ih_fov_val')?.focus();
        return;
    }
    const ivFov = readNumberInput('iv_fov_val', 'iv_fov', '');
    if (!ivFov) {
        alert('⚠️ Please enter a valid Input Vertical FOV (iv_fov).');
        document.getElementById('iv_fov_val')?.focus();
        return;
    }
    if (options && options.ffmpeg_crf && !options.ffmpeg_crf.value.trim()) {
        alert('⚠️ Please specify an FFmpeg CRF quality value.');
        options.ffmpeg_crf.focus();
        return;
    }
    if (options && options.ffmpeg_preset && !options.ffmpeg_preset.value.trim()) {
        alert('⚠️ Please select an FFmpeg Preset.');
        options.ffmpeg_preset.focus();
        return;
    }
    if (!validateStreetViewOptions(false)) {
        return;
    }
    await showProceedConfirmation();
}

async function executeStartStitching() {
    hideProceedConfirmation();
    const outputName = (options && options.output && options.output.value) ? options.output.value.trim() : '';
    if (!outputName) {
        alert('⚠️ Please enter an Output Filename before starting the pipeline.');
        if (btnStitch) btnStitch.disabled = false;
        if (options && options.output) options.output.focus();
        return;
    }
    if (!validateStreetViewOptions(false)) {
        if (btnStitch) btnStitch.disabled = false;
        return;
    }
    const stabConflict = typeof checkStabOrderConflicts === 'function' ? checkStabOrderConflicts() : null;
    const isStabActive = document.getElementById('stabilize')?.checked;
    if (isStabActive && stabConflict) {
        const proceed = confirm(`⚠️ Warning: Suboptimal Stabilization Pipeline Order Detected!\n\n${stabConflict.message}\n\nDo you want to proceed anyway?`);
        if (!proceed) {
            if (btnStitch) btnStitch.disabled = false;
            return;
        }
    }
    window._checkpointModalDismissedFor = null;
    btnStitch.disabled = true;
    userSelectedTab = null;
    _lastAutoSwitchedStage = null;
    resetAllIntermediateVideoSlots();
    try {
        const response = await fetch('api/v1/jobs', {
            method: 'POST',
            body: buildStitchFormData()
        });
        const data = await response.json();
        if (data.command) logCommand(data.command);
        if (data.status === 'success') {
            if (data.job_id) {
                currentJobId = data.job_id;
                sessionStorage.setItem('current_tab_job_id', currentJobId);
            }
            lastHandledCompletedOutput = null;
            userClearedLog = false;
            progressCard.style.display = 'flex';
            checkStatus();
            if (!pollInterval) {
                pollInterval = setInterval(checkStatus, 2000);
            }
        } else {
            alert('Failed to start stitching: ' + (data.error || 'Unknown error'));
            btnStitch.disabled = false;
        }
    } catch (e) {
        console.error(e);
        btnStitch.disabled = false;
    }
}

async function generateVideoPreview() {
    const inputName = (options && options.input && options.input.value) || (inputSelect && inputSelect.value) || '';
    if (!inputName) {
        alert('⚠️ Please select an Input Video File before generating a preview.');
        return;
    }
    if (!validateStreetViewOptions(false)) {
        return;
    }
    const btnPreviewVid = document.getElementById('btn-preview-vid');
    if (btnPreviewVid) btnPreviewVid.disabled = true;
    btnStitch.disabled = true;
    userSelectedTab = null;
    _lastAutoSwitchedStage = null;
    resetAllIntermediateVideoSlots();

    try {
        const response = await fetch('api/v1/jobs', {
            method: 'POST',
            body: buildStitchFormData('data/runtime/work/preview_vid.mp4', 3)
        });
        const data = await response.json();
        if (data.command) logCommand(data.command);
        if (data.status === 'success') {
            if (data.job_id) {
                currentJobId = data.job_id;
                sessionStorage.setItem('current_tab_job_id', currentJobId);
            }
            lastHandledCompletedOutput = null;
            userClearedLog = false;
            checkStatus();
        } else {
            alert('Video preview start error: ' + (data.error || 'Unknown error'));
            if (btnPreviewVid) btnPreviewVid.disabled = false;
            btnStitch.disabled = false;
        }
    } catch (e) {
        console.error(e);
        if (btnPreviewVid) btnPreviewVid.disabled = false;
        btnStitch.disabled = false;
    }
}

async function checkStatus() {
    try {
        const statusUrl = currentJobId ? `api/v1/jobs/${encodeURIComponent(currentJobId)}/status` : 'api/v1/jobs/_/status';
        const data = await safeFetchJson(statusUrl);
        if (data) updateProgressUI(data);
    } catch (e) {
        console.error('Failed to poll status', e);
    }
}
window.checkStatus = checkStatus;

async function resetStitcher() {
    const isCancel = btnReset && btnReset.textContent.includes('Cancel');
    if (isCancel) {
        if (!confirm('Are you sure you want to cancel the running process?')) return false;
    }
    try {
        const cancelUrl = currentJobId ? `api/v1/jobs/${encodeURIComponent(currentJobId)}/cancel` : 'api/v1/jobs/_/cancel';
        const response = await fetch(cancelUrl, { method: 'DELETE' });
        const data = await response.json();
        if (data.status === 'success') {
            lastHandledCompletedOutput = null;
            userClearedLog = true;
            const logOutput = document.getElementById('log-output');
            if (logOutput) logOutput.textContent = 'Process canceled.';
            if (progressCard) progressCard.style.display = 'none';
            resetAllIntermediateVideoSlots();
            _lastAutoSwitchedStage = null;
            updateInProgressViewport(null);
            const transformModal = document.getElementById('transformSelectModal');
            if (transformModal) transformModal.style.display = 'none';
            window._transformsSubmitted = false;
            window._lastRenderedTransformKey = null;
            currentJobId = '';
            sessionStorage.removeItem('current_tab_job_id');
            checkStatus();
            return true;
        }
    } catch (e) {
        console.error(e);
    }
    return false;
}

// ── 4. Update UI based on Status ──────────────────────────────────────────────
async function updateProgressUI(data) {
    if (data) lastStatusData = data;
    updateStatusHeaderText(data.status);

    const statusLabels = {
        'stitching':           'Stitching',
        'stabilizing':         'Stabilizing',
        'nadiring':            'Adding Nadir',
        'injecting':           'Injecting Meta',
        'reporting':           'Reporting',
        'exporting':           'Exporting',
        'awaiting_transforms': 'Awaiting Selection',
        'completed':           'Completed',
        'failed':              'Failed',
        'idle':                'Idle'
    };

    statusBadge.className = 'status-pill ' + (data.status || 'idle');
    statusBadge.textContent = statusLabels[data.status] || (data.status ? data.status.toUpperCase() : 'IDLE');

    const logOutput = document.getElementById('log-output');
    if (logOutput) {
        if (!userClearedLog && data.log !== undefined && data.log !== "") {
            logOutput.innerHTML = formatLogHtml(data.log);
            setTimeout(() => {
                logOutput.scrollTop = logOutput.scrollHeight;
            }, 10);
        } else if (userClearedLog || data.log === "") {
            logOutput.textContent = 'Waiting for process to start...';
        }
    }

    let phaseText = data.phase || '';
    if (!phaseText && data.eta && !/^(\d+(\.\d+)?s?|\d{1,2}:\d{2}(:\d{2})?|calculating(\.\.\.)?|n\/a|paused|finished)$/i.test(String(data.eta).trim())) {
        phaseText = String(data.eta).trim();
    }

    if (phaseLabel) {
        if (phaseText) {
            phaseLabel.innerHTML = `Phase: <strong style="color: #facc15;">${escapeHtml(phaseText)}</strong>`;
        } else {
            phaseLabel.innerHTML = '';
        }
    }

    // Check for Transform Selection Interactive Pause
    const isAwaitingTransforms = (data.status === 'awaiting_transforms');
    const transformModal = document.getElementById('transformSelectModal');
    if (isAwaitingTransforms && data.transforms && Array.isArray(data.transforms) && data.transforms.length > 0) {
        showTransformSelectModal(data.transforms, data.skipped_transforms);
    } else if (transformModal && !isAwaitingTransforms) {
        transformModal.style.display = 'none';
        window._transformsSubmitted = false;
        window._lastRenderedTransformKey = null;
    }

    // Check for Horizon Checkpoints Interactive Pause
    const isWaitingCheckpoints = (data.status === 'stabilizing' || data.status === 'processing') && data.phase && data.phase.includes('Waiting for Horizon Checkpoints');
    const modal = document.getElementById('checkpointPauseModal');

    if (isWaitingCheckpoints) {
        const match = data.log ? data.log.match(/\[STATUS:WAITING_FOR_HORIZON_CHECKPOINTS:out_base=([^:]+):video=([^\]]+)\]/) : null;
        let outBase = match ? match[1] : '';
        let videoPath = match ? match[2] : '';
        if (!outBase && options.output && options.output.value) {
            const rawVal = options.output.value;
            const dotIdx = rawVal.lastIndexOf('.');
            const baseNoExt = dotIdx === -1 ? rawVal : rawVal.substring(0, dotIdx);
            outBase = baseNoExt.replace(/^.*[\\\/]/, '');
        }
        if (window._checkpointModalDismissedFor !== outBase) {
            showCheckpointPauseModal(outBase, videoPath);
        }
    } else if (modal && data.status !== 'stabilizing' && data.status !== 'processing') {
        modal.style.display = 'none';
    }

    const activeStatuses = ['stitching', 'stabilizing', 'nadiring', 'injecting', 'reporting', 'exporting', 'processing', 'awaiting_transforms'];
    if (activeStatuses.includes(data.status)) {
        syncPipelineTabsVisibility(data);
        progressCard.style.display = 'flex';
        btnStitch.disabled = true;

        const btnPreviewVid = document.getElementById('btn-preview-vid');
        if (btnPreviewVid) btnPreviewVid.disabled = true;

        btnReset.disabled = false;
        btnReset.innerHTML = 'Cancel Process';

        const progress = data.progress || 0;
        progressBarFill.style.width = progress + '%';
        progressPercent.textContent = progress + '%';
        metaSpeed.textContent   = data.speed   || 'N/A';
        metaEta.textContent     = formatTimeHHMMSS(data.eta);
        metaElapsed.textContent = formatTimeHHMMSS(data.elapsed);

        lockControls(true);

        const currentInputBase = options.input ? options.input.value.replace(/^.*[\\\/]/, '').replace(/\.[^/.]+$/, '').toLowerCase() : '';
        const outputBase = (data.output || (options.output ? options.output.value : '')).replace(/^.*[\\\/]/, '').replace(/\.[^/.]+$/, '').toLowerCase();
        const matchesCurrentInput = Boolean(currentInputBase && outputBase && outputBase.includes(currentInputBase));

        if (matchesCurrentInput || !currentInputBase) {
            const currentOutFile = data.output || (options.output ? options.output.value : '');
            checkAllIntermediateVideos(null, currentOutFile);
        }

    } else if (data.status === 'completed') {
        const hasSessionJob = Boolean(currentJobId);
        if (!hasSessionJob) {
            lastHandledCompletedOutput = (data.output || (options.output ? options.output.value : '')) + '|' + (data.elapsed || 0);
            progressCard.style.display = 'none';
            btnStitch.disabled = false;
            btnReset.disabled = true;
            btnReset.innerHTML = 'Reset Status';
            if (phaseLabel) phaseLabel.innerHTML = '';
            lockControls(false);
            return;
        }

        syncPipelineTabsVisibility(data);
        progressCard.style.display = 'flex';
        btnStitch.disabled = false;
        btnReset.disabled  = false;

        progressBarFill.style.width = '100%';
        progressPercent.textContent = '100%';
        metaSpeed.textContent   = 'Finished';
        metaEta.textContent     = 'N/A';
        metaElapsed.textContent = formatTimeHHMMSS(data.elapsed);
        if (phaseLabel) phaseLabel.innerHTML = '';

        lockControls(false);

        const currentInputBase = options.input ? options.input.value.replace(/^.*[\\\/]/, '').replace(/\.[^/.]+$/, '').toLowerCase() : '';
        const outputBase = (data.output || '').replace(/^.*[\\\/]/, '').replace(/\.[^/.]+$/, '').toLowerCase();
        const matchesCurrentInput = Boolean(currentInputBase && outputBase && outputBase.includes(currentInputBase));

        const completedKey = (data.output || (options.output ? options.output.value : '')) + '|' + (data.elapsed || 0);
        if (matchesCurrentInput && lastHandledCompletedOutput !== completedKey) {
            lastHandledCompletedOutput = completedKey;

            const finalFilename = data.output || (options.output ? options.output.value : '');
            await checkAllIntermediateVideos(null, finalFilename);

            if (tab360) tab360.classList.remove('hidden');
            if (userSelectedTab === null) {
                switchTab('360');
            }
        }

    } else {
        // idle or failed
        progressCard.style.display = (data.status === 'failed') ? 'flex' : 'none';
        btnStitch.disabled = false;

        const btnPreviewVid = document.getElementById('btn-preview-vid');
        if (btnPreviewVid) btnPreviewVid.disabled = false;

        btnReset.disabled = (data.status === 'idle');
        btnReset.innerHTML = 'Reset Status';
        if (phaseLabel) phaseLabel.innerHTML = '';
        lockControls(false);

        if (data.status === 'failed') {
            metaSpeed.textContent = 'ERROR';
            metaEta.textContent   = data.error || 'Failed';
            progressBarFill.style.width = '0%';
            progressPercent.textContent = '0%';
        }
    }

    updateInProgressViewport(data);

    // Stop polling only when truly terminal:
    // - 'completed' or 'failed' are always terminal
    // - 'idle' is terminal only if we have no active job (currentJobId is empty),
    //   otherwise the process is still spinning up and hasn't written its status file yet
    const isTrulyTerminal =
        data.status === 'completed' ||
        data.status === 'failed' ||
        (data.status === 'idle' && !currentJobId);

    if (isTrulyTerminal) {
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
        // Clear the job reference so the next idle poll doesn't re-trigger polling (H-1)
        currentJobId = '';
        sessionStorage.removeItem('current_tab_job_id');
    } else {
        // Ensure polling is active if we are in a running state (e.g., after page reload)
        if (!pollInterval) {
            pollInterval = setInterval(checkStatus, 2000);
        }
    }
}

function lockControls(lock) {
    Object.values(sliders).forEach(slider => slider.disabled = lock);
    Object.values(options).forEach(opt => opt.disabled = lock);
    if (btnPreview) btnPreview.disabled = lock;
}

// ── 5. Three.js 360 VR Player ─────────────────────────────────────────────────
function initThreeJS() {
    if (isThreeInitialized) return;

    const width  = player360Container.clientWidth;
    const height = player360Container.clientHeight;

    threeScene = new THREE.Scene();
    threeCamera = new THREE.PerspectiveCamera(75, width / height, 1, 1100);
    threeCamera.target = new THREE.Vector3(0, 0, 0);

    threeRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeRenderer.setPixelRatio(window.devicePixelRatio);
    threeRenderer.setSize(width, height);
    player360Container.appendChild(threeRenderer.domElement);

    threeControls = new OrbitControls(threeCamera, threeRenderer.domElement);
    threeControls.enableZoom  = false; // Disable camera translation zoom to use FOV zoom instead
    threeControls.enablePan   = false;
    threeControls.rotateSpeed = -0.25;
    threeControls.addEventListener('change', () => handleControlsChange('360'));

    threeCamera.position.set(0, 0, 0.1);
    threeControls.target.set(0, 0, 0);
    threeControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    if (threeTexture) {
        try { threeTexture.dispose(); } catch (e) {}
    }
    threeTexture  = new THREE.VideoTexture(video360);
    threeTexture.colorSpace = THREE.SRGBColorSpace;
    threeTexture.needsUpdate = true;
    const hasReadyVideo = Boolean(video360 && video360.getAttribute('data-loaded-src') && video360.readyState >= 2 && video360.duration > 0 && !video360.error);
    const initialMap = hasReadyVideo ? threeTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialMap });
    threeSphere = new THREE.Mesh(geometry, material);
    threeSphere.rotation.y = -Math.PI / 2;
    threeScene.add(threeSphere);

    window.addEventListener('resize', onWindowResize);
    document.addEventListener('fullscreenchange', () => {
        onWindowResize();
        setTimeout(onWindowResize, 100);
        setTimeout(onWindowResize, 300);
    });
    document.addEventListener('webkitfullscreenchange', () => {
        onWindowResize();
        setTimeout(onWindowResize, 100);
        setTimeout(onWindowResize, 300);
    });

    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResize();
        });
        resizeObserver.observe(player360Container);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    isThreeInitialized = true;
    animate();
}

function onWindowResize() {
    if (!isThreeInitialized) return;
    const width  = player360Container.clientWidth;
    const height = player360Container.clientHeight;
    threeCamera.aspect = width / height;
    threeCamera.updateProjectionMatrix();
    threeRenderer.setSize(width, height);
}

function animate() {
    if (!isThreeInitialized) return;
    requestAnimationFrame(animate);
    if (document.hidden || !player360Container || player360Container.clientWidth === 0 || player360Container.clientHeight === 0) {
        return;
    }
    if (threeTexture && video360 && video360.readyState >= 1) {
        hideViewportLoader('vr-loader');
        if (threeSphere && threeSphere.material && threeSphere.material.map !== threeTexture) {
            threeSphere.material.map = threeTexture;
            threeSphere.material.needsUpdate = true;
        }
    }
    threeControls.update();
    threeRenderer.render(threeScene, threeCamera);
}

function togglePlay360() {
    if (!video360) return;
    if (video360.paused) {
        video360.play().then(() => {
            if (vrPlayPause) vrPlayPause.innerHTML = '&#10074;&#10074;';
        }).catch(err => {
            console.error("Video play failed:", err);
        });
    } else {
        video360.pause();
        if (vrPlayPause) vrPlayPause.innerHTML = '&#9654;';
    }
}

function updateVRTimeDisplay() {
    if (!video360) return;
    const cur = video360.currentTime || 0;
    const dur = video360.duration || 0;
    const fps = currentVideoMeta.fps || 30.0;
    
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    
    if (vrTimeDisplay) {
        vrTimeDisplay.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    }
    updateGotoFrameInputValue('vr-goto-frame', curF);
    updateGotoTimeInputValue('vr-goto-time', cur);
}

function formatTime(secs) {
    if (isNaN(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
}

function formatTimeHHMMSS(val) {
    if (val === undefined || val === null || val === '') return 'Calculating...';
    const strVal = String(val).trim();
    if (/^calculating(\.\.\.)?$/i.test(strVal)) return 'Calculating...';
    if (/^n\/a$/i.test(strVal)) return 'N/A';
    if (/^paused$/i.test(strVal)) return 'Paused';
    if (/^finished$/i.test(strVal)) return '00:00:00';

    const match = strVal.match(/^(\d+(?:\.\d+)?)s?$/);
    if (match) {
        const totalSeconds = Math.round(parseFloat(match[1]));
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        const pad = (num) => String(num).padStart(2, '0');
        return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
    }

    const timeMatch = strVal.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
    if (timeMatch) {
        const pad = (num) => String(num).padStart(2, '0');
        if (timeMatch[3] !== undefined) {
            return `${pad(timeMatch[1])}:${pad(timeMatch[2])}:${pad(timeMatch[3])}`;
        } else {
            return `00:${pad(timeMatch[1])}:${pad(timeMatch[2])}`;
        }
    }

    return 'Calculating...';
}

function resolveVideoSrc(path) {
    if (!path || typeof path !== 'string') return '';
    let p = path.trim().replace(/\\/g, '/');
    if (/^(javascript|data|vbscript|blob):/i.test(p)) return '';
    let cleanQuery = '';
    if (p.includes('://')) {
        try {
            const u = new URL(p, window.location.origin);
            if (u.origin !== window.location.origin) return '';
            if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
            p = u.pathname;
            if (u.search && /^\?[a-zA-Z0-9_=&.\-]+$/.test(u.search)) {
                cleanQuery = u.search;
            }
        } catch (e) {
            return '';
        }
    } else {
        const qIndex = p.indexOf('?');
        if (qIndex !== -1) {
            const rawQuery = p.slice(qIndex);
            p = p.slice(0, qIndex);
            if (/^\?[a-zA-Z0-9_=&.\-]+$/.test(rawQuery)) {
                cleanQuery = rawQuery;
            }
        }
    }
    p = p.replace(/^\/+/, '');
    const segments = p.split('/').map(seg => seg.replace(/[^a-zA-Z0-9_\-\.]/g, '_').replace(/^[\.\-]+/, '')).filter(Boolean);
    if (segments.length === 0) return '';
    const safeRelPath = segments.join('/');
    const finalPath = (safeRelPath.startsWith('data/') || safeRelPath.startsWith('samples/'))
        ? safeRelPath
        : `data/input/videos/${safeRelPath}`;
    return encodeURI(finalPath) + cleanQuery;
}

async function loadVideoMetadata() {
    const videoFile = options.input.value;
    if (!videoFile) return;

    const resolvedVideo = resolveVideoSrc(videoFile);

    if (warmupDetectStatus) {
        warmupDetectStatus.style.display = 'none';
        warmupDetectStatus.innerHTML = '';
    }

    // Immediately bind video src and display player so it never stays black
    if (originalVideo) {
        if (originalVideo.getAttribute('data-loaded-src') !== resolvedVideo) {
            showViewportLoader('orig-loader', 'Loading Raw Video Stream...');
            originalVideo.src = resolvedVideo;
            originalVideo.loop = true;
            originalVideo.setAttribute('data-loaded-src', resolvedVideo);
            if (origControlsOverlay) origControlsOverlay.style.display = 'flex';
            originalVideo.load();
        }
    }

    if (videoMetadataLbl) videoMetadataLbl.textContent = 'Loading video metadata...';

    try {
        const response = await fetch(`api/v1/videos/info?input=${encodeURIComponent(videoFile)}`);
        const data = await response.json();
        if (data.status === 'success') {
            currentVideoMeta.duration = data.duration;
            currentVideoMeta.fps = data.fps;
            currentVideoMeta.total_frames = data.total_frames;

            setOrigVRMode(false);

            if (videoMetadataLbl) {
                videoMetadataLbl.textContent = `Total Video: ${data.duration.toFixed(2)}s (${data.total_frames} frames @ ${data.fps.toFixed(2)} fps)`;
            }

            const currentPreviewF = parseInt(document.getElementById('preview-frame-input')?.value || 0);
            updatePreviewFrameDisplay(currentPreviewF);

            if (trimStartSlider) {
                trimStartSlider.max = data.total_frames - 1;
                trimStartSlider.value = 0;
            }
            if (trimEndSlider) {
                trimEndSlider.max = data.total_frames;
                trimEndSlider.value = data.total_frames;
            }

            if (trimStartFrame) {
                trimStartFrame.max = data.total_frames;
                trimStartFrame.value = 0;
            }
            if (trimEndFrame) {
                trimEndFrame.max = data.total_frames;
                trimEndFrame.value = data.total_frames;
            }

            updateTrimTimeLabels();
            updateCroppedOutputName();
            updateOrigTimeDisplay();
        } else {
            console.error('Metadata retrieval failed:', data.error);
            if (videoMetadataLbl) videoMetadataLbl.textContent = 'Failed to load video properties.';
        }
    } catch (e) {
        console.error(e);
        if (videoMetadataLbl) videoMetadataLbl.textContent = 'Error loading metadata.';
    } finally {
        // NOTE: Do NOT hide orig-loader here — attachVideoLoaderEvents handles it via canplay/loadeddata
    }
}



async function checkAllIntermediateVideos(overrideTestNum, overrideOutput) {
    const inputVal = options.input ? options.input.value : '';
    const outputVal = overrideOutput || (options.output ? options.output.value : '');
    const testNum = (overrideTestNum !== undefined && overrideTestNum !== null)
        ? overrideTestNum
        : (overrideOutput ? '' : (document.getElementById('load_test_number')?.value?.trim() || ''));
    if (!inputVal && !testNum && !outputVal) return;

    const injectMeta = document.getElementById('inject_intermediate_meta')?.checked ? 1 : 0;
    let checkUrl = `api/v1/jobs/intermediates?input=${encodeURIComponent(inputVal)}&output=${encodeURIComponent(outputVal)}&inject_intermediate_meta=${injectMeta}`;
    if (testNum) {
        checkUrl += `&test_num=${encodeURIComponent(testNum)}`;
    } else if (currentJobId) {
        checkUrl += `&job_id=${encodeURIComponent(currentJobId)}`;
    }

    try {
        const response = await fetch(checkUrl);
        const data = await response.json();
        if (data.status === 'success') {
            const t = new Date().getTime();

            // 0. Update Raw Input Video & UI controls when loading a specific test by ID
            if (overrideTestNum) {
                userSelectedTab = null;
                activatedIntermediateSteps.clear();
                if (data.job_id) {
                    currentJobId = data.job_id;
                    sessionStorage.setItem('current_tab_job_id', currentJobId);
                } else {
                    currentJobId = '';
                    sessionStorage.removeItem('current_tab_job_id');
                }
                lastHandledCompletedOutput = 'loaded_test_' + overrideTestNum;
                checkStatus();

                if (data.raw_input && options.input) {
                    let matched = false;
                    const rawBase = data.raw_input.replace(/^.*[\\\/]/, '').toLowerCase();
                    for (let i = 0; i < options.input.options.length; i++) {
                        const optVal = options.input.options[i].value;
                        const optBase = optVal.replace(/^.*[\\\/]/, '').toLowerCase();
                        if (optVal === data.raw_input || optBase === rawBase) {
                            options.input.selectedIndex = i;
                            matched = true;
                            break;
                        }
                    }
                    if (!matched) {
                        const canonicalRaw = resolveVideoSrc(data.raw_input);
                        const newOpt = document.createElement('option');
                        newOpt.value = canonicalRaw;
                        newOpt.textContent = canonicalRaw;
                        newOpt.selected = true;
                        options.input.appendChild(newOpt);
                    }
                }
                if (data.raw_input && originalVideo) {
                    const canonicalRaw = resolveVideoSrc(data.raw_input);
                    const rawFileUrl = canonicalRaw + '?t=' + t;
                    if (originalVideo.getAttribute('data-loaded-src') !== canonicalRaw) {
                        originalVideo.src = rawFileUrl;
                        originalVideo.loop = true;
                        originalVideo.setAttribute('data-loaded-src', canonicalRaw);
                        originalVideo.style.display = 'block';
                        if (origControlsOverlay) origControlsOverlay.style.display = 'flex';
                        if (originalImg) originalImg.style.display = 'none';
                        originalVideo.load();
                    }
                }
                if (data.raw_input) {
                    detectInputFeatures(data.raw_input);
                    loadVideoMetadata(true);
                }

                if (data.output_file && options.output) {
                    options.output.value = data.output_file;
                }

                if (data.config && typeof data.config === 'object') {
                    applyPipelineConfig(data.config, 'Test #' + overrideTestNum);
                } else if (data.params && typeof data.params === 'object') {
                    if (data.params.ih_fov !== undefined) setSliderAndInputValue('ih_fov', 'ih_fov_val', data.params.ih_fov, false);
                    if (data.params.iv_fov !== undefined) setSliderAndInputValue('iv_fov', 'iv_fov_val', data.params.iv_fov, false);
                    if (data.params.yaw !== undefined) setSliderAndInputValue('yaw', 'yaw_val', data.params.yaw, false);
                    if (data.params.pitch !== undefined) setSliderAndInputValue('pitch', 'pitch_val', data.params.pitch, false);
                    if (data.params.roll !== undefined) setSliderAndInputValue('roll', 'roll_val', data.params.roll, false);
                    if (data.params.left_y_offset !== undefined) setSliderAndInputValue('left_y_offset', 'left_y_offset_val', data.params.left_y_offset, false);
                    if (data.params.rear_roll_offset !== undefined) setSliderAndInputValue('rear_roll_offset', 'rear_roll_offset_val', data.params.rear_roll_offset, false);
                    if (data.params.blend_width !== undefined) setSliderAndInputValue('blend_width', 'blend_width_val', data.params.blend_width, false);
                    if (data.params.blend_seams !== undefined) setElementValue('blend_seams', data.params.blend_seams, false);
                }
            }

            // Always update original frame image and preview frame image if returned
            if (data.original && originalImg) {
                const prevOrig = originalImg.getAttribute('data-orig-path');
                if (prevOrig !== data.original) {
                    originalImg.setAttribute('data-orig-path', data.original);
                    originalImg.src = data.original + '?t=' + t;
                }
            }

            if (data.preview && previewImg) {
                const prevPrev = previewImg.getAttribute('data-prev-path');
                if (prevPrev !== data.preview) {
                    previewImg.setAttribute('data-prev-path', data.preview);
                    previewImg.src = data.preview + '?t=' + t;
                    previewImg.style.opacity = '1';
                }
            }

            _isBatchLoadingIntermediates = true;
            try {
                // 1. Stitched Unstabilized video
                updateIntermediateVideoSlot(videoStitched, tabStitched, data.stitched, 'stitched', t);

                // 2. Telemetry Stabilized video
                updateIntermediateVideoSlot(videoTelemetry, tabTelemetry, data.telemetry, 'telemetry', t);

                // 2b. Kabsch Stabilized video
                updateIntermediateVideoSlot(videoKabsch, tabKabsch, data.kabsch, 'kabsch', t);

                // 2c. Kopf Stabilized video
                updateIntermediateVideoSlot(videoKopf, tabKopf, data.kopf, 'kopf', t);

                // 3. Vidstab Stabilized video
                const vidstabSrc = data.vidstab;
                updateIntermediateVideoSlot(videoVidstab, tabVidstab, vidstabSrc, 'vidstab', t);

                // 3a. L1-Smooth Stabilized video
                const cinematicSrc = data.cinematic;
                updateIntermediateVideoSlot(videoCinematic, tabCinematic, cinematicSrc, 'cinematic', t);

                // 3b. Horizon Leveled video
                const horizonSrc = data.horizon;
                updateIntermediateVideoSlot(videoHorizon, tabHorizon, horizonSrc, 'horizon', t);

                // 3c. Direction-Lock Stabilized video
                const traveldirSrc = data.traveldir;
                updateIntermediateVideoSlot(videoTraveldir, tabTraveldir, traveldirSrc, 'traveldir', t);

                // 4b. 360 VR Viewport Video (Final Net Output Video) - ONLY shown when status is COMPLETED or when loading test
                const isCompletedJob = (lastStatusData && lastStatusData.status === 'completed') || Boolean(overrideTestNum);
                if (isCompletedJob) {
                    const finalVideoFile = (data.final && data.final !== '') ? data.final : ((data.output_file && data.output_file !== '') ? data.output_file : (data.telemetry || data.kopf || data.kabsch || vidstabSrc || cinematicSrc || horizonSrc || traveldirSrc || data.stitched || ''));
                    updateIntermediateVideoSlot(video360, tab360, finalVideoFile, null, t);
                } else {
                    if (tab360) tab360.classList.add('hidden');
                    if (video360) {
                        video360.removeAttribute('data-file-path');
                        video360.removeAttribute('data-pending-src');
                        video360.removeAttribute('data-loaded-src');
                        video360.removeAttribute('src');
                        video360.pause();
                    }
                    const { sphere } = getSphereAndTextureForTab('360');
                    if (sphere) {
                        updateThreeSphereTexture(sphere, null, null);
                    }
                }
            } finally {
                _isBatchLoadingIntermediates = false;
            }

            // Lazy-load ONLY the active tab video (prevents concurrent 4K streams choking browser)
            const currentActiveTabBtn = document.querySelector('.viewport-tabs .tab-btn.active');
            let currentActiveTabName = currentActiveTabBtn ? currentActiveTabBtn.id.replace('tab-', '') : 'preview';
            if (overrideTestNum && userSelectedTab === null) {
                if (data.final || data.output_file) currentActiveTabName = '360';
                else if (data.traveldir) currentActiveTabName = 'traveldir';
                else if (data.horizon) currentActiveTabName = 'horizon';
                else if (data.cinematic) currentActiveTabName = 'cinematic';
                else if (data.vidstab) currentActiveTabName = 'vidstab';
                else if (data.kabsch) currentActiveTabName = 'kabsch';
                else if (data.kopf) currentActiveTabName = 'kopf';
                else if (data.telemetry) currentActiveTabName = 'telemetry';
                else if (data.stitched) currentActiveTabName = 'stitched';
                switchTab(currentActiveTabName, false);
            } else if (currentActiveTabBtn && currentActiveTabBtn.classList.contains('hidden')) {
                currentActiveTabName = 'preview';
                switchTab('preview', true);
            } else {
                ensureTabVideoLoaded(currentActiveTabName);
            }

            updateActiveVideoFilenameLabel();

            // 5. Telemetry, Kopf, Kabsch, Vidstab, L1-Smooth, Horizon & Direction-Lock Corrections Graphs & Reports
            const gTelemetryBtn     = document.getElementById('vr-telemetry-graph-btn');
            const gKopfBtn          = document.getElementById('vr-kopf-graph-btn');
            const gKabschBtn        = document.getElementById('vr-kabsch-graph-btn');
            const gVidstabBtn       = document.getElementById('vr-vidstab-graph-btn');
            const gCinematicBtn      = document.getElementById('vr-cinematic-graph-btn');
            const gHorizonBtn       = document.getElementById('vr-horizon-graph-btn');
            const gTraveldirBtn = document.getElementById('vr-traveldir-graph-btn');

            const rTelemetryBtn     = document.getElementById('vr-telemetry-report-btn');
            const rKopfBtn          = document.getElementById('vr-kopf-report-btn');
            const rKabschBtn        = document.getElementById('vr-kabsch-report-btn');
            const rVidstabBtn       = document.getElementById('vr-vidstab-report-btn');
            const rCinematicBtn      = document.getElementById('vr-cinematic-report-btn');
            const rHorizonBtn       = document.getElementById('vr-horizon-report-btn');
            const rTraveldirBtn = document.getElementById('vr-traveldir-report-btn');

            if (data.telemetry_graph) {
                currentTelemetryGraphUrl = data.telemetry_graph + '?t=' + t;
                if (gTelemetryBtn) gTelemetryBtn.style.display = 'inline-flex';
            } else {
                currentTelemetryGraphUrl = '';
                if (gTelemetryBtn) gTelemetryBtn.style.display = 'none';
            }
            if (data.telemetry_report) {
                currentTelemetryReportFile = data.telemetry_report;
                if (rTelemetryBtn) rTelemetryBtn.style.display = 'inline-flex';
            } else {
                currentTelemetryReportFile = '';
                if (rTelemetryBtn) rTelemetryBtn.style.display = 'none';
            }

            if (data.kopf_graph) {
                currentKopfGraphUrl = data.kopf_graph + '?t=' + t;
                if (gKopfBtn) gKopfBtn.style.display = 'inline-flex';
            } else {
                currentKopfGraphUrl = '';
                if (gKopfBtn) gKopfBtn.style.display = 'none';
            }
            if (data.kopf_report) {
                currentKopfReportFile = data.kopf_report;
                if (rKopfBtn) rKopfBtn.style.display = 'inline-flex';
            } else {
                currentKopfReportFile = '';
                if (rKopfBtn) rKopfBtn.style.display = 'none';
            }

            if (data.kabsch_graph) {
                currentKabschGraphUrl = data.kabsch_graph + '?t=' + t;
                if (gKabschBtn) gKabschBtn.style.display = 'inline-flex';
            } else {
                currentKabschGraphUrl = '';
                if (gKabschBtn) gKabschBtn.style.display = 'none';
            }
            if (data.kabsch_report) {
                currentKabschReportFile = data.kabsch_report;
                if (rKabschBtn) rKabschBtn.style.display = 'inline-flex';
            } else {
                currentKabschReportFile = '';
                if (rKabschBtn) rKabschBtn.style.display = 'none';
            }

            if (data.vidstab_graph || data.optical_graph) {
                currentVidstabGraphUrl = (data.vidstab_graph || data.optical_graph) + '?t=' + t;
                if (gVidstabBtn) gVidstabBtn.style.display = 'inline-flex';
            } else {
                currentVidstabGraphUrl = '';
                if (gVidstabBtn) gVidstabBtn.style.display = 'none';
            }
            if (data.vidstab_report || data.optical_report) {
                currentVidstabReportFile = data.vidstab_report || data.optical_report;
                if (rVidstabBtn) rVidstabBtn.style.display = 'inline-flex';
            } else {
                currentVidstabReportFile = '';
                if (rVidstabBtn) rVidstabBtn.style.display = 'none';
            }

            if (data.cinematic_graph) {
                currentCinematicGraphUrl = data.cinematic_graph + '?t=' + t;
                if (gCinematicBtn) gCinematicBtn.style.display = 'inline-flex';
            } else {
                currentCinematicGraphUrl = '';
                if (gCinematicBtn) gCinematicBtn.style.display = 'none';
            }
            if (data.cinematic_report) {
                currentCinematicReportFile = data.cinematic_report;
                if (rCinematicBtn) rCinematicBtn.style.display = 'inline-flex';
            } else {
                currentCinematicReportFile = '';
                if (rCinematicBtn) rCinematicBtn.style.display = 'none';
            }

            if (data.horizon_graph || data.checkpoints_graph) {
                currentHorizonGraphUrl = (data.horizon_graph || data.checkpoints_graph) + '?t=' + t;
                if (gHorizonBtn) gHorizonBtn.style.display = 'inline-flex';
            } else {
                currentHorizonGraphUrl = '';
                if (gHorizonBtn) gHorizonBtn.style.display = 'none';
            }
            if (data.horizon_report || data.checkpoints_report) {
                currentHorizonReportFile = data.horizon_report || data.checkpoints_report;
                if (rHorizonBtn) rHorizonBtn.style.display = 'inline-flex';
            } else {
                currentHorizonReportFile = '';
                if (rHorizonBtn) rHorizonBtn.style.display = 'none';
            }

            // Horizon Editor link with found checkpoints and video in subject
            const cpCompletedBox = document.getElementById('horizon-completed-link-container');
            const cpCompletedBtn = document.getElementById('btn-open-horizon-editor-completed');
            const cpCompletedBadge = document.getElementById('cp-completed-badge');
            const vrHorizonEditorBtn = document.getElementById('vr-horizon-editor-btn');

            const hasCheckpoints = Boolean(data.horizon_checkpoints);
            const editorVideo = data.stitched || data.raw_input || data.horizon || data.final || '';

            if (hasCheckpoints && editorVideo) {
                const editorUrl = `horizon_editor.html?video=${encodeURIComponent(editorVideo)}&out_base=${encodeURIComponent(data.target_base || '')}&checkpoints=${encodeURIComponent(data.horizon_checkpoints)}`;
                if (cpCompletedBtn) {
                    cpCompletedBtn.href = editorUrl;
                }
                if (cpCompletedBox) {
                    cpCompletedBox.style.display = 'block';
                }
                if (cpCompletedBadge) {
                    cpCompletedBadge.textContent = data.target_base ? `${data.target_base}` : 'Ready';
                }
                if (vrHorizonEditorBtn) {
                    vrHorizonEditorBtn.style.display = 'inline-flex';
                    vrHorizonEditorBtn.onclick = () => window.open(editorUrl, '_blank');
                }
            } else {
                if (cpCompletedBox) cpCompletedBox.style.display = 'none';
                if (vrHorizonEditorBtn) vrHorizonEditorBtn.style.display = 'none';
            }

            if (data.traveldir_graph) {
                currentTraveldirGraphUrl = data.traveldir_graph + '?t=' + t;
                if (gTraveldirBtn) gTraveldirBtn.style.display = 'inline-flex';
            } else {
                currentTraveldirGraphUrl = '';
                if (gTraveldirBtn) gTraveldirBtn.style.display = 'none';
            }
            if (data.traveldir_report) {
                currentTraveldirReportFile = data.traveldir_report;
                if (rTraveldirBtn) rTraveldirBtn.style.display = 'inline-flex';
            } else {
                currentTraveldirReportFile = '';
                if (rTraveldirBtn) rTraveldirBtn.style.display = 'none';
            }
        } else if (overrideTestNum && data.status === 'error') {
            alert(data.message || data.error || `No test run files found matching test #${overrideTestNum}.`);
        }
    } catch (e) {
        console.error("Failed checking intermediate videos:", e);
    }
}

// ── Movable Floating Popup Handlers ───────────────────────────────────────────
function togglePopup360() {
    if (!popup360) return;
    if (popup360.style.display === 'none' || !popup360.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popup360.style.zIndex = '10001';
        popup360.style.top = '0px';
        popup360.style.left = '50vw';
        popup360.style.right = 'auto';
        popup360.style.bottom = 'auto';
        popup360.style.display = 'flex';
        if (popup360Body) {
            const overlay = view360 ? view360.querySelector('.vr-controls-overlay') : null;
            if (video360) popup360Body.appendChild(video360);
            if (player360Container) popup360Body.appendChild(player360Container);
            if (overlay) popup360Body.appendChild(overlay);
        }
        if (!isThreeInitialized) initThreeJS();
        onWindowResize();
        setTimeout(onWindowResize, 50);
        setTimeout(onWindowResize, 150);
        syncFirstFrame();
    } else {
        closePopup360();
    }
}

function closePopup360() {
    if (!popup360) return;
    popup360.style.display = 'none';
    if (view360) {
        const overlay = popup360Body ? popup360Body.querySelector('.vr-controls-overlay') : null;
        if (video360) view360.appendChild(video360);
        if (player360Container) view360.appendChild(player360Container);
        if (overlay) view360.appendChild(overlay);
    }
    onWindowResize();
    setTimeout(onWindowResize, 50);
    setTimeout(onWindowResize, 150);
    syncFirstFrame();
}

function togglePopupStitched() {
    if (!popupStitched) return;
    if (popupStitched.style.display === 'none' || !popupStitched.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupStitched.style.zIndex = '10001';
        popupStitched.style.top = '0px';
        popupStitched.style.left = '0px';
        popupStitched.style.right = 'auto';
        popupStitched.style.bottom = 'auto';
        popupStitched.style.display = 'flex';
        if (popupStitchedBody) {
            const overlay = viewStitched ? viewStitched.querySelector('.vr-controls-overlay') : null;
            if (videoStitched) popupStitchedBody.appendChild(videoStitched);
            if (playerStitchedContainer) popupStitchedBody.appendChild(playerStitchedContainer);
            if (overlay) popupStitchedBody.appendChild(overlay);
        }
        if (!isThreeStitchedInitialized) initThreeStitchedJS();
        onWindowResizeStitched();
        setTimeout(onWindowResizeStitched, 50);
        setTimeout(onWindowResizeStitched, 150);
        syncFirstFrame();
    } else {
        closePopupStitched();
    }
}

function closePopupStitched() {
    if (!popupStitched) return;
    popupStitched.style.display = 'none';
    if (viewStitched) {
        const overlay = popupStitchedBody ? popupStitchedBody.querySelector('.vr-controls-overlay') : null;
        if (videoStitched) viewStitched.appendChild(videoStitched);
        if (playerStitchedContainer) viewStitched.appendChild(playerStitchedContainer);
        if (overlay) viewStitched.appendChild(overlay);
    }
    onWindowResizeStitched();
    setTimeout(onWindowResizeStitched, 50);
    setTimeout(onWindowResizeStitched, 150);
    syncFirstFrame();
}

function togglePopupOriginal() {
    if (!popupOriginal) return;
    if (popupOriginal.style.display === 'none' || !popupOriginal.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupOriginal.style.zIndex = '10001';
        popupOriginal.style.top = '0px';
        popupOriginal.style.left = '0px';
        popupOriginal.style.right = 'auto';
        popupOriginal.style.bottom = 'auto';
        popupOriginal.style.display = 'flex';
        if (popupOriginalBody) {
            const origImg = document.getElementById('original-img');
            const origVid = document.getElementById('original-video');
            const origOverlay = document.getElementById('orig-controls-overlay');
            if (origImg) popupOriginalBody.appendChild(origImg);
            if (origVid) popupOriginalBody.appendChild(origVid);
            if (playerOrigContainer) popupOriginalBody.appendChild(playerOrigContainer);
            if (origOverlay) popupOriginalBody.appendChild(origOverlay);
        }
        if (isOrigVRMode) {
            if (!isThreeOrigInitialized) initThreeOrigJS();
            onWindowResizeOrig();
            setTimeout(onWindowResizeOrig, 100);
        }
        syncFirstFrame();
    } else {
        closePopupOriginal();
    }
}

function closePopupOriginal() {
    if (!popupOriginal) return;
    popupOriginal.style.display = 'none';
    const container = document.getElementById('original-container');
    if (container) {
        const origImg = popupOriginalBody ? popupOriginalBody.querySelector('#original-img') : null;
        const origVid = popupOriginalBody ? popupOriginalBody.querySelector('#original-video') : null;
        const origPlayer = popupOriginalBody ? popupOriginalBody.querySelector('#player-orig-container') : null;
        const origOverlay = popupOriginalBody ? popupOriginalBody.querySelector('#orig-controls-overlay') : null;
        if (origImg) container.appendChild(origImg);
        if (origVid) container.appendChild(origVid);
        if (origPlayer) container.appendChild(origPlayer);
        if (origOverlay) container.appendChild(origOverlay);
    }
    if (isOrigVRMode) {
        onWindowResizeOrig();
        setTimeout(onWindowResizeOrig, 100);
    }
    syncFirstFrame();
}



function togglePopupTelemetry() {
    if (!popupTelemetry) return;
    if (popupTelemetry.style.display === 'none' || !popupTelemetry.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupTelemetry.style.zIndex = '10001';
        popupTelemetry.style.left = '50vw';
        popupTelemetry.style.top = '0px';
        popupTelemetry.style.bottom = 'auto';
        popupTelemetry.style.right = 'auto';
        popupTelemetry.style.display = 'flex';
        if (popupTelemetryBody) {
            const overlay = viewTelemetry ? viewTelemetry.querySelector('.vr-controls-overlay') : null;
            if (videoTelemetry) popupTelemetryBody.appendChild(videoTelemetry);
            if (playerTelemetryContainer) popupTelemetryBody.appendChild(playerTelemetryContainer);
            if (overlay) popupTelemetryBody.appendChild(overlay);
        }
        if (!isThreeTelemetryInitialized) initThreeTelemetryJS();
        onWindowResizeTelemetry();
        setTimeout(onWindowResizeTelemetry, 50);
        setTimeout(onWindowResizeTelemetry, 150);
        syncFirstFrame();
    } else {
        closePopupTelemetry();
    }
}

function closePopupTelemetry() {
    if (!popupTelemetry) return;
    popupTelemetry.style.display = 'none';
    const container = document.getElementById('telemetry-container');
    if (container) {
        const overlay = popupTelemetryBody ? popupTelemetryBody.querySelector('.vr-controls-overlay') : null;
        if (videoTelemetry) container.appendChild(videoTelemetry);
        if (playerTelemetryContainer) container.appendChild(playerTelemetryContainer);
        if (overlay) container.appendChild(overlay);
    }
    onWindowResizeTelemetry();
    setTimeout(onWindowResizeTelemetry, 50);
    setTimeout(onWindowResizeTelemetry, 150);
    syncFirstFrame();
}

function togglePopupKabsch() {
    if (!popupKabsch) return;
    if (popupKabsch.style.display === 'none' || !popupKabsch.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupKabsch.style.zIndex = '10001';
        popupKabsch.style.right = '0px';
        popupKabsch.style.bottom = '0px';
        popupKabsch.style.top = 'auto';
        popupKabsch.style.left = '50vw';
        popupKabsch.style.display = 'flex';
        if (popupKabschBody) {
            const overlay = viewKabsch ? viewKabsch.querySelector('.vr-controls-overlay') : null;
            if (videoKabsch) popupKabschBody.appendChild(videoKabsch);
            if (playerKabschContainer) popupKabschBody.appendChild(playerKabschContainer);
            if (overlay) popupKabschBody.appendChild(overlay);
        }
        if (!isThreeKabschInitialized) initThreeKabschJS();
        onWindowResizeKabsch();
        setTimeout(onWindowResizeKabsch, 50);
        setTimeout(onWindowResizeKabsch, 150);
        syncFirstFrame();
    } else {
        closePopupKabsch();
    }
}

function closePopupKabsch() {
    if (!popupKabsch) return;
    popupKabsch.style.display = 'none';
    const container = document.getElementById('kabsch-container');
    if (container) {
        const overlay = popupKabschBody ? popupKabschBody.querySelector('.vr-controls-overlay') : null;
        if (videoKabsch) container.appendChild(videoKabsch);
        if (playerKabschContainer) container.appendChild(playerKabschContainer);
        if (overlay) container.appendChild(overlay);
    }
    onWindowResizeKabsch();
    setTimeout(onWindowResizeKabsch, 50);
    setTimeout(onWindowResizeKabsch, 150);
    syncFirstFrame();
}

function togglePopupKopf() {
    if (!popupKopf) return;
    if (popupKopf.style.display === 'none' || !popupKopf.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupKopf.style.zIndex = '10001';
        popupKopf.style.right = '0px';
        popupKopf.style.bottom = '0px';
        popupKopf.style.top = 'auto';
        popupKopf.style.left = '50vw';
        popupKopf.style.display = 'flex';
        if (popupKopfBody) {
            const overlay = viewKopf ? viewKopf.querySelector('.vr-controls-overlay') : null;
            if (videoKopf) popupKopfBody.appendChild(videoKopf);
            if (playerKopfContainer) popupKopfBody.appendChild(playerKopfContainer);
            if (overlay) popupKopfBody.appendChild(overlay);
        }
        if (!isThreeKopfInitialized) initThreeKopfJS();
        onWindowResizeKopf();
        setTimeout(onWindowResizeKopf, 50);
        setTimeout(onWindowResizeKopf, 150);
        syncFirstFrame();
    } else {
        closePopupKopf();
    }
}

function closePopupKopf() {
    if (!popupKopf) return;
    popupKopf.style.display = 'none';
    const container = document.getElementById('kopf-container');
    if (container) {
        const overlay = popupKopfBody ? popupKopfBody.querySelector('.vr-controls-overlay') : null;
        if (videoKopf) container.appendChild(videoKopf);
        if (playerKopfContainer) container.appendChild(playerKopfContainer);
        if (overlay) container.appendChild(overlay);
    }
    onWindowResizeKopf();
    setTimeout(onWindowResizeKopf, 50);
    setTimeout(onWindowResizeKopf, 150);
    syncFirstFrame();
}

function togglePopupVidstab() {
    if (!popupVidstab) return;
    if (popupVidstab.style.display === 'none' || !popupVidstab.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupVidstab.style.zIndex = '10001';
        popupVidstab.style.left = '0px';
        popupVidstab.style.bottom = '0px';
        popupVidstab.style.top = 'auto';
        popupVidstab.style.right = 'auto';
        popupVidstab.style.display = 'flex';
        if (popupVidstabBody) {
            const overlay = viewVidstab ? viewVidstab.querySelector('.vr-controls-overlay') : null;
            if (videoVidstab) popupVidstabBody.appendChild(videoVidstab);
            if (playerVidstabContainer) popupVidstabBody.appendChild(playerVidstabContainer);
            if (overlay) popupVidstabBody.appendChild(overlay);
        }
        if (!isThreeVidstabInitialized) initThreeVidstabJS();
        onWindowResizeVidstab();
        setTimeout(onWindowResizeVidstab, 50);
        setTimeout(onWindowResizeVidstab, 150);
        syncFirstFrame();
    } else {
        closePopupVidstab();
    }
}

function closePopupVidstab() {
    if (!popupVidstab) return;
    popupVidstab.style.display = 'none';
    const container = document.getElementById('vidstab-container');
    if (container) {
        const overlay = popupVidstabBody ? popupVidstabBody.querySelector('.vr-controls-overlay') : null;
        if (videoVidstab) container.appendChild(videoVidstab);
        if (playerVidstabContainer) container.appendChild(playerVidstabContainer);
        if (overlay) container.appendChild(overlay);
    }
    onWindowResizeVidstab();
    setTimeout(onWindowResizeVidstab, 50);
    setTimeout(onWindowResizeVidstab, 150);
    syncFirstFrame();
}

function togglePopupCinematic() {
    if (!popupCinematic) return;
    if (popupCinematic.style.display === 'none' || !popupCinematic.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupCinematic.style.zIndex = '10001';
        popupCinematic.style.top = '0px';
        popupCinematic.style.left = '50vw';
        popupCinematic.style.right = 'auto';
        popupCinematic.style.bottom = 'auto';
        popupCinematic.style.width = '50vw';
        popupCinematic.style.height = 'calc(25vw + 38px)';
        popupCinematic.style.maxHeight = '50vh';
        popupCinematic.style.display = 'flex';
        if (popupCinematicBody) {
            const overlay = viewCinematic ? viewCinematic.querySelector('.vr-controls-overlay') : null;
            if (videoCinematic) popupCinematicBody.appendChild(videoCinematic);
            if (playerCinematicContainer) popupCinematicBody.appendChild(playerCinematicContainer);
            if (overlay) popupCinematicBody.appendChild(overlay);
        }
        if (!isThreeCinematicInitialized) initThreeCinematicJS();
        onWindowResizeCinematic();
        setTimeout(onWindowResizeCinematic, 50);
        setTimeout(onWindowResizeCinematic, 150);
        syncFirstFrame();
    } else {
        closePopupCinematic();
    }
}

function closePopupCinematic() {
    if (!popupCinematic) return;
    popupCinematic.style.display = 'none';
    const container = document.getElementById('cinematic-container');
    if (container) {
        const overlay = popupCinematicBody ? popupCinematicBody.querySelector('.vr-controls-overlay') : null;
        if (videoCinematic) container.appendChild(videoCinematic);
        if (playerCinematicContainer) container.appendChild(playerCinematicContainer);
        if (overlay) container.appendChild(overlay);
    }
    onWindowResizeCinematic();
    setTimeout(onWindowResizeCinematic, 50);
    setTimeout(onWindowResizeCinematic, 150);
    syncFirstFrame();
}

function togglePopupHorizon() {
    if (!popupHorizon) return;
    if (popupHorizon.style.display === 'none' || !popupHorizon.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupHorizon.style.zIndex = '10001';
        popupHorizon.style.top = '0px';
        popupHorizon.style.left = '50vw';
        popupHorizon.style.right = 'auto';
        popupHorizon.style.bottom = 'auto';
        popupHorizon.style.width = '50vw';
        popupHorizon.style.height = 'calc(25vw + 38px)';
        popupHorizon.style.maxHeight = '50vh';
        popupHorizon.style.display = 'flex';
        if (popupHorizonBody) {
            const overlay = viewHorizon ? viewHorizon.querySelector('.vr-controls-overlay') : null;
            if (videoHorizon) popupHorizonBody.appendChild(videoHorizon);
            if (playerHorizonContainer) popupHorizonBody.appendChild(playerHorizonContainer);
            if (overlay) popupHorizonBody.appendChild(overlay);
        }
        if (!isThreeHorizonInitialized) initThreeHorizonJS();
        onWindowResizeHorizon();
        setTimeout(onWindowResizeHorizon, 50);
        setTimeout(onWindowResizeHorizon, 150);
        syncFirstFrame();
    } else {
        closePopupHorizon();
    }
}

function closePopupHorizon() {
    if (!popupHorizon) return;
    popupHorizon.style.display = 'none';
    const container = document.getElementById('horizon-container');
    if (container) {
        const overlay = popupHorizonBody ? popupHorizonBody.querySelector('.vr-controls-overlay') : null;
        if (videoHorizon) container.appendChild(videoHorizon);
        if (playerHorizonContainer) container.appendChild(playerHorizonContainer);
        if (overlay) container.appendChild(overlay);
    }
    onWindowResizeHorizon();
    setTimeout(onWindowResizeHorizon, 50);
    setTimeout(onWindowResizeHorizon, 150);
    syncFirstFrame();
}

function togglePopupTraveldir() {
    if (!popupTraveldir) return;
    if (popupTraveldir.style.display === 'none' || !popupTraveldir.style.display) {
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        popupTraveldir.style.zIndex = '10001';
        popupTraveldir.style.top = '0px';
        popupTraveldir.style.left = '50vw';
        popupTraveldir.style.right = 'auto';
        popupTraveldir.style.bottom = 'auto';
        popupTraveldir.style.width = '50vw';
        popupTraveldir.style.height = 'calc(25vw + 38px)';
        popupTraveldir.style.maxHeight = '50vh';
        popupTraveldir.style.display = 'flex';
        if (popupTraveldirBody) {
            const overlay = viewTraveldir ? viewTraveldir.querySelector('.vr-controls-overlay') : null;
            if (videoTraveldir) popupTraveldirBody.appendChild(videoTraveldir);
            if (playerTraveldirContainer) popupTraveldirBody.appendChild(playerTraveldirContainer);
            if (overlay) popupTraveldirBody.appendChild(overlay);
        }
        if (!isThreeTraveldirInitialized) initThreeTraveldirJS();
        onWindowResizeTraveldir();
        setTimeout(onWindowResizeTraveldir, 50);
        setTimeout(onWindowResizeTraveldir, 150);
        syncFirstFrame();
    } else {
        closePopupTraveldir();
    }
}

function closePopupTraveldir() {
    if (!popupTraveldir) return;
    popupTraveldir.style.display = 'none';
    const container = document.getElementById('traveldir-container');
    if (container) {
        const overlay = popupTraveldirBody ? popupTraveldirBody.querySelector('.vr-controls-overlay') : null;
        if (videoTraveldir) container.appendChild(videoTraveldir);
        if (playerTraveldirContainer) container.appendChild(playerTraveldirContainer);
        if (overlay) container.appendChild(overlay);
    }
    onWindowResizeTraveldir();
    setTimeout(onWindowResizeTraveldir, 50);
    setTimeout(onWindowResizeTraveldir, 150);
    syncFirstFrame();
}

async function openPopupGraph(graphType = 'telemetry') {
    if (!popupGraph) return;
    document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
    popupGraph.style.zIndex = '10001';

    const inputVal = options.input ? options.input.value : '';
    const outputVal = options.output ? options.output.value : '';
    const testNum = document.getElementById('load_test_number')?.value?.trim() || '';
    const injectMeta = document.getElementById('inject_intermediate_meta')?.checked ? 1 : 0;
    const timestamp = new Date().getTime();

    try {
        let checkUrl = `api/v1/jobs/intermediates?input=${encodeURIComponent(inputVal)}&output=${encodeURIComponent(outputVal)}&inject_intermediate_meta=${injectMeta}`;
        if (testNum) {
            checkUrl += `&test_num=${encodeURIComponent(testNum)}`;
        } else if (currentJobId) {
            checkUrl += `&job_id=${encodeURIComponent(currentJobId)}`;
        }
        const resp = await fetch(checkUrl);
        const data = await resp.json();
        if (data.status === 'success') {
            currentTelemetryGraphUrl     = data.telemetry_graph ? data.telemetry_graph + '?t=' + timestamp : '';
            currentKopfGraphUrl          = data.kopf_graph ? data.kopf_graph + '?t=' + timestamp : '';
            currentKabschGraphUrl        = data.kabsch_graph ? data.kabsch_graph + '?t=' + timestamp : '';
            currentVidstabGraphUrl       = data.vidstab_graph ? data.vidstab_graph + '?t=' + timestamp : '';
            currentCinematicGraphUrl      = data.cinematic_graph ? data.cinematic_graph + '?t=' + timestamp : '';
            currentHorizonGraphUrl       = data.horizon_graph ? data.horizon_graph + '?t=' + timestamp : '';
            currentTraveldirGraphUrl = data.traveldir_graph ? data.traveldir_graph + '?t=' + timestamp : '';
        }
    } catch (e) {
        console.error("Failed fetching graph URL:", e);
    }

    let targetUrl = '';
    if (graphType === 'vidstab') targetUrl = currentVidstabGraphUrl;
    else if (graphType === 'kopf') targetUrl = currentKopfGraphUrl;
    else if (graphType === 'kabsch') targetUrl = currentKabschGraphUrl;
    else if (graphType === 'cinematic') targetUrl = currentCinematicGraphUrl;
    else if (graphType === 'horizon') targetUrl = currentHorizonGraphUrl;
    else if (graphType === 'traveldir') targetUrl = currentTraveldirGraphUrl;
    else targetUrl = currentTelemetryGraphUrl;

    const titleMap = {
        'telemetry':      '≡ 3D IMU Telemetry Stabilization Corrections Graph',
        'kopf':           '≡ Kopf 3D-2D Vision Keyframe Motion Graph',
        'kabsch':         '≡ 3D Kabsch SVD Spherical Motion Graph',
        'vidstab':        '≡ VidSTAB (2D Motion Flow) Stabilization Graph',
        'cinematic':      '≡ Cinematic Path Smoothing Graph',
        'horizon':        '≡ Horizon Leveling Trajectory Graph',
        'traveldir': '≡ Travel-Direction Lock Motion Graph'
    };

    const titleHandle = document.querySelector('#popup-graph-header .vr-popup-drag-handle');
    if (titleHandle) {
        titleHandle.textContent = titleMap[graphType] || '≡ Stabilization Motion Graph';
    }

    if (targetUrl) {
        if (graphImg) {
            graphImg.style.display = 'block';
            graphImg.src = targetUrl;
        }
        popupGraph.style.display = 'flex';
    } else {
        alert(`No ${graphType} graph found for this video. Run a ${graphType} stabilization pass first.`);
    }
}

function closePopupGraph() {
    if (!popupGraph) return;
    popupGraph.style.display = 'none';
}

async function openPopupReport(reportType = 'telemetry') {
    if (!popupReport) return;
    document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
    popupReport.style.zIndex = '10001';

    const inputVal = options.input ? options.input.value : '';
    const outputVal = options.output ? options.output.value : '';
    const testNum = document.getElementById('load_test_number')?.value?.trim() || '';
    const injectMeta = document.getElementById('inject_intermediate_meta')?.checked ? 1 : 0;

    const titleMap = {
        'telemetry':      '≡ Telemetry IMU Stabilization Diagnostic Report',
        'kopf':           '≡ Kopf 3D-2D Vision Keyframe Diagnostic Report',
        'kabsch':         '≡ Kabsch Spherical SVD Diagnostic Report',
        'vidstab':        '≡ VidSTAB (2D Motion Flow) Diagnostic Report',
        'cinematic':      '≡ Cinematic Smoothing Diagnostic Report',
        'horizon':        '≡ Horizon Leveling Diagnostic Report',
        'traveldir': '≡ Travel-Direction Lock Diagnostic Report'
    };

    if (popupReportTitleText) {
        popupReportTitleText.textContent = titleMap[reportType] || '≡ Stabilization Diagnostic Report';
    }
    if (popupReportText) {
        popupReportText.textContent = 'Loading report...';
    }
    popupReport.style.display = 'flex';

    try {
        let checkUrl = `api/v1/jobs/intermediates?input=${encodeURIComponent(inputVal)}&output=${encodeURIComponent(outputVal)}&inject_intermediate_meta=${injectMeta}`;
        if (testNum) {
            checkUrl += `&test_num=${encodeURIComponent(testNum)}`;
        } else if (currentJobId) {
            checkUrl += `&job_id=${encodeURIComponent(currentJobId)}`;
        }
        const resp = await fetch(checkUrl);
        const data = await resp.json();
        if (data.status === 'success') {
            currentTelemetryReportFile     = data.telemetry_report || '';
            currentKopfReportFile          = data.kopf_report || '';
            currentKabschReportFile        = data.kabsch_report || '';
            currentVidstabReportFile       = data.vidstab_report || data.optical_report || '';
            currentCinematicReportFile      = data.cinematic_report || '';
            currentHorizonReportFile       = data.horizon_report || data.checkpoints_report || '';
            currentTraveldirReportFile = data.traveldir_report || '';
        }
    } catch (e) {
        console.error("Failed fetching report info:", e);
    }

    let targetFile = '';
    if (reportType === 'vidstab') targetFile = currentVidstabReportFile;
    else if (reportType === 'kopf') targetFile = currentKopfReportFile;
    else if (reportType === 'kabsch') targetFile = currentKabschReportFile;
    else if (reportType === 'cinematic') targetFile = currentCinematicReportFile;
    else if (reportType === 'horizon') targetFile = currentHorizonReportFile;
    else if (reportType === 'traveldir') targetFile = currentTraveldirReportFile;
    else targetFile = currentTelemetryReportFile;

    if (targetFile) {
        try {
            const res = await safeFetchJson(`api/v1/reports?file=${encodeURIComponent(targetFile)}`);
            if (res && res.status === 'success' && res.content) {
                if (popupReportText) popupReportText.textContent = res.content;
            } else {
                if (popupReportText) popupReportText.textContent = res?.error || `Could not load ${reportType} report content.`;
            }
        } catch (err) {
            if (popupReportText) popupReportText.textContent = 'Failed to load report: ' + err.message;
        }
    } else {
        if (popupReportText) popupReportText.textContent = `No ${reportType} report found for this video. Run a ${reportType} stabilization pass first.`;
    }
}

function closePopupReport() {
    if (!popupReport) return;
    popupReport.style.display = 'none';
}

function closePopupPreview() {
    if (!popupPreview) return;
    popupPreview.style.display = 'none';
    const container = document.getElementById('preview-container');
    if (container) {
        const previewImg = popupPreviewBody ? popupPreviewBody.querySelector('#preview-img') : null;
        const previewVid = popupPreviewBody ? popupPreviewBody.querySelector('#preview-video') : null;
        const previewHorizonLine = popupPreviewBody ? popupPreviewBody.querySelector('#preview-horizon-line') : null;
        if (previewImg) container.appendChild(previewImg);
        if (previewHorizonLine) container.appendChild(previewHorizonLine);
        if (previewVid) container.appendChild(previewVid);
        if (previewVid) {
            previewVid.pause();
            previewVid.currentTime = 0;
        }
    }
    syncFirstFrame();
}

function makeDraggable(elmnt, header) {
    if (!elmnt) return;
    let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
    const targetHeader = header || elmnt;
    targetHeader.onmousedown = dragMouseDown;

    function dragMouseDown(e) {
        if (e.target.tagName === 'BUTTON' || e.target.closest('button') || e.target.classList.contains('vr-popup-close-btn')) return;
        e.preventDefault();

        if (elmnt.style.bottom && elmnt.style.bottom !== 'auto') {
            elmnt.style.top = elmnt.offsetTop + 'px';
            elmnt.style.bottom = '';
        }
        if (elmnt.style.right && elmnt.style.right !== 'auto') {
            elmnt.style.left = elmnt.offsetLeft + 'px';
            elmnt.style.right = '';
        }

        // Bring active popup window to the top
        document.querySelectorAll('.vr-popup-window').forEach(p => p.style.zIndex = '10000');
        elmnt.style.zIndex = '10001';

        pos3 = e.clientX;
        pos4 = e.clientY;
        document.onmouseup = closeDragElement;
        document.onmousemove = elementDrag;
    }

    function elementDrag(e) {
        e.preventDefault();
        pos1 = pos3 - e.clientX;
        pos2 = pos4 - e.clientY;
        pos3 = e.clientX;
        pos4 = e.clientY;
        let newTop = elmnt.offsetTop - pos2;
        let newLeft = elmnt.offsetLeft - pos1;
        const maxTop = window.innerHeight - elmnt.offsetHeight;
        const maxLeft = window.innerWidth - elmnt.offsetWidth;
        elmnt.style.top = Math.min(Math.max(0, newTop), maxTop) + "px";
        elmnt.style.left = Math.min(Math.max(0, newLeft), maxLeft) + "px";
    }

    function closeDragElement() {
        document.onmouseup = null;
        document.onmousemove = null;
    }
}

// ── Three.js 360 Original Raw Player ──────────────────────────────────────────
function setOrigVRMode(enable) {
    isOrigVRMode = !!enable;
    const origVRInputs = document.getElementById('orig-vr-inputs-container');
    const origZoomIn   = document.getElementById('vr-orig-zoom-in');
    const origZoomOut  = document.getElementById('vr-orig-zoom-out');
    const origReset    = document.getElementById('vr-orig-reset-view');

    if (isOrigVRMode) {
        if (originalVideo) originalVideo.style.display = 'none';
        if (originalImg) originalImg.style.display = 'none';
        if (playerOrigContainer) playerOrigContainer.style.display = 'block';
        if (origToggleVR) {
            origToggleVR.classList.add('active');
            origToggleVR.style.background = 'rgba(56,189,248,0.25)';
            origToggleVR.style.borderColor = '#38bdf8';
        }
        if (origToggleVRText) origToggleVRText.textContent = '2D';
        if (origVRInputs) origVRInputs.style.display = 'flex';
        if (origZoomIn) origZoomIn.style.display = '';
        if (origZoomOut) origZoomOut.style.display = '';
        if (origReset) origReset.style.display = '';

        if (!isThreeOrigInitialized) {
            initThreeOrigJS();
        } else if (threeOrigTexture) {
            threeOrigTexture.needsUpdate = true;
        }
        onWindowResizeOrig();
        setTimeout(onWindowResizeOrig, 80);
        setTimeout(onWindowResizeOrig, 250);
    } else {
        if (playerOrigContainer) playerOrigContainer.style.display = 'none';
        const isPhoto = isPhotoFile(options.input ? options.input.value : '');
        if (isPhoto) {
            if (originalImg) originalImg.style.display = 'block';
            if (originalVideo) originalVideo.style.display = 'none';
        } else {
            if (originalVideo) originalVideo.style.display = 'block';
            if (originalImg) originalImg.style.display = 'none';
        }
        if (origToggleVR) {
            origToggleVR.classList.remove('active');
            origToggleVR.style.background = '';
            origToggleVR.style.borderColor = 'rgba(56,189,248,0.4)';
        }
        if (origToggleVRText) origToggleVRText.textContent = '360';
        if (origVRInputs) origVRInputs.style.display = 'none';
        if (origZoomIn) origZoomIn.style.display = 'none';
        if (origZoomOut) origZoomOut.style.display = 'none';
        if (origReset) origReset.style.display = 'none';
    }
}

let isOrigVRControlsInitialized = false;
function initOrigVRControls() {
    if (isOrigVRControlsInitialized) return;
    isOrigVRControlsInitialized = true;

    const vrOrigZoomIn  = document.getElementById('vr-orig-zoom-in');
    const vrOrigZoomOut = document.getElementById('vr-orig-zoom-out');
    const vrOrigReset   = document.getElementById('vr-orig-reset-view');
    const vrOrigYaw     = document.getElementById('vr-orig-yaw');
    const vrOrigPitch   = document.getElementById('vr-orig-pitch');
    const vrOrigRoll    = document.getElementById('vr-orig-roll');
    const vrOrigFov     = document.getElementById('vr-orig-fov');

    function updateOrigVRRotationAndFov() {
        updateVRPlayerOrientation('orig');
    }

    [vrOrigYaw, vrOrigPitch, vrOrigRoll, vrOrigFov].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateOrigVRRotationAndFov);
            inp.addEventListener('change', updateOrigVRRotationAndFov);
        }
    });

    if (vrOrigZoomIn) {
        vrOrigZoomIn.addEventListener('click', () => zoomContainer(threeOrigCamera, vrOrigFov, -5));
    }

    if (vrOrigZoomOut) {
        vrOrigZoomOut.addEventListener('click', () => zoomContainer(threeOrigCamera, vrOrigFov, 5));
    }

    if (vrOrigReset) {
        vrOrigReset.addEventListener('click', () => handleResetView('orig'));
    }

    const origCopyLink = document.getElementById('orig-copy-link');
    if (origCopyLink && !origCopyLink._hasCopyListener) {
        origCopyLink._hasCopyListener = true;
        origCopyLink.addEventListener('click', (e) => copyVideoLink(originalVideo, e.currentTarget));
    }
}

function initThreeOrigJS() {
    if (isThreeOrigInitialized || !playerOrigContainer) return;

    const width  = playerOrigContainer.clientWidth;
    const height = playerOrigContainer.clientHeight;

    threeOrigScene = new THREE.Scene();
    threeOrigCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeOrigCamera.target = new THREE.Vector3(0, 0, 0);

    threeOrigRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeOrigRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeOrigRenderer.setSize(width, height);
    }
    playerOrigContainer.appendChild(threeOrigRenderer.domElement);

    threeOrigControls = new OrbitControls(threeOrigCamera, threeOrigRenderer.domElement);
    threeOrigControls.enableZoom  = false;
    threeOrigControls.enablePan   = false;
    threeOrigControls.rotateSpeed = -0.25;
    threeOrigControls.addEventListener('change', () => handleControlsChange('orig'));

    threeOrigCamera.position.set(0, 0, 0.1);
    threeOrigControls.target.set(0, 0, 0);
    threeOrigControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeOrigTexture = new THREE.VideoTexture(originalVideo);
    threeOrigTexture.colorSpace = THREE.SRGBColorSpace;
    threeOrigTexture.needsUpdate = true;

    const hasReadyVideo = Boolean(originalVideo && originalVideo.getAttribute('data-loaded-src') && originalVideo.readyState >= 2 && originalVideo.duration > 0 && !originalVideo.error);
    const initialOrigMap = hasReadyVideo ? threeOrigTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialOrigMap });
    threeOrigSphere = new THREE.Mesh(geometry, material);
    threeOrigSphere.rotation.y = -Math.PI / 2;
    threeOrigScene.add(threeOrigSphere);

    window.addEventListener('resize', onWindowResizeOrig);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeOrig();
        });
        resizeObserver.observe(playerOrigContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    playerOrigContainer.addEventListener('wheel', (e) => {
        if (!isThreeOrigInitialized || !threeOrigCamera) return;
        e.preventDefault();
        const delta = e.deltaY > 0 ? 5 : -5;
        zoomContainer(threeOrigCamera, document.getElementById('vr-orig-fov'), delta);
    }, { passive: false });

    initOrigVRControls();
    isThreeOrigInitialized = true;
    onWindowResizeOrig();
    animateOrig();
}

function onWindowResizeOrig() {
    if (!isThreeOrigInitialized || !playerOrigContainer) return;
    const width  = playerOrigContainer.clientWidth;
    const height = playerOrigContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeOrigCamera.aspect = width / height;
        threeOrigCamera.updateProjectionMatrix();
        threeOrigRenderer.setSize(width, height);
    }
}

function animateOrig() {
    if (!isThreeOrigInitialized) return;
    requestAnimationFrame(animateOrig);
    if (document.hidden || !playerOrigContainer || playerOrigContainer.clientWidth === 0 || playerOrigContainer.clientHeight === 0 || !isOrigVRMode) {
        return;
    }
    if (threeOrigTexture && originalVideo && originalVideo.readyState >= 1) {
        hideViewportLoader('orig-loader');
        if (threeOrigSphere && threeOrigSphere.material && threeOrigSphere.material.map !== threeOrigTexture) {
            threeOrigSphere.material.map = threeOrigTexture;
            threeOrigSphere.material.needsUpdate = true;
        }
    }
    threeOrigControls.update();
    threeOrigRenderer.render(threeOrigScene, threeOrigCamera);
}

// ── Three.js 360 Stitched Player ─────────────────────────────────────────────
function initThreeStitchedJS() {
    if (isThreeStitchedInitialized || !playerStitchedContainer) return;

    const width  = playerStitchedContainer.clientWidth;
    const height = playerStitchedContainer.clientHeight;

    threeStitchedScene = new THREE.Scene();
    threeStitchedCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeStitchedCamera.target = new THREE.Vector3(0, 0, 0);

    threeStitchedRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeStitchedRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeStitchedRenderer.setSize(width, height);
    }
    playerStitchedContainer.appendChild(threeStitchedRenderer.domElement);

    threeStitchedControls = new OrbitControls(threeStitchedCamera, threeStitchedRenderer.domElement);
    threeStitchedControls.enableZoom  = false;
    threeStitchedControls.enablePan   = false;
    threeStitchedControls.rotateSpeed = -0.25;
    threeStitchedControls.addEventListener('change', () => handleControlsChange('stitched'));

    threeStitchedCamera.position.set(0, 0, 0.1);
    threeStitchedControls.target.set(0, 0, 0);
    threeStitchedControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeStitchedTexture = new THREE.VideoTexture(videoStitched);
    threeStitchedTexture.colorSpace = THREE.SRGBColorSpace;
    threeStitchedTexture.needsUpdate = true;
    const hasReadyStitched = Boolean(videoStitched && videoStitched.getAttribute('data-loaded-src') && videoStitched.readyState >= 2 && videoStitched.duration > 0 && !videoStitched.error);
    const initialStitchedMap = hasReadyStitched ? threeStitchedTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialStitchedMap });
    threeStitchedSphere = new THREE.Mesh(geometry, material);
    threeStitchedSphere.rotation.y = -Math.PI / 2;
    threeStitchedScene.add(threeStitchedSphere);

    window.addEventListener('resize', onWindowResizeStitched);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeStitched();
        });
        resizeObserver.observe(playerStitchedContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    initStitchedVRControls();
    isThreeStitchedInitialized = true;
    onWindowResizeStitched();
    animateStitched();
}

function onWindowResizeStitched() {
    if (!isThreeStitchedInitialized || !playerStitchedContainer) return;
    const width  = playerStitchedContainer.clientWidth;
    const height = playerStitchedContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeStitchedCamera.aspect = width / height;
        threeStitchedCamera.updateProjectionMatrix();
        threeStitchedRenderer.setSize(width, height);
    }
}

function animateStitched() {
    if (!isThreeStitchedInitialized) return;
    requestAnimationFrame(animateStitched);
    if (document.hidden || !playerStitchedContainer || playerStitchedContainer.clientWidth === 0 || playerStitchedContainer.clientHeight === 0) {
        return;
    }
    if (threeStitchedTexture && videoStitched && videoStitched.readyState >= 1) {
        hideViewportLoader('stitched-loader');
        if (threeStitchedSphere && threeStitchedSphere.material && threeStitchedSphere.material.map !== threeStitchedTexture) {
            threeStitchedSphere.material.map = threeStitchedTexture;
            threeStitchedSphere.material.needsUpdate = true;
        }
    }
    threeStitchedControls.update();
    threeStitchedRenderer.render(threeStitchedScene, threeStitchedCamera);
}

function togglePlayStitched() {
    if (!videoStitched) return;
    if (videoStitched.paused) {
        videoStitched.play().then(() => {
            if (vrStitchedPlayPause) vrStitchedPlayPause.innerHTML = '&#10074;&#10074;';
        }).catch(err => {
            console.error("Stitched Video play failed:", err);
        });
    } else {
        videoStitched.pause();
        if (vrStitchedPlayPause) vrStitchedPlayPause.innerHTML = '&#9654;';
    }
}

function updateVRStitchedTimeDisplay() {
    if (!videoStitched) return;
    const cur = videoStitched.currentTime || 0;
    const dur = videoStitched.duration || 0;
    const fps = currentVideoMeta.fps || 30.0;
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    if (vrStitchedTimeDisplay) {
        vrStitchedTimeDisplay.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    }
    updateGotoFrameInputValue('vr-stitched-goto-frame', curF);
    updateGotoTimeInputValue('vr-stitched-goto-time', cur);
}

let isStitchedVRControlsInitialized = false;
function initStitchedVRControls() {
    if (isStitchedVRControlsInitialized || !videoStitched) return;
    isStitchedVRControlsInitialized = true;
    if (vrStitchedPlayPause) vrStitchedPlayPause.addEventListener('click', togglePlayStitched);

    videoStitched.addEventListener('timeupdate', () => {
        updateVRStitchedTimeDisplay();
        if (vrStitchedSeekbar && videoStitched.duration) {
            vrStitchedSeekbar.value = (videoStitched.currentTime / videoStitched.duration) * (vrStitchedSeekbar.max || 100);
        }
    });

    if (vrStitchedSeekbar) {
        vrStitchedSeekbar.addEventListener('input', () => {
            if (videoStitched.duration) {
                const targetSec = (parseFloat(vrStitchedSeekbar.value) / (vrStitchedSeekbar.max || 100)) * videoStitched.duration;
                syncSeekToTime(targetSec);
            }
        });
    }

    if (vrStitchedMute) {
        vrStitchedMute.addEventListener('click', () => {
            videoStitched.muted = !videoStitched.muted;
            vrStitchedMute.innerHTML = videoStitched.muted ? '&#128263;' : '&#128266;';
        });
    }

    if (vrStitchedVolume) {
        vrStitchedVolume.addEventListener('input', () => {
            videoStitched.volume = vrStitchedVolume.value;
            videoStitched.muted = (videoStitched.volume === 0);
            if (vrStitchedMute) vrStitchedMute.innerHTML = videoStitched.muted ? '&#128263;' : '&#128266;';
        });
    }

    const vrStitchedZoomIn  = document.getElementById('vr-stitched-zoom-in');
    const vrStitchedZoomOut = document.getElementById('vr-stitched-zoom-out');
    const vrStitchedReset   = document.getElementById('vr-stitched-reset-view');
    const vrStitchedYaw     = document.getElementById('vr-stitched-yaw');
    const vrStitchedPitch   = document.getElementById('vr-stitched-pitch');
    const vrStitchedRoll    = document.getElementById('vr-stitched-roll');
    const vrStitchedFov     = document.getElementById('vr-stitched-fov');

    function updateStitchedVRRotationAndFov() {
        updateVRPlayerOrientation('stitched');
    }

    [vrStitchedYaw, vrStitchedPitch, vrStitchedRoll, vrStitchedFov].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateStitchedVRRotationAndFov);
            inp.addEventListener('change', updateStitchedVRRotationAndFov);
        }
    });

    if (vrStitchedZoomIn) {
        vrStitchedZoomIn.addEventListener('click', () => zoomContainer(threeStitchedCamera, vrStitchedFov, -5));
    }

    if (vrStitchedZoomOut) {
        vrStitchedZoomOut.addEventListener('click', () => zoomContainer(threeStitchedCamera, vrStitchedFov, 5));
    }

    if (vrStitchedReset) {
        vrStitchedReset.addEventListener('click', () => handleResetView('stitched'));
    }

    const vrStitchedCopyLink = document.getElementById('vr-stitched-copy-link');
    if (vrStitchedCopyLink && !vrStitchedCopyLink._hasCopyListener) {
        vrStitchedCopyLink._hasCopyListener = true;
        vrStitchedCopyLink.addEventListener('click', (e) => copyVideoLink(videoStitched, e.currentTarget));
    }

    if (playerStitchedContainer) {
        playerStitchedContainer.addEventListener('wheel', (e) => {
            if (!isThreeStitchedInitialized || !threeStitchedCamera) return;
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeStitchedCamera, vrStitchedFov, delta);
        }, { passive: false });
    }

    if (vrStitchedFirstFrame) vrStitchedFirstFrame.addEventListener('click', syncFirstFrame);
    if (vrStitchedPrevFrame) vrStitchedPrevFrame.addEventListener('click', syncPrevFrame);
    if (vrStitchedNextFrame) vrStitchedNextFrame.addEventListener('click', syncNextFrame);
    if (vrStitchedLastFrame) vrStitchedLastFrame.addEventListener('click', syncLastFrame);
}

// ── Three.js 360 Telemetry Stabilized Player ─────────────────────────────────
let threeTelemetryScene, threeTelemetryCamera, threeTelemetryRenderer, threeTelemetryControls, threeTelemetrySphere, threeTelemetryTexture;
let isThreeTelemetryInitialized = false;
const playerTelemetryContainer = document.getElementById('player-telemetry-container');

function initThreeTelemetryJS() {
    if (isThreeTelemetryInitialized || !playerTelemetryContainer || !videoTelemetry) return;

    const width  = playerTelemetryContainer.clientWidth;
    const height = playerTelemetryContainer.clientHeight;

    threeTelemetryScene = new THREE.Scene();
    threeTelemetryCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeTelemetryCamera.target = new THREE.Vector3(0, 0, 0);

    threeTelemetryRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeTelemetryRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeTelemetryRenderer.setSize(width, height);
    }
    playerTelemetryContainer.appendChild(threeTelemetryRenderer.domElement);

    threeTelemetryControls = new OrbitControls(threeTelemetryCamera, threeTelemetryRenderer.domElement);
    threeTelemetryControls.enableZoom  = false;
    threeTelemetryControls.enablePan   = false;
    threeTelemetryControls.rotateSpeed = -0.25;
    threeTelemetryControls.addEventListener('change', () => handleControlsChange('telemetry'));

    threeTelemetryCamera.position.set(0, 0, 0.1);
    threeTelemetryControls.target.set(0, 0, 0);
    threeTelemetryControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeTelemetryTexture = new THREE.VideoTexture(videoTelemetry);
    threeTelemetryTexture.colorSpace = THREE.SRGBColorSpace;
    threeTelemetryTexture.needsUpdate = true;
    const hasReadyTelemetry = Boolean(videoTelemetry && videoTelemetry.getAttribute('data-loaded-src') && videoTelemetry.readyState >= 2 && videoTelemetry.duration > 0 && !videoTelemetry.error);
    const initialTelemetryMap = hasReadyTelemetry ? threeTelemetryTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialTelemetryMap });
    threeTelemetrySphere = new THREE.Mesh(geometry, material);
    threeTelemetrySphere.rotation.y = -Math.PI / 2;
    threeTelemetryScene.add(threeTelemetrySphere);

    window.addEventListener('resize', onWindowResizeTelemetry);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeTelemetry();
        });
        resizeObserver.observe(playerTelemetryContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    initTelemetryVRControls();
    isThreeTelemetryInitialized = true;
    onWindowResizeTelemetry();
    animateTelemetry();
}

function onWindowResizeTelemetry() {
    if (!isThreeTelemetryInitialized || !playerTelemetryContainer) return;
    const width  = playerTelemetryContainer.clientWidth;
    const height = playerTelemetryContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeTelemetryCamera.aspect = width / height;
        threeTelemetryCamera.updateProjectionMatrix();
        threeTelemetryRenderer.setSize(width, height);
    }
}

function animateTelemetry() {
    if (!isThreeTelemetryInitialized) return;
    requestAnimationFrame(animateTelemetry);
    if (document.hidden || !playerTelemetryContainer || playerTelemetryContainer.clientWidth === 0 || playerTelemetryContainer.clientHeight === 0) {
        return;
    }
    if (threeTelemetryTexture && videoTelemetry && videoTelemetry.readyState >= 1) {
        hideViewportLoader('telemetry-loader');
        if (threeTelemetrySphere && threeTelemetrySphere.material && threeTelemetrySphere.material.map !== threeTelemetryTexture) {
            threeTelemetrySphere.material.map = threeTelemetryTexture;
            threeTelemetrySphere.material.needsUpdate = true;
        }
    }
    threeTelemetryControls.update();
    threeTelemetryRenderer.render(threeTelemetryScene, threeTelemetryCamera);
}

// ── Three.js 360 Kabsch Stabilized Player ───────────────────────────────────
let threeKabschScene, threeKabschCamera, threeKabschRenderer, threeKabschControls, threeKabschSphere, threeKabschTexture;
let isThreeKabschInitialized = false;
const playerKabschContainer = document.getElementById('player-kabsch-container');

function initThreeKabschJS() {
    if (isThreeKabschInitialized || !playerKabschContainer || !videoKabsch) return;

    const width  = playerKabschContainer.clientWidth;
    const height = playerKabschContainer.clientHeight;

    threeKabschScene = new THREE.Scene();
    threeKabschCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeKabschCamera.target = new THREE.Vector3(0, 0, 0);

    threeKabschRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeKabschRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeKabschRenderer.setSize(width, height);
    }
    playerKabschContainer.appendChild(threeKabschRenderer.domElement);

    threeKabschControls = new OrbitControls(threeKabschCamera, threeKabschRenderer.domElement);
    threeKabschControls.enableZoom  = false;
    threeKabschControls.enablePan   = false;
    threeKabschControls.rotateSpeed = -0.25;
    threeKabschControls.addEventListener('change', () => handleControlsChange('kabsch'));

    threeKabschCamera.position.set(0, 0, 0.1);
    threeKabschControls.target.set(0, 0, 0);
    threeKabschControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeKabschTexture = new THREE.VideoTexture(videoKabsch);
    threeKabschTexture.colorSpace = THREE.SRGBColorSpace;
    threeKabschTexture.needsUpdate = true;
    const hasReadyKabsch = Boolean(videoKabsch && videoKabsch.getAttribute('data-loaded-src') && videoKabsch.readyState >= 2 && videoKabsch.duration > 0 && !videoKabsch.error);
    const initialKabschMap = hasReadyKabsch ? threeKabschTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialKabschMap });
    threeKabschSphere = new THREE.Mesh(geometry, material);
    threeKabschSphere.rotation.y = -Math.PI / 2;
    threeKabschScene.add(threeKabschSphere);

    window.addEventListener('resize', onWindowResizeKabsch);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeKabsch();
        });
        resizeObserver.observe(playerKabschContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    initKabschVRControls();
    isThreeKabschInitialized = true;
    onWindowResizeKabsch();
    animateKabsch();
}

function onWindowResizeKabsch() {
    if (!isThreeKabschInitialized || !playerKabschContainer) return;
    const width  = playerKabschContainer.clientWidth;
    const height = playerKabschContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeKabschCamera.aspect = width / height;
        threeKabschCamera.updateProjectionMatrix();
        threeKabschRenderer.setSize(width, height);
    }
}

function animateKabsch() {
    if (!isThreeKabschInitialized) return;
    requestAnimationFrame(animateKabsch);
    if (document.hidden || !playerKabschContainer || playerKabschContainer.clientWidth === 0 || playerKabschContainer.clientHeight === 0) {
        return;
    }
    if (threeKabschTexture && videoKabsch && videoKabsch.readyState >= 1) {
        hideViewportLoader('kabsch-loader');
        if (threeKabschSphere && threeKabschSphere.material && threeKabschSphere.material.map !== threeKabschTexture) {
            threeKabschSphere.material.map = threeKabschTexture;
            threeKabschSphere.material.needsUpdate = true;
        }
    }
    threeKabschControls.update();
    threeKabschRenderer.render(threeKabschScene, threeKabschCamera);
}

// ── Three.js 360 Kopf Stabilized Player ─────────────────────────────────────
let threeKopfScene, threeKopfCamera, threeKopfRenderer, threeKopfControls, threeKopfSphere, threeKopfTexture;
let isThreeKopfInitialized = false;
const playerKopfContainer = document.getElementById('player-kopf-container');

function initThreeKopfJS() {
    if (isThreeKopfInitialized || !playerKopfContainer || !videoKopf) return;

    const width  = playerKopfContainer.clientWidth;
    const height = playerKopfContainer.clientHeight;

    threeKopfScene = new THREE.Scene();
    threeKopfCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeKopfCamera.target = new THREE.Vector3(0, 0, 0);

    threeKopfRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeKopfRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeKopfRenderer.setSize(width, height);
    }
    playerKopfContainer.appendChild(threeKopfRenderer.domElement);

    threeKopfControls = new OrbitControls(threeKopfCamera, threeKopfRenderer.domElement);
    threeKopfControls.enableZoom  = false;
    threeKopfControls.enablePan   = false;
    threeKopfControls.rotateSpeed = -0.25;
    threeKopfControls.addEventListener('change', () => handleControlsChange('kopf'));

    threeKopfCamera.position.set(0, 0, 0.1);
    threeKopfControls.target.set(0, 0, 0);
    threeKopfControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeKopfTexture = new THREE.VideoTexture(videoKopf);
    threeKopfTexture.colorSpace = THREE.SRGBColorSpace;
    threeKopfTexture.needsUpdate = true;
    const hasReadyKopf = Boolean(videoKopf && videoKopf.getAttribute('data-loaded-src') && videoKopf.readyState >= 2 && videoKopf.duration > 0 && !videoKopf.error);
    const initialKopfMap = hasReadyKopf ? threeKopfTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialKopfMap });
    threeKopfSphere = new THREE.Mesh(geometry, material);
    threeKopfSphere.rotation.y = -Math.PI / 2;
    threeKopfScene.add(threeKopfSphere);

    window.addEventListener('resize', onWindowResizeKopf);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeKopf();
        });
        resizeObserver.observe(playerKopfContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    initKopfVRControls();
    isThreeKopfInitialized = true;
    onWindowResizeKopf();
    animateKopf();
}

function onWindowResizeKopf() {
    if (!isThreeKopfInitialized || !playerKopfContainer) return;
    const width  = playerKopfContainer.clientWidth;
    const height = playerKopfContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeKopfCamera.aspect = width / height;
        threeKopfCamera.updateProjectionMatrix();
        threeKopfRenderer.setSize(width, height);
    }
}

function animateKopf() {
    if (!isThreeKopfInitialized) return;
    requestAnimationFrame(animateKopf);
    if (document.hidden || !playerKopfContainer || playerKopfContainer.clientWidth === 0 || playerKopfContainer.clientHeight === 0) {
        return;
    }
    if (threeKopfTexture && videoKopf && videoKopf.readyState >= 1) {
        hideViewportLoader('kopf-loader');
        if (threeKopfSphere && threeKopfSphere.material && threeKopfSphere.material.map !== threeKopfTexture) {
            threeKopfSphere.material.map = threeKopfTexture;
            threeKopfSphere.material.needsUpdate = true;
        }
    }
    threeKopfControls.update();
    threeKopfRenderer.render(threeKopfScene, threeKopfCamera);
}

// ── Three.js 360 Horizon Leveled Player ─────────────────────────────────────
let threeHorizonScene, threeHorizonCamera, threeHorizonRenderer, threeHorizonTexture, threeHorizonSphere, threeHorizonControls;
let isThreeHorizonInitialized = false;
const playerHorizonContainer = document.getElementById('player-horizon-container');
const playerCheckpointsContainer = playerHorizonContainer;

function initThreeHorizonJS() {
    if (isThreeHorizonInitialized || !playerHorizonContainer || !videoHorizon) return;

    const width  = playerHorizonContainer.clientWidth;
    const height = playerHorizonContainer.clientHeight;

    threeHorizonScene = new THREE.Scene();
    threeHorizonCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeHorizonCamera.target = new THREE.Vector3(0, 0, 0);

    threeHorizonRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeHorizonRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeHorizonRenderer.setSize(width, height);
    }
    playerHorizonContainer.appendChild(threeHorizonRenderer.domElement);

    threeHorizonControls = new OrbitControls(threeHorizonCamera, threeHorizonRenderer.domElement);
    threeHorizonControls.enableZoom  = false;
    threeHorizonControls.enablePan   = false;
    threeHorizonControls.rotateSpeed = -0.25;
    threeHorizonControls.addEventListener('change', () => handleControlsChange('horizon'));

    threeHorizonCamera.position.set(0, 0, 0.1);
    threeHorizonControls.target.set(0, 0, 0);
    threeHorizonControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeHorizonTexture = new THREE.VideoTexture(videoHorizon);
    threeHorizonTexture.colorSpace = THREE.SRGBColorSpace;
    threeHorizonTexture.needsUpdate = true;
    const hasReadyHorizon = Boolean(videoHorizon && videoHorizon.getAttribute('data-loaded-src') && videoHorizon.readyState >= 2 && videoHorizon.duration > 0 && !videoHorizon.error);
    const initialHorizonMap = hasReadyHorizon ? threeHorizonTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialHorizonMap });
    threeHorizonSphere = new THREE.Mesh(geometry, material);
    threeHorizonSphere.rotation.y = -Math.PI / 2;
    threeHorizonScene.add(threeHorizonSphere);

    window.addEventListener('resize', onWindowResizeHorizon);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeHorizon();
        });
        resizeObserver.observe(playerHorizonContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    initHorizonVRControls();
    isThreeHorizonInitialized = true;
    onWindowResizeHorizon();
    animateHorizon();
}

function onWindowResizeHorizon() {
    if (!isThreeHorizonInitialized || !playerHorizonContainer) return;
    const width  = playerHorizonContainer.clientWidth;
    const height = playerHorizonContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeHorizonCamera.aspect = width / height;
        threeHorizonCamera.updateProjectionMatrix();
        threeHorizonRenderer.setSize(width, height);
    }
}

function animateHorizon() {
    if (!isThreeHorizonInitialized) return;
    requestAnimationFrame(animateHorizon);
    if (document.hidden || !playerHorizonContainer || playerHorizonContainer.clientWidth === 0 || playerHorizonContainer.clientHeight === 0) {
        return;
    }
    if (threeHorizonTexture && videoHorizon && videoHorizon.readyState >= 1) {
        hideViewportLoader('horizon-loader');
        if (threeHorizonSphere && threeHorizonSphere.material && threeHorizonSphere.material.map !== threeHorizonTexture) {
            threeHorizonSphere.material.map = threeHorizonTexture;
            threeHorizonSphere.material.needsUpdate = true;
        }
    }
    threeHorizonControls.update();
    threeHorizonRenderer.render(threeHorizonScene, threeHorizonCamera);
}

let isHorizonVRControlsInitialized = false;
function initHorizonVRControls() {
    if (isHorizonVRControlsInitialized || !videoHorizon) return;
    isHorizonVRControlsInitialized = true;
    const playPauseBtn = document.getElementById('vr-horizon-play-pause');
    const firstFrameBtn = document.getElementById('vr-horizon-first-frame');
    const prevFrameBtn = document.getElementById('vr-horizon-prev-frame');
    const nextFrameBtn = document.getElementById('vr-horizon-next-frame');
    const lastFrameBtn = document.getElementById('vr-horizon-last-frame');
    const gotoFrameInput = document.getElementById('vr-horizon-goto-frame');
    const yawInput = document.getElementById('vr-horizon-yaw');
    const pitchInput = document.getElementById('vr-horizon-pitch');
    const rollInput = document.getElementById('vr-horizon-roll');
    const fovInput = document.getElementById('vr-horizon-fov');
    const zoomInBtn = document.getElementById('vr-horizon-zoom-in');
    const zoomOutBtn = document.getElementById('vr-horizon-zoom-out');
    const resetViewBtn = document.getElementById('vr-horizon-reset-view');
    const copyLinkBtn = document.getElementById('vr-horizon-copy-link');
    const fullscreenBtn = document.getElementById('vr-horizon-fullscreen');
    const realFullscreenBtn = document.getElementById('vr-horizon-real-fullscreen');

    if (playPauseBtn) playPauseBtn.addEventListener('click', () => togglePlayVideo(videoHorizon));
    if (firstFrameBtn) firstFrameBtn.addEventListener('click', syncFirstFrame);
    if (prevFrameBtn)  prevFrameBtn.addEventListener('click', syncPrevFrame);
    if (nextFrameBtn)  nextFrameBtn.addEventListener('click', syncNextFrame);
    if (lastFrameBtn)  lastFrameBtn.addEventListener('click', syncLastFrame);

    if (videoHorizon) {
        videoHorizon.addEventListener('timeupdate', updateHorizonTimeDisplay);
        videoHorizon.addEventListener('play', updatePlayPauseButtonIcons);
        videoHorizon.addEventListener('pause', updatePlayPauseButtonIcons);
        videoHorizon.addEventListener('ended', updatePlayPauseButtonIcons);
    }

    if (gotoFrameInput) {
        gotoFrameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const targetFrame = parseInt(gotoFrameInput.value);
                const fps = currentVideoMeta.fps || 29.97;
                if (!isNaN(targetFrame) && fps > 0) {
                    syncSeekToTime(targetFrame / fps);
                }
            }
        });
    }

    function updateHorizonVRRotationAndFov() {
        updateVRPlayerOrientation('horizon');
    }

    [yawInput, pitchInput, rollInput, fovInput].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateHorizonVRRotationAndFov);
            inp.addEventListener('change', updateHorizonVRRotationAndFov);
        }
    });

    if (resetViewBtn) {
        resetViewBtn.addEventListener('click', () => handleResetView('horizon'));
    }

    if (zoomInBtn) {
        zoomInBtn.addEventListener('click', () => zoomContainer(threeHorizonCamera, fovInput, -5));
    }

    if (zoomOutBtn) {
        zoomOutBtn.addEventListener('click', () => zoomContainer(threeHorizonCamera, fovInput, 5));
    }

    if (playerHorizonContainer) {
        playerHorizonContainer.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeHorizonCamera, fovInput, delta);
        }, { passive: false });
    }

    if (copyLinkBtn && videoHorizon) {
        copyLinkBtn.addEventListener('click', (e) => copyVideoLink(videoHorizon, e.currentTarget));
    }

    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', togglePopupHorizon);
    }

    if (realFullscreenBtn && playerHorizonContainer) {
        realFullscreenBtn.addEventListener('click', () => {
            toggleRealFullscreen(playerHorizonContainer);
        });
    }
}

const initThreeCheckpointsJS = initThreeHorizonJS;
const onWindowResizeCheckpoints = onWindowResizeHorizon;
const initCheckpointsVRControls = initHorizonVRControls;
const isThreeCheckpointsInitialized = isThreeHorizonInitialized;

// ── Three.js 360 Vidstab Stabilized Player ─────────────────────────────────
let threeVidstabScene, threeVidstabCamera, threeVidstabRenderer, threeVidstabControls, threeVidstabSphere, threeVidstabTexture;
let isThreeVidstabInitialized = false;
const playerVidstabContainer = document.getElementById('player-vidstab-container');
const playerOpticalContainer = playerVidstabContainer;

function initThreeVidstabJS() {
    if (isThreeVidstabInitialized || !playerVidstabContainer || !videoVidstab) return;

    const width  = playerVidstabContainer.clientWidth;
    const height = playerVidstabContainer.clientHeight;

    threeVidstabScene = new THREE.Scene();
    threeVidstabCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeVidstabCamera.target = new THREE.Vector3(0, 0, 0);

    threeVidstabRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeVidstabRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeVidstabRenderer.setSize(width, height);
    }
    playerVidstabContainer.appendChild(threeVidstabRenderer.domElement);

    threeVidstabControls = new OrbitControls(threeVidstabCamera, threeVidstabRenderer.domElement);
    threeVidstabControls.enableZoom  = false;
    threeVidstabControls.enablePan   = false;
    threeVidstabControls.rotateSpeed = -0.25;
    threeVidstabControls.addEventListener('change', () => handleControlsChange('vidstab'));

    threeVidstabCamera.position.set(0, 0, 0.1);
    threeVidstabControls.target.set(0, 0, 0);
    threeVidstabControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeVidstabTexture = new THREE.VideoTexture(videoVidstab);
    threeVidstabTexture.colorSpace = THREE.SRGBColorSpace;
    threeVidstabTexture.needsUpdate = true;
    const hasReadyVidstab = Boolean(videoVidstab && videoVidstab.getAttribute('data-loaded-src') && videoVidstab.readyState >= 2 && videoVidstab.duration > 0 && !videoVidstab.error);
    const initialVidstabMap = hasReadyVidstab ? threeVidstabTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialVidstabMap });
    threeVidstabSphere = new THREE.Mesh(geometry, material);
    threeVidstabSphere.rotation.y = -Math.PI / 2;
    threeVidstabScene.add(threeVidstabSphere);

    window.addEventListener('resize', onWindowResizeVidstab);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeVidstab();
        });
        resizeObserver.observe(playerVidstabContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    initVidstabVRControls();
    isThreeVidstabInitialized = true;
    onWindowResizeVidstab();
    animateVidstab();
}

function onWindowResizeVidstab() {
    if (!isThreeVidstabInitialized || !playerVidstabContainer) return;
    const width  = playerVidstabContainer.clientWidth;
    const height = playerVidstabContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeVidstabCamera.aspect = width / height;
        threeVidstabCamera.updateProjectionMatrix();
        threeVidstabRenderer.setSize(width, height);
    }
}

function animateVidstab() {
    if (!isThreeVidstabInitialized) return;
    requestAnimationFrame(animateVidstab);
    if (document.hidden || !playerVidstabContainer || playerVidstabContainer.clientWidth === 0 || playerVidstabContainer.clientHeight === 0) {
        return;
    }
    if (threeVidstabTexture && videoVidstab && videoVidstab.readyState >= 1) {
        hideViewportLoader('vidstab-loader');
        if (threeVidstabSphere && threeVidstabSphere.material && threeVidstabSphere.material.map !== threeVidstabTexture) {
            threeVidstabSphere.material.map = threeVidstabTexture;
            threeVidstabSphere.material.needsUpdate = true;
        }
    }
    threeVidstabControls.update();
    threeVidstabRenderer.render(threeVidstabScene, threeVidstabCamera);
}

const initThreeOpticalJS = initThreeVidstabJS;
const onWindowResizeOptical = onWindowResizeVidstab;
const animateOptical = animateVidstab;
const isThreeOpticalInitialized = isThreeVidstabInitialized;

function updateTelemetryTimeDisplay() {
    if (!videoTelemetry) return;
    const cur = videoTelemetry.currentTime || 0;
    const dur = videoTelemetry.duration || 0;
    const fps = currentVideoMeta.fps || 30.0;
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    const display = document.getElementById('vr-telemetry-time-display');
    if (display) {
        display.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    }
    updateGotoFrameInputValue('vr-telemetry-goto-frame', curF);
    updateGotoTimeInputValue('vr-telemetry-goto-time', cur);
}

function updateKabschTimeDisplay() {
    if (!videoKabsch) return;
    const cur = videoKabsch.currentTime || 0;
    const dur = videoKabsch.duration || 0;
    const fps = currentVideoMeta.fps || 30.0;
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    const display = document.getElementById('vr-kabsch-time-display');
    if (display) {
        display.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    }
    updateGotoFrameInputValue('vr-kabsch-goto-frame', curF);
    updateGotoTimeInputValue('vr-kabsch-goto-time', cur);
}

function updateKopfTimeDisplay() {
    if (!videoKopf) return;
    const cur = videoKopf.currentTime || 0;
    const dur = videoKopf.duration || 0;
    const fps = currentVideoMeta.fps || 30.0;
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    const display = document.getElementById('vr-kopf-time-display');
    if (display) {
        display.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    }
    updateGotoFrameInputValue('vr-kopf-goto-frame', curF);
    updateGotoTimeInputValue('vr-kopf-goto-time', cur);
}

function updateVidstabTimeDisplay() {
    if (!videoVidstab) return;
    const cur = videoVidstab.currentTime || 0;
    const dur = videoVidstab.duration || 0;
    const fps = currentVideoMeta.fps || 30.0;
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    const display = document.getElementById('vr-vidstab-time-display');
    if (display) {
        display.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    }
    updateGotoFrameInputValue('vr-vidstab-goto-frame', curF);
    updateGotoTimeInputValue('vr-vidstab-goto-time', cur);
}
const updateOpticalTimeDisplay = updateVidstabTimeDisplay;

function updateCinematicTimeDisplay() {
    if (!videoCinematic) return;
    const cur = isFinite(videoCinematic.currentTime) ? videoCinematic.currentTime : 0;
    const dur = isFinite(videoCinematic.duration) ? videoCinematic.duration : 0;
    const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 30.0;
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    const display = document.getElementById('vr-cinematic-time-display');
    if (display) {
        display.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    }
    updateGotoFrameInputValue('vr-cinematic-goto-frame', curF);
    updateGotoTimeInputValue('vr-cinematic-goto-time', cur);
}

let isKabschVRControlsInitialized = false;
function initKabschVRControls() {
    if (isKabschVRControlsInitialized || !videoKabsch) return;
    isKabschVRControlsInitialized = true;
    const playPauseBtn = document.getElementById('vr-kabsch-play-pause');
    const seekbar      = document.getElementById('vr-kabsch-seekbar');
    const muteBtn      = document.getElementById('vr-kabsch-mute');
    const volumeSlider = document.getElementById('vr-kabsch-volume');
    const firstFrame   = document.getElementById('vr-kabsch-first-frame');
    const prevFrame    = document.getElementById('vr-kabsch-prev-frame');
    const nextFrame    = document.getElementById('vr-kabsch-next-frame');
    const lastFrame    = document.getElementById('vr-kabsch-last-frame');
    const copyLink     = document.getElementById('vr-kabsch-copy-link');

    if (playPauseBtn) playPauseBtn.addEventListener('click', () => togglePlayVideo(videoKabsch));
    if (firstFrame)   firstFrame.addEventListener('click', syncFirstFrame);
    if (prevFrame)    prevFrame.addEventListener('click', syncPrevFrame);
    if (nextFrame)    nextFrame.addEventListener('click', syncNextFrame);
    if (lastFrame)    lastFrame.addEventListener('click', syncLastFrame);
    if (copyLink)     copyLink.addEventListener('click', (e) => copyVideoLink(videoKabsch, e.currentTarget));

    if (seekbar) {
        seekbar.addEventListener('input', () => {
            if (videoKabsch.duration) {
                const targetTime = (parseFloat(seekbar.value) / (seekbar.max || 100)) * videoKabsch.duration;
                syncSeekToTime(targetTime);
            }
        });
    }

    if (muteBtn) {
        muteBtn.addEventListener('click', () => {
            videoKabsch.muted = !videoKabsch.muted;
            muteBtn.textContent = videoKabsch.muted ? '🔇' : '🔊';
        });
    }
    if (volumeSlider) {
        volumeSlider.addEventListener('input', () => {
            videoKabsch.volume = parseFloat(volumeSlider.value);
            videoKabsch.muted = videoKabsch.volume === 0;
            if (muteBtn) muteBtn.textContent = videoKabsch.muted ? '🔇' : '🔊';
        });
    }

    const vrKabschZoomIn  = document.getElementById('vr-kabsch-zoom-in');
    const vrKabschZoomOut = document.getElementById('vr-kabsch-zoom-out');
    const vrKabschReset   = document.getElementById('vr-kabsch-reset-view');
    const vrKabschYaw     = document.getElementById('vr-kabsch-yaw');
    const vrKabschPitch   = document.getElementById('vr-kabsch-pitch');
    const vrKabschRoll    = document.getElementById('vr-kabsch-roll');
    const vrKabschFov     = document.getElementById('vr-kabsch-fov');

    function updateKabschVRRotationAndFov() {
        updateVRPlayerOrientation('kabsch');
    }

    [vrKabschYaw, vrKabschPitch, vrKabschRoll, vrKabschFov].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateKabschVRRotationAndFov);
            inp.addEventListener('change', updateKabschVRRotationAndFov);
        }
    });

    if (vrKabschZoomIn) {
        vrKabschZoomIn.addEventListener('click', () => zoomContainer(threeKabschCamera, vrKabschFov, -5));
    }

    if (vrKabschZoomOut) {
        vrKabschZoomOut.addEventListener('click', () => zoomContainer(threeKabschCamera, vrKabschFov, 5));
    }

    if (vrKabschReset) {
        vrKabschReset.addEventListener('click', () => handleResetView('kabsch'));
    }

    if (playerKabschContainer) {
        playerKabschContainer.addEventListener('wheel', (e) => {
            if (!isThreeKabschInitialized || !threeKabschCamera) return;
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeKabschCamera, vrKabschFov, delta);
        }, { passive: false });
    }

    videoKabsch.addEventListener('timeupdate', () => {
        updateKabschTimeDisplay();
        if (seekbar && videoKabsch.duration) {
            seekbar.value = (videoKabsch.currentTime / videoKabsch.duration) * (seekbar.max || 100);
        }
    });
}

let isKopfVRControlsInitialized = false;
function initKopfVRControls() {
    if (isKopfVRControlsInitialized || !videoKopf) return;
    isKopfVRControlsInitialized = true;
    const playPauseBtn = document.getElementById('vr-kopf-play-pause');
    const seekbar      = document.getElementById('vr-kopf-seekbar');
    const muteBtn      = document.getElementById('vr-kopf-mute');
    const volumeSlider = document.getElementById('vr-kopf-volume');
    const firstFrame   = document.getElementById('vr-kopf-first-frame');
    const prevFrame    = document.getElementById('vr-kopf-prev-frame');
    const nextFrame    = document.getElementById('vr-kopf-next-frame');
    const lastFrame    = document.getElementById('vr-kopf-last-frame');
    const copyLink     = document.getElementById('vr-kopf-copy-link');

    if (playPauseBtn) playPauseBtn.addEventListener('click', () => togglePlayVideo(videoKopf));
    if (firstFrame)   firstFrame.addEventListener('click', syncFirstFrame);
    if (prevFrame)    prevFrame.addEventListener('click', syncPrevFrame);
    if (nextFrame)    nextFrame.addEventListener('click', syncNextFrame);
    if (lastFrame)    lastFrame.addEventListener('click', syncLastFrame);
    if (copyLink)     copyLink.addEventListener('click', (e) => copyVideoLink(videoKopf, e.currentTarget));

    if (seekbar) {
        seekbar.addEventListener('input', () => {
            if (videoKopf.duration) {
                const targetTime = (parseFloat(seekbar.value) / (seekbar.max || 100)) * videoKopf.duration;
                syncSeekToTime(targetTime);
            }
        });
    }

    if (muteBtn) {
        muteBtn.addEventListener('click', () => {
            videoKopf.muted = !videoKopf.muted;
            muteBtn.textContent = videoKopf.muted ? '🔇' : '🔊';
        });
    }
    if (volumeSlider) {
        volumeSlider.addEventListener('input', () => {
            videoKopf.volume = parseFloat(volumeSlider.value);
            videoKopf.muted = videoKopf.volume === 0;
            if (muteBtn) muteBtn.textContent = videoKopf.muted ? '🔇' : '🔊';
        });
    }

    const vrKopfZoomIn  = document.getElementById('vr-kopf-zoom-in');
    const vrKopfZoomOut = document.getElementById('vr-kopf-zoom-out');
    const vrKopfReset   = document.getElementById('vr-kopf-reset-view');
    const vrKopfYaw     = document.getElementById('vr-kopf-yaw');
    const vrKopfPitch   = document.getElementById('vr-kopf-pitch');
    const vrKopfRoll    = document.getElementById('vr-kopf-roll');
    const vrKopfFov     = document.getElementById('vr-kopf-fov');

    function updateKopfVRRotationAndFov() {
        updateVRPlayerOrientation('kopf');
    }

    [vrKopfYaw, vrKopfPitch, vrKopfRoll, vrKopfFov].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateKopfVRRotationAndFov);
            inp.addEventListener('change', updateKopfVRRotationAndFov);
        }
    });

    if (vrKopfZoomIn) {
        vrKopfZoomIn.addEventListener('click', () => zoomContainer(threeKopfCamera, vrKopfFov, -5));
    }

    if (vrKopfZoomOut) {
        vrKopfZoomOut.addEventListener('click', () => zoomContainer(threeKopfCamera, vrKopfFov, 5));
    }

    if (vrKopfReset) {
        vrKopfReset.addEventListener('click', () => handleResetView('kopf'));
    }

    if (playerKopfContainer) {
        playerKopfContainer.addEventListener('wheel', (e) => {
            if (!isThreeKopfInitialized || !threeKopfCamera) return;
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeKopfCamera, vrKopfFov, delta);
        }, { passive: false });
    }

    videoKopf.addEventListener('timeupdate', () => {
        updateKopfTimeDisplay();
        if (seekbar && videoKopf.duration) {
            seekbar.value = (videoKopf.currentTime / videoKopf.duration) * (seekbar.max || 100);
        }
    });
}

let isTelemetryVRControlsInitialized = false;
function initTelemetryVRControls() {
    if (isTelemetryVRControlsInitialized || !videoTelemetry) return;
    isTelemetryVRControlsInitialized = true;
    const playPauseBtn = document.getElementById('vr-telemetry-play-pause');
    const seekbar      = document.getElementById('vr-telemetry-seekbar');
    const muteBtn      = document.getElementById('vr-telemetry-mute');
    const volumeSlider = document.getElementById('vr-telemetry-volume');
    const firstFrame   = document.getElementById('vr-telemetry-first-frame');
    const prevFrame    = document.getElementById('vr-telemetry-prev-frame');
    const nextFrame    = document.getElementById('vr-telemetry-next-frame');
    const lastFrame    = document.getElementById('vr-telemetry-last-frame');
    const copyLink     = document.getElementById('vr-telemetry-copy-link');

    if (playPauseBtn) playPauseBtn.addEventListener('click', () => togglePlayVideo(videoTelemetry));
    if (firstFrame)   firstFrame.addEventListener('click', syncFirstFrame);
    if (prevFrame)    prevFrame.addEventListener('click', syncPrevFrame);
    if (nextFrame)    nextFrame.addEventListener('click', syncNextFrame);
    if (lastFrame)    lastFrame.addEventListener('click', syncLastFrame);
    if (copyLink)     copyLink.addEventListener('click', (e) => copyVideoLink(videoTelemetry, e.currentTarget));

    if (seekbar) {
        seekbar.addEventListener('input', () => {
            if (videoTelemetry.duration) {
                const targetTime = (parseFloat(seekbar.value) / (seekbar.max || 100)) * videoTelemetry.duration;
                syncSeekToTime(targetTime);
            }
        });
    }

    if (muteBtn) {
        muteBtn.addEventListener('click', () => {
            videoTelemetry.muted = !videoTelemetry.muted;
            muteBtn.textContent = videoTelemetry.muted ? '🔇' : '🔊';
        });
    }
    if (volumeSlider) {
        volumeSlider.addEventListener('input', () => {
            videoTelemetry.volume = parseFloat(volumeSlider.value);
            videoTelemetry.muted = videoTelemetry.volume === 0;
            if (muteBtn) muteBtn.textContent = videoTelemetry.muted ? '🔇' : '🔊';
        });
    }

    const vrTelemetryZoomIn  = document.getElementById('vr-telemetry-zoom-in');
    const vrTelemetryZoomOut = document.getElementById('vr-telemetry-zoom-out');
    const vrTelemetryReset   = document.getElementById('vr-telemetry-reset-view');
    const vrTelemetryYaw     = document.getElementById('vr-telemetry-yaw');
    const vrTelemetryPitch   = document.getElementById('vr-telemetry-pitch');
    const vrTelemetryRoll    = document.getElementById('vr-telemetry-roll');
    const vrTelemetryFov     = document.getElementById('vr-telemetry-fov');

    function updateTelemetryVRRotationAndFov() {
        updateVRPlayerOrientation('telemetry');
    }

    [vrTelemetryYaw, vrTelemetryPitch, vrTelemetryRoll, vrTelemetryFov].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateTelemetryVRRotationAndFov);
            inp.addEventListener('change', updateTelemetryVRRotationAndFov);
        }
    });

    if (vrTelemetryZoomIn) {
        vrTelemetryZoomIn.addEventListener('click', () => zoomContainer(threeTelemetryCamera, vrTelemetryFov, -5));
    }

    if (vrTelemetryZoomOut) {
        vrTelemetryZoomOut.addEventListener('click', () => zoomContainer(threeTelemetryCamera, vrTelemetryFov, 5));
    }

    if (vrTelemetryReset) {
        vrTelemetryReset.addEventListener('click', () => handleResetView('telemetry'));
    }

    if (playerTelemetryContainer) {
        playerTelemetryContainer.addEventListener('wheel', (e) => {
            if (!isThreeTelemetryInitialized || !threeTelemetryCamera) return;
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeTelemetryCamera, vrTelemetryFov, delta);
        }, { passive: false });
    }

    videoTelemetry.addEventListener('timeupdate', () => {
        updateTelemetryTimeDisplay();
        if (seekbar && videoTelemetry.duration) {
            seekbar.value = (videoTelemetry.currentTime / videoTelemetry.duration) * (seekbar.max || 100);
        }
    });
}
const initTelemetryControls = initTelemetryVRControls;

let isVidstabVRControlsInitialized = false;
function initVidstabVRControls() {
    if (isVidstabVRControlsInitialized || !videoVidstab) return;
    isVidstabVRControlsInitialized = true;
    const playPauseBtn = document.getElementById('vr-vidstab-play-pause');
    const seekbar      = document.getElementById('vr-vidstab-seekbar');
    const muteBtn      = document.getElementById('vr-vidstab-mute');
    const volumeSlider = document.getElementById('vr-vidstab-volume');
    const firstFrame   = document.getElementById('vr-vidstab-first-frame');
    const prevFrame    = document.getElementById('vr-vidstab-prev-frame');
    const nextFrame    = document.getElementById('vr-vidstab-next-frame');
    const lastFrame    = document.getElementById('vr-vidstab-last-frame');
    const copyLink     = document.getElementById('vr-vidstab-copy-link');

    if (playPauseBtn) playPauseBtn.addEventListener('click', () => togglePlayVideo(videoVidstab));
    if (firstFrame)   firstFrame.addEventListener('click', syncFirstFrame);
    if (prevFrame)    prevFrame.addEventListener('click', syncPrevFrame);
    if (nextFrame)    nextFrame.addEventListener('click', syncNextFrame);
    if (lastFrame)    lastFrame.addEventListener('click', syncLastFrame);
    if (copyLink)     copyLink.addEventListener('click', (e) => copyVideoLink(videoVidstab, e.currentTarget));

    if (seekbar) {
        seekbar.addEventListener('input', () => {
            if (videoVidstab.duration) {
                const targetTime = (parseFloat(seekbar.value) / (seekbar.max || 100)) * videoVidstab.duration;
                syncSeekToTime(targetTime);
            }
        });
    }

    if (muteBtn) {
        muteBtn.addEventListener('click', () => {
            videoVidstab.muted = !videoVidstab.muted;
            muteBtn.textContent = videoVidstab.muted ? '🔇' : '🔊';
        });
    }
    if (volumeSlider) {
        volumeSlider.addEventListener('input', () => {
            videoVidstab.volume = parseFloat(volumeSlider.value);
            videoVidstab.muted = videoVidstab.volume === 0;
            if (muteBtn) muteBtn.textContent = videoVidstab.muted ? '🔇' : '🔊';
        });
    }

    const vrVidstabZoomIn  = document.getElementById('vr-vidstab-zoom-in');
    const vrVidstabZoomOut = document.getElementById('vr-vidstab-zoom-out');
    const vrVidstabReset   = document.getElementById('vr-vidstab-reset-view');
    const vrVidstabYaw     = document.getElementById('vr-vidstab-yaw');
    const vrVidstabPitch   = document.getElementById('vr-vidstab-pitch');
    const vrVidstabRoll    = document.getElementById('vr-vidstab-roll');
    const vrVidstabFov     = document.getElementById('vr-vidstab-fov');

    function updateVidstabVRRotationAndFov() {
        updateVRPlayerOrientation('vidstab');
    }

    [vrVidstabYaw, vrVidstabPitch, vrVidstabRoll, vrVidstabFov].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateVidstabVRRotationAndFov);
            inp.addEventListener('change', updateVidstabVRRotationAndFov);
        }
    });

    if (vrVidstabZoomIn) {
        vrVidstabZoomIn.addEventListener('click', () => zoomContainer(threeVidstabCamera, vrVidstabFov, -5));
    }

    if (vrVidstabZoomOut) {
        vrVidstabZoomOut.addEventListener('click', () => zoomContainer(threeVidstabCamera, vrVidstabFov, 5));
    }

    if (vrVidstabReset) {
        vrVidstabReset.addEventListener('click', () => handleResetView('vidstab'));
    }

    if (playerVidstabContainer) {
        playerVidstabContainer.addEventListener('wheel', (e) => {
            if (!isThreeVidstabInitialized || !threeVidstabCamera) return;
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeVidstabCamera, vrVidstabFov, delta);
        }, { passive: false });
    }

    videoVidstab.addEventListener('timeupdate', () => {
        updateVidstabTimeDisplay();
        if (seekbar && videoVidstab.duration) {
            seekbar.value = (videoVidstab.currentTime / videoVidstab.duration) * (seekbar.max || 100);
        }
    });
}
const initOpticalVRControls = initVidstabVRControls;
const initOpticalControls = initVidstabVRControls;

// ── Three.js 360 Cinematic Stabilized Player ───────────────────────────────
let threeCinematicScene, threeCinematicCamera, threeCinematicRenderer, threeCinematicControls, threeCinematicSphere, threeCinematicTexture;
let isThreeCinematicInitialized = false;

function initThreeCinematicJS() {
    if (isThreeCinematicInitialized || !playerCinematicContainer || !videoCinematic) return;

    const width  = playerCinematicContainer.clientWidth;
    const height = playerCinematicContainer.clientHeight;

    threeCinematicScene = new THREE.Scene();
    threeCinematicCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeCinematicCamera.target = new THREE.Vector3(0, 0, 0);

    threeCinematicRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeCinematicRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeCinematicRenderer.setSize(width, height);
    }
    playerCinematicContainer.appendChild(threeCinematicRenderer.domElement);

    threeCinematicControls = new OrbitControls(threeCinematicCamera, threeCinematicRenderer.domElement);
    threeCinematicControls.enableZoom  = false;
    threeCinematicControls.enablePan   = false;
    threeCinematicControls.rotateSpeed = -0.25;
    threeCinematicControls.addEventListener('change', () => handleControlsChange('cinematic'));

    threeCinematicCamera.position.set(0, 0, 0.1);
    threeCinematicControls.target.set(0, 0, 0);
    threeCinematicControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeCinematicTexture = new THREE.VideoTexture(videoCinematic);
    threeCinematicTexture.colorSpace = THREE.SRGBColorSpace;
    threeCinematicTexture.needsUpdate = true;
    const hasReadyCinematic = Boolean(videoCinematic && videoCinematic.getAttribute('data-loaded-src') && videoCinematic.readyState >= 2 && videoCinematic.duration > 0 && !videoCinematic.error);
    const initialCinematicMap = hasReadyCinematic ? threeCinematicTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialCinematicMap });
    threeCinematicSphere = new THREE.Mesh(geometry, material);
    threeCinematicSphere.rotation.y = -Math.PI / 2;
    threeCinematicScene.add(threeCinematicSphere);

    window.addEventListener('resize', onWindowResizeCinematic);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeCinematic();
        });
        resizeObserver.observe(playerCinematicContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    initCinematicVRControls();
    isThreeCinematicInitialized = true;
    onWindowResizeCinematic();
    animateCinematic();
}

function onWindowResizeCinematic() {
    if (!isThreeCinematicInitialized || !playerCinematicContainer) return;
    const width  = playerCinematicContainer.clientWidth;
    const height = playerCinematicContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeCinematicCamera.aspect = width / height;
        threeCinematicCamera.updateProjectionMatrix();
        threeCinematicRenderer.setSize(width, height);
    }
}

function animateCinematic() {
    if (!isThreeCinematicInitialized) return;
    requestAnimationFrame(animateCinematic);
    if (document.hidden || !playerCinematicContainer || playerCinematicContainer.clientWidth === 0 || playerCinematicContainer.clientHeight === 0) {
        return;
    }
    if (threeCinematicTexture && videoCinematic && videoCinematic.readyState >= 1) {
        hideViewportLoader('cinematic-loader');
        if (threeCinematicSphere && threeCinematicSphere.material && threeCinematicSphere.material.map !== threeCinematicTexture) {
            threeCinematicSphere.material.map = threeCinematicTexture;
            threeCinematicSphere.material.needsUpdate = true;
        }
    }
    threeCinematicControls.update();
    threeCinematicRenderer.render(threeCinematicScene, threeCinematicCamera);
}

let isCinematicVRControlsInitialized = false;
function initCinematicVRControls() {
    if (isCinematicVRControlsInitialized || !videoCinematic) return;
    isCinematicVRControlsInitialized = true;
    const playPauseBtn = document.getElementById('vr-cinematic-play-pause');
    const firstFrameBtn = document.getElementById('vr-cinematic-first-frame');
    const prevFrameBtn  = document.getElementById('vr-cinematic-prev-frame');
    const nextFrameBtn  = document.getElementById('vr-cinematic-next-frame');
    const lastFrameBtn  = document.getElementById('vr-cinematic-last-frame');
    const gotoFrameInput = document.getElementById('vr-cinematic-goto-frame');
    const yawInput      = document.getElementById('vr-cinematic-yaw');
    const pitchInput    = document.getElementById('vr-cinematic-pitch');
    const rollInput     = document.getElementById('vr-cinematic-roll');
    const fovInput      = document.getElementById('vr-cinematic-fov');
    const zoomInBtn     = document.getElementById('vr-cinematic-zoom-in');
    const zoomOutBtn    = document.getElementById('vr-cinematic-zoom-out');
    const resetViewBtn  = document.getElementById('vr-cinematic-reset-view');
    const copyLinkBtn   = document.getElementById('vr-cinematic-copy-link');
    const fullscreenBtn = document.getElementById('vr-cinematic-fullscreen');
    const realFullscreenBtn = document.getElementById('vr-cinematic-real-fullscreen');

    if (playPauseBtn) playPauseBtn.addEventListener('click', () => togglePlayVideo(videoCinematic));
    if (firstFrameBtn) firstFrameBtn.addEventListener('click', syncFirstFrame);
    if (prevFrameBtn)  prevFrameBtn.addEventListener('click', syncPrevFrame);
    if (nextFrameBtn)  nextFrameBtn.addEventListener('click', syncNextFrame);
    if (lastFrameBtn)  lastFrameBtn.addEventListener('click', syncLastFrame);

    if (videoCinematic) {
        videoCinematic.addEventListener('timeupdate', updateCinematicTimeDisplay);
        videoCinematic.addEventListener('play', updatePlayPauseButtonIcons);
        videoCinematic.addEventListener('pause', updatePlayPauseButtonIcons);
        videoCinematic.addEventListener('ended', updatePlayPauseButtonIcons);
    }

    if (gotoFrameInput) {
        gotoFrameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const targetFrame = parseInt(gotoFrameInput.value);
                const fps = currentVideoMeta.fps || 29.97;
                if (!isNaN(targetFrame) && fps > 0) {
                    syncSeekToTime(targetFrame / fps);
                }
            }
        });
    }

    function updateCinematicVRRotationAndFov() {
        updateVRPlayerOrientation('cinematic');
    }

    [yawInput, pitchInput, rollInput, fovInput].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateCinematicVRRotationAndFov);
            inp.addEventListener('change', updateCinematicVRRotationAndFov);
        }
    });

    if (resetViewBtn) {
        resetViewBtn.addEventListener('click', () => handleResetView('cinematic'));
    }

    if (zoomInBtn) {
        zoomInBtn.addEventListener('click', () => zoomContainer(threeCinematicCamera, fovInput, -5));
    }

    if (zoomOutBtn) {
        zoomOutBtn.addEventListener('click', () => zoomContainer(threeCinematicCamera, fovInput, 5));
    }

    if (playerCinematicContainer) {
        playerCinematicContainer.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeCinematicCamera, fovInput, delta);
        }, { passive: false });
    }

    if (copyLinkBtn && videoCinematic) {
        copyLinkBtn.addEventListener('click', (e) => copyVideoLink(videoCinematic, e.currentTarget));
    }

    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', togglePopupCinematic);
    }
}

// ── Three.js 360 Travel-Direction Stabilized Player ──────────────────────────
let threeTraveldirScene, threeTraveldirCamera, threeTraveldirRenderer, threeTraveldirControls, threeTraveldirSphere, threeTraveldirTexture;
let isThreeTraveldirInitialized = false;

function initThreeTraveldirJS() {
    if (isThreeTraveldirInitialized || !playerTraveldirContainer || !videoTraveldir) return;

    const width  = playerTraveldirContainer.clientWidth;
    const height = playerTraveldirContainer.clientHeight;

    threeTraveldirScene = new THREE.Scene();
    threeTraveldirCamera = new THREE.PerspectiveCamera(75, (width && height) ? width / height : 2, 1, 1100);
    threeTraveldirCamera.target = new THREE.Vector3(0, 0, 0);

    threeTraveldirRenderer = new THREE.WebGLRenderer({ antialias: true });
    threeTraveldirRenderer.setPixelRatio(window.devicePixelRatio);
    if (width > 0 && height > 0) {
        threeTraveldirRenderer.setSize(width, height);
    }
    playerTraveldirContainer.appendChild(threeTraveldirRenderer.domElement);

    threeTraveldirControls = new OrbitControls(threeTraveldirCamera, threeTraveldirRenderer.domElement);
    threeTraveldirControls.enableZoom  = false;
    threeTraveldirControls.enablePan   = false;
    threeTraveldirControls.rotateSpeed = -0.25;
    threeTraveldirControls.addEventListener('change', () => handleControlsChange('traveldir'));

    threeTraveldirCamera.position.set(0, 0, 0.1);
    threeTraveldirControls.target.set(0, 0, 0);
    threeTraveldirControls.update();

    const geometry = new THREE.SphereGeometry(500, 60, 40);
    geometry.scale(-1, 1, 1);

    threeTraveldirTexture = new THREE.VideoTexture(videoTraveldir);
    threeTraveldirTexture.colorSpace = THREE.SRGBColorSpace;
    threeTraveldirTexture.needsUpdate = true;
    const hasReadyTraveldir = Boolean(videoTraveldir && videoTraveldir.getAttribute('data-loaded-src') && videoTraveldir.readyState >= 2 && videoTraveldir.duration > 0 && !videoTraveldir.error);
    const initialTraveldirMap = hasReadyTraveldir ? threeTraveldirTexture : getGeneric360PlaceholderTexture();
    const material = new THREE.MeshBasicMaterial({ map: initialTraveldirMap });
    threeTraveldirSphere = new THREE.Mesh(geometry, material);
    threeTraveldirSphere.rotation.y = -Math.PI / 2;
    threeTraveldirScene.add(threeTraveldirSphere);

    window.addEventListener('resize', onWindowResizeTraveldir);
    try {
        const resizeObserver = new ResizeObserver(() => {
            onWindowResizeTraveldir();
        });
        resizeObserver.observe(playerTraveldirContainer);
    } catch (e) {
        console.warn("ResizeObserver not supported:", e);
    }

    initTraveldirVRControls();
    isThreeTraveldirInitialized = true;
    onWindowResizeTraveldir();
    animateTraveldir();
}

function onWindowResizeTraveldir() {
    if (!isThreeTraveldirInitialized || !playerTraveldirContainer) return;
    const width  = playerTraveldirContainer.clientWidth;
    const height = playerTraveldirContainer.clientHeight;
    if (width > 0 && height > 0) {
        threeTraveldirCamera.aspect = width / height;
        threeTraveldirCamera.updateProjectionMatrix();
        threeTraveldirRenderer.setSize(width, height);
    }
}

function animateTraveldir() {
    if (!isThreeTraveldirInitialized) return;
    requestAnimationFrame(animateTraveldir);
    if (document.hidden || !playerTraveldirContainer || playerTraveldirContainer.clientWidth === 0 || playerTraveldirContainer.clientHeight === 0) {
        return;
    }
    if (threeTraveldirTexture && videoTraveldir && videoTraveldir.readyState >= 1) {
        hideViewportLoader('traveldir-loader');
        if (threeTraveldirSphere && threeTraveldirSphere.material && threeTraveldirSphere.material.map !== threeTraveldirTexture) {
            threeTraveldirSphere.material.map = threeTraveldirTexture;
            threeTraveldirSphere.material.needsUpdate = true;
        }
    }
    threeTraveldirControls.update();
    threeTraveldirRenderer.render(threeTraveldirScene, threeTraveldirCamera);
}

let isTraveldirVRControlsInitialized = false;
function initTraveldirVRControls() {
    if (isTraveldirVRControlsInitialized || !videoTraveldir) return;
    isTraveldirVRControlsInitialized = true;
    const playPauseBtn = document.getElementById('vr-traveldir-play-pause');
    const firstFrameBtn = document.getElementById('vr-traveldir-first-frame');
    const prevFrameBtn  = document.getElementById('vr-traveldir-prev-frame');
    const nextFrameBtn  = document.getElementById('vr-traveldir-next-frame');
    const lastFrameBtn  = document.getElementById('vr-traveldir-last-frame');
    const gotoFrameInput = document.getElementById('vr-traveldir-goto-frame');
    const yawInput      = document.getElementById('vr-traveldir-yaw');
    const pitchInput    = document.getElementById('vr-traveldir-pitch');
    const rollInput     = document.getElementById('vr-traveldir-roll');
    const fovInput      = document.getElementById('vr-traveldir-fov');
    const zoomInBtn     = document.getElementById('vr-traveldir-zoom-in');
    const zoomOutBtn    = document.getElementById('vr-traveldir-zoom-out');
    const resetViewBtn  = document.getElementById('vr-traveldir-reset-view');
    const copyLinkBtn   = document.getElementById('vr-traveldir-copy-link');
    const fullscreenBtn = document.getElementById('vr-traveldir-fullscreen');
    const realFullscreenBtn = document.getElementById('vr-traveldir-real-fullscreen');

    if (playPauseBtn) playPauseBtn.addEventListener('click', () => togglePlayVideo(videoTraveldir));
    if (firstFrameBtn) firstFrameBtn.addEventListener('click', syncFirstFrame);
    if (prevFrameBtn)  prevFrameBtn.addEventListener('click', syncPrevFrame);
    if (nextFrameBtn)  nextFrameBtn.addEventListener('click', syncNextFrame);
    if (lastFrameBtn)  lastFrameBtn.addEventListener('click', syncLastFrame);

    if (videoTraveldir) {
        videoTraveldir.addEventListener('timeupdate', updateTraveldirTimeDisplay);
        videoTraveldir.addEventListener('play', updatePlayPauseButtonIcons);
        videoTraveldir.addEventListener('pause', updatePlayPauseButtonIcons);
        videoTraveldir.addEventListener('ended', updatePlayPauseButtonIcons);
    }

    if (gotoFrameInput) {
        gotoFrameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const targetFrame = parseInt(gotoFrameInput.value);
                const fps = currentVideoMeta.fps || 29.97;
                if (!isNaN(targetFrame) && fps > 0) {
                    syncSeekToTime(targetFrame / fps);
                }
            }
        });
    }

    function updateTraveldirVRRotationAndFov() {
        updateVRPlayerOrientation('traveldir');
    }

    [yawInput, pitchInput, rollInput, fovInput].forEach(inp => {
        if (inp) {
            inp.addEventListener('input', updateTraveldirVRRotationAndFov);
            inp.addEventListener('change', updateTraveldirVRRotationAndFov);
        }
    });

    if (resetViewBtn) {
        resetViewBtn.addEventListener('click', () => handleResetView('traveldir'));
    }

    if (zoomInBtn) {
        zoomInBtn.addEventListener('click', () => zoomContainer(threeTraveldirCamera, fovInput, -5));
    }

    if (zoomOutBtn) {
        zoomOutBtn.addEventListener('click', () => zoomContainer(threeTraveldirCamera, fovInput, 5));
    }

    if (playerTraveldirContainer) {
        playerTraveldirContainer.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? 5 : -5;
            zoomContainer(threeTraveldirCamera, fovInput, delta);
        }, { passive: false });
    }

    if (copyLinkBtn && videoTraveldir) {
        copyLinkBtn.addEventListener('click', (e) => copyVideoLink(videoTraveldir, e.currentTarget));
    }

    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', togglePopupTraveldir);
    }
}

function updateCroppedOutputName() {
    if (!croppedOutputName || !options.input) return;
    const videoFile = options.input.value;
    if (!videoFile) return;
    const rawName = videoFile.replace(/^(data\/input\/videos\/|ready\/)/, '');
    const dotIndex = rawName.lastIndexOf('.');
    const base = dotIndex === -1 ? rawName : rawName.substring(0, dotIndex);
    const ext = dotIndex === -1 ? '.mp4' : rawName.substring(dotIndex);
    const cleanBase = base.replace(/(_\d+_\d+|_cropped)$/i, '');
    const startF = parseInt(trimStartFrame?.value) || 0;
    const endF = parseInt(trimEndFrame?.value) || 0;
    croppedOutputName.value = `data/input/videos/${cleanBase}_${startF}_${endF}${ext}`;
}

function updateTrimTimeLabels() {
    const startF = parseInt(trimStartFrame.value) || 0;
    const endF   = parseInt(trimEndFrame.value) || 0;
    const fps    = currentVideoMeta.fps || 30;

    const startSec = startF / fps;
    const endSec   = endF / fps;

    if (trimStartTimeLbl) trimStartTimeLbl.textContent = `${startSec.toFixed(2)}s (${formatTimeHHMMSS(startSec)})`;
    if (trimEndTimeLbl)   trimEndTimeLbl.textContent = `${endSec.toFixed(2)}s (${formatTimeHHMMSS(endSec)})`;
    updateCroppedOutputName();
}

function isAnyPopupOpen() {
    const popups = [
        popup360, popupStitched, popupOriginal,
        popupPreview, popupTelemetry, popupKopf, popupKabsch, popupVidstab,
        popupCinematic, popupHorizon, popupTraveldir
    ];
    return popups.some(p => p && p.style.display !== 'none');
}

function updateSyncButtonIcons(isPlaying) {
    const text = isPlaying ? '&#10074;&#10074; Sync All' : '&#9654; Sync All';
    const syncBtnIds = [
        'btn-sync-play-stitched', 'btn-sync-play-360', 'btn-sync-play-original',
        'btn-sync-play-telemetry', 'btn-sync-play-optical',
        'btn-sync-play-kabsch', 'btn-sync-play-kopf', 'btn-sync-play-vidstab',
        'btn-sync-play-cinematic', 'btn-sync-play-horizon', 'btn-sync-play-traveldir',
        'btn-sync-play-checkpoints'
    ];
    syncBtnIds.forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.innerHTML = text;
    });
}

function updatePlayPauseButtonIcons() {
    if (vrPlayPause)         vrPlayPause.innerHTML         = (video360 && !video360.paused) ? '&#10074;&#10074;' : '&#9654;';
    if (vrStitchedPlayPause) vrStitchedPlayPause.innerHTML = (videoStitched && !videoStitched.paused) ? '&#10074;&#10074;' : '&#9654;';
    if (origPlayPause)       origPlayPause.innerHTML       = (originalVideo && !originalVideo.paused) ? '&#10074;&#10074;' : '&#9654;';

    const telBtn = document.getElementById('vr-telemetry-play-pause');
    if (telBtn) telBtn.innerHTML = (videoTelemetry && !videoTelemetry.paused) ? '&#10074;&#10074;' : '&#9654;';

    const kopfBtn = document.getElementById('vr-kopf-play-pause');
    if (kopfBtn) kopfBtn.innerHTML = (videoKopf && !videoKopf.paused) ? '&#10074;&#10074;' : '&#9654;';

    const kabBtn = document.getElementById('vr-kabsch-play-pause');
    if (kabBtn) kabBtn.innerHTML = (videoKabsch && !videoKabsch.paused) ? '&#10074;&#10074;' : '&#9654;';

    const vidBtn = document.getElementById('vr-vidstab-play-pause');
    if (vidBtn) vidBtn.innerHTML = (videoVidstab && !videoVidstab.paused) ? '&#10074;&#10074;' : '&#9654;';

    const l1Btn = document.getElementById('vr-cinematic-play-pause');
    if (l1Btn) l1Btn.innerHTML = (videoCinematic && !videoCinematic.paused) ? '&#10074;&#10074;' : '&#9654;';

    const hzBtn = document.getElementById('vr-horizon-play-pause');
    if (hzBtn) hzBtn.innerHTML = (videoHorizon && !videoHorizon.paused) ? '&#10074;&#10074;' : '&#9654;';

    const dlBtn = document.getElementById('vr-traveldir-play-pause');
    if (dlBtn) dlBtn.innerHTML = (videoTraveldir && !videoTraveldir.paused) ? '&#10074;&#10074;' : '&#9654;';

    const allVids = [video360, videoStitched, videoTelemetry, videoKopf, videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, originalVideo];
    const isAnyPlaying = allVids.some(v => v && !v.paused && !v.ended);
    updateSyncButtonIcons(isAnyPlaying);
}

function togglePlayVideo(targetVideo) {
    if (!targetVideo) return;
    if (!targetVideo.src && !targetVideo.currentSrc) {
        console.warn("No video source loaded for target video element:", targetVideo.id);
        return;
    }
    const allVideos = [
        video360, videoStitched, videoTelemetry, videoKopf,
        videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, originalVideo
    ];
    if (targetVideo.paused) {
        allVideos.forEach(v => {
            if (v && v !== targetVideo && !v.paused) {
                v.pause();
            }
        });
        targetVideo.play().then(() => {
            updatePlayPauseButtonIcons();
        }).catch(err => {
            console.error("Video play failed:", err);
        });
    } else {
        targetVideo.pause();
        updatePlayPauseButtonIcons();
    }
}

function togglePlayOrig() {
    if (!originalVideo) return;
    if (!originalVideo.src && !originalVideo.currentSrc) {
        if (options.input && options.input.value) {
            originalVideo.src = resolveVideoSrc(options.input.value);
            originalVideo.load();
        }
    }
    togglePlayVideo(originalVideo);
}

function updateHorizonTimeDisplay() {
    if (!videoHorizon) return;
    const cur = isFinite(videoHorizon.currentTime) ? videoHorizon.currentTime : 0;
    const dur = isFinite(videoHorizon.duration) ? videoHorizon.duration : 0;
    const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 29.97;
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    const el = document.getElementById('vr-horizon-time-display');
    if (el) el.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    updateGotoFrameInputValue('vr-horizon-goto-frame', curF);
    updateGotoTimeInputValue('vr-horizon-goto-time', cur);
}
const updateCheckpointsTimeDisplay = updateHorizonTimeDisplay;

function updateTraveldirTimeDisplay() {
    if (!videoTraveldir) return;
    const cur = isFinite(videoTraveldir.currentTime) ? videoTraveldir.currentTime : 0;
    const dur = isFinite(videoTraveldir.duration) ? videoTraveldir.duration : 0;
    const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 29.97;
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    const el = document.getElementById('vr-traveldir-time-display');
    if (el) el.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    updateGotoFrameInputValue('vr-traveldir-goto-frame', curF);
    updateGotoTimeInputValue('vr-traveldir-goto-time', cur);
}

function updateOrigTimeDisplay() {
    if (!originalVideo) return;
    const cur = isFinite(originalVideo.currentTime) ? originalVideo.currentTime : 0;
    const dur = isFinite(originalVideo.duration) ? originalVideo.duration : (currentVideoMeta.duration || 0);
    const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 30.0;
    
    const curF = Math.round(cur * fps);
    const durF = currentVideoMeta.total_frames || Math.round(dur * fps);
    
    if (origTimeDisplay) {
        origTimeDisplay.textContent = `${formatTime(cur)} / ${formatTime(dur)} (Frame ${curF} / ${durF})`;
    }
    updateGotoFrameInputValue('orig-goto-frame', curF);
    updateGotoTimeInputValue('orig-goto-time', cur);
}

function toggleSyncPlay() {
    const vids = [video360, videoStitched, videoTelemetry, videoKopf, videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, originalVideo].filter(v => v && (v.src || v.currentSrc) && v.readyState >= 1);
    if (vids.length === 0) return;

    const isAnyPlaying = vids.some(v => !v.paused && !v.ended);

    if (isAnyPlaying) {
        vids.forEach(v => v.pause());
        updatePlayPauseButtonIcons();
    } else {
        const primary = vids.find(v => v.currentTime > 0) || vids[0];
        const targetTime = primary ? primary.currentTime : 0;

        vids.forEach(v => {
            v.loop = true;
            v.currentTime = targetTime;
        });

        Promise.all(vids.map(v => v.play().catch(e => console.warn(e)))).then(() => {
            updatePlayPauseButtonIcons();
        });
    }
}

function syncFirstFrame() {
    const activeTabBtn = document.querySelector('.viewport-tabs .tab-btn.active');
    const activeTabId = activeTabBtn ? activeTabBtn.id : '';
    const activeVid = getActiveTabVideo(activeTabId);
    if (activeVid) {
        activeVid.pause();
        activeVid.currentTime = 0;
    } else {
        const vids = [video360, videoStitched, videoTelemetry, videoKopf, videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, originalVideo].filter(v => v);
        vids.forEach(v => { v.pause(); v.currentTime = 0; });
    }
    updateVRTimeDisplay();
    updateVRStitchedTimeDisplay();
    updateTelemetryTimeDisplay();
    updateKopfTimeDisplay();
    updateKabschTimeDisplay();
    updateOpticalTimeDisplay();
    updateCheckpointsTimeDisplay();
    updateOrigTimeDisplay();
    updateVidstabTimeDisplay();
    updateCinematicTimeDisplay();
    updateHorizonTimeDisplay();
    updateTraveldirTimeDisplay();
    updatePlayPauseButtonIcons();
}

function syncPrevFrame() {
    const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 30.0;
    const activeTabBtn = document.querySelector('.viewport-tabs .tab-btn.active');
    const activeTabId = activeTabBtn ? activeTabBtn.id : '';
    const activeVid = getActiveTabVideo(activeTabId);
    const fallbackVid = [video360, videoStitched, videoTelemetry, videoKopf, videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, originalVideo].find(v => v && (v.src || v.currentSrc) && isFinite(v.currentTime) && v.currentTime > 0);
    const targetVid = activeVid || fallbackVid;
    const cur = targetVid && isFinite(targetVid.currentTime) ? targetVid.currentTime : 0;
    const target = Math.max(0, cur - (1.0 / fps));
    if (isFinite(target)) {
        if (activeVid) {
            activeVid.pause();
            activeVid.currentTime = target;
        } else if (targetVid) {
            targetVid.pause();
            targetVid.currentTime = target;
        }
    }
    updateVRTimeDisplay();
    updateVRStitchedTimeDisplay();
    updateTelemetryTimeDisplay();
    updateKopfTimeDisplay();
    updateKabschTimeDisplay();
    updateOpticalTimeDisplay();
    updateCheckpointsTimeDisplay();
    updateOrigTimeDisplay();
    updateVidstabTimeDisplay();
    updateCinematicTimeDisplay();
    updateHorizonTimeDisplay();
    updateTraveldirTimeDisplay();
    updatePlayPauseButtonIcons();
}

function syncNextFrame() {
    const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 30.0;
    const activeTabBtn = document.querySelector('.viewport-tabs .tab-btn.active');
    const activeTabId = activeTabBtn ? activeTabBtn.id : '';
    const activeVid = getActiveTabVideo(activeTabId);
    const fallbackVid = [video360, videoStitched, videoTelemetry, videoKopf, videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, originalVideo].find(v => v && (v.src || v.currentSrc) && isFinite(v.currentTime) && v.currentTime > 0);
    const targetVid = activeVid || fallbackVid;
    const cur = targetVid && isFinite(targetVid.currentTime) ? targetVid.currentTime : 0;
    const dur = targetVid && isFinite(targetVid.duration) && targetVid.duration > 0 ? targetVid.duration : (currentVideoMeta.duration || 0);
    const target = dur > 0 ? Math.min(dur, cur + (1.0 / fps)) : cur + (1.0 / fps);
    if (isFinite(target)) {
        if (activeVid) {
            activeVid.pause();
            activeVid.currentTime = target;
        } else if (targetVid) {
            targetVid.pause();
            targetVid.currentTime = target;
        }
    }
    updateVRTimeDisplay();
    updateVRStitchedTimeDisplay();
    updateTelemetryTimeDisplay();
    updateKopfTimeDisplay();
    updateKabschTimeDisplay();
    updateOpticalTimeDisplay();
    updateCheckpointsTimeDisplay();
    updateOrigTimeDisplay();
    updateVidstabTimeDisplay();
    updateCinematicTimeDisplay();
    updateHorizonTimeDisplay();
    updateTraveldirTimeDisplay();
    updatePlayPauseButtonIcons();
}

function syncLastFrame() {
    const activeTabBtn = document.querySelector('.viewport-tabs .tab-btn.active');
    const activeTabId = activeTabBtn ? activeTabBtn.id : '';
    const activeVid = getActiveTabVideo(activeTabId);
    const fps = isFinite(currentVideoMeta.fps) && currentVideoMeta.fps > 0 ? currentVideoMeta.fps : 30.0;
    const dur = activeVid && isFinite(activeVid.duration) && activeVid.duration > 0 ? activeVid.duration : (currentVideoMeta.duration || 0);
    if (dur > 0) {
        const target = Math.max(0, dur - (1.0 / fps));
        if (isFinite(target)) {
            if (activeVid) {
                activeVid.pause();
                activeVid.currentTime = target;
            }
        }
    }
    updateVRTimeDisplay();
    updateVRStitchedTimeDisplay();
    updateTelemetryTimeDisplay();
    updateKabschTimeDisplay();
    updateKopfTimeDisplay();
    updateOpticalTimeDisplay();
    updateCheckpointsTimeDisplay();
    updateOrigTimeDisplay();
    updateCinematicTimeDisplay();
    updateTraveldirTimeDisplay();
    updatePlayPauseButtonIcons();
}

function syncSeekToTime(targetTime) {
    if (!isFinite(targetTime)) return;
    const vids = [video360, videoStitched, videoTelemetry, videoKopf, videoKabsch, videoVidstab, videoCinematic, videoHorizon, videoTraveldir, originalVideo].filter(v => v);
    vids.forEach(v => { v.currentTime = targetTime; });
    updateVRTimeDisplay();
    updateVRStitchedTimeDisplay();
    updateTelemetryTimeDisplay();
    updateKopfTimeDisplay();
    updateKabschTimeDisplay();
    updateVidstabTimeDisplay();
    updateCinematicTimeDisplay();
    updateHorizonTimeDisplay();
    updateTraveldirTimeDisplay();
    updateOrigTimeDisplay();
}

let isSyncingRotation = false;

function getVRPlayer(key) {
    if (key === '360') {
        return {
            name: '360',
            cam: threeCamera,
            ctrl: threeControls,
            sphere: threeSphere,
            yawEl: document.getElementById('vr-yaw'),
            pitchEl: document.getElementById('vr-pitch'),
            rollEl: document.getElementById('vr-roll'),
            fovEl: document.getElementById('vr-fov')
        };
    } else if (key === 'stitched') {
        return {
            name: 'stitched',
            cam: threeStitchedCamera,
            ctrl: threeStitchedControls,
            sphere: threeStitchedSphere,
            yawEl: document.getElementById('vr-stitched-yaw'),
            pitchEl: document.getElementById('vr-stitched-pitch'),
            rollEl: document.getElementById('vr-stitched-roll'),
            fovEl: document.getElementById('vr-stitched-fov')
        };
    } else if (key === 'telemetry') {
        return {
            name: 'telemetry',
            cam: threeTelemetryCamera,
            ctrl: threeTelemetryControls,
            sphere: threeTelemetrySphere,
            yawEl: document.getElementById('vr-telemetry-yaw'),
            pitchEl: document.getElementById('vr-telemetry-pitch'),
            rollEl: document.getElementById('vr-telemetry-roll'),
            fovEl: document.getElementById('vr-telemetry-fov')
        };
    } else if (key === 'kabsch') {
        return {
            name: 'kabsch',
            cam: threeKabschCamera,
            ctrl: threeKabschControls,
            sphere: threeKabschSphere,
            yawEl: document.getElementById('vr-kabsch-yaw'),
            pitchEl: document.getElementById('vr-kabsch-pitch'),
            rollEl: document.getElementById('vr-kabsch-roll'),
            fovEl: document.getElementById('vr-kabsch-fov')
        };
    } else if (key === 'kopf') {
        return {
            name: 'kopf',
            cam: threeKopfCamera,
            ctrl: threeKopfControls,
            sphere: threeKopfSphere,
            yawEl: document.getElementById('vr-kopf-yaw'),
            pitchEl: document.getElementById('vr-kopf-pitch'),
            rollEl: document.getElementById('vr-kopf-roll'),
            fovEl: document.getElementById('vr-kopf-fov')
        };
    } else if (key === 'vidstab' || key === 'optical') {
        return {
            name: 'vidstab',
            cam: threeVidstabCamera,
            ctrl: threeVidstabControls,
            sphere: threeVidstabSphere,
            yawEl: document.getElementById('vr-vidstab-yaw'),
            pitchEl: document.getElementById('vr-vidstab-pitch'),
            rollEl: document.getElementById('vr-vidstab-roll'),
            fovEl: document.getElementById('vr-vidstab-fov')
        };
    } else if (key === 'cinematic') {
        return {
            name: 'cinematic',
            cam: threeCinematicCamera,
            ctrl: threeCinematicControls,
            sphere: threeCinematicSphere,
            yawEl: document.getElementById('vr-cinematic-yaw'),
            pitchEl: document.getElementById('vr-cinematic-pitch'),
            rollEl: document.getElementById('vr-cinematic-roll'),
            fovEl: document.getElementById('vr-cinematic-fov')
        };
    } else if (key === 'horizon' || key === 'checkpoints') {
        return {
            name: 'horizon',
            cam: threeHorizonCamera,
            ctrl: threeHorizonControls,
            sphere: threeHorizonSphere,
            yawEl: document.getElementById('vr-horizon-yaw'),
            pitchEl: document.getElementById('vr-horizon-pitch'),
            rollEl: document.getElementById('vr-horizon-roll'),
            fovEl: document.getElementById('vr-horizon-fov')
        };
    } else if (key === 'traveldir') {
        return {
            name: 'traveldir',
            cam: threeTraveldirCamera,
            ctrl: threeTraveldirControls,
            sphere: threeTraveldirSphere,
            yawEl: document.getElementById('vr-traveldir-yaw'),
            pitchEl: document.getElementById('vr-traveldir-pitch'),
            rollEl: document.getElementById('vr-traveldir-roll'),
            fovEl: document.getElementById('vr-traveldir-fov')
        };
    } else if (key === 'orig' || key === 'original') {
        return {
            name: 'orig',
            cam: threeOrigCamera,
            ctrl: threeOrigControls,
            sphere: threeOrigSphere,
            yawEl: document.getElementById('vr-orig-yaw'),
            pitchEl: document.getElementById('vr-orig-pitch'),
            rollEl: document.getElementById('vr-orig-roll'),
            fovEl: document.getElementById('vr-orig-fov')
        };
    }
    return null;
}

function getVRPlayersList() {
    return ['360', 'stitched', 'telemetry', 'kabsch', 'kopf', 'vidstab', 'cinematic', 'horizon', 'traveldir', 'orig'].map(getVRPlayer).filter(Boolean);
}

function getAnglesFromControls(controls) {
    if (!controls) return { yaw: 0, pitch: 0 };
    const theta = controls.getAzimuthalAngle();
    const phi = controls.getPolarAngle();

    let rawYaw = -THREE.MathUtils.radToDeg(theta);
    let yaw = ((rawYaw + 180) % 360 + 360) % 360 - 180;
    yaw = Math.round(yaw) === 0 ? 0 : Math.round(yaw);

    let rawPitch = THREE.MathUtils.radToDeg(phi - Math.PI / 2);
    let pitch = Math.round(Math.max(-90, Math.min(90, rawPitch)));
    pitch = pitch === 0 ? 0 : pitch;

    return { yaw, pitch };
}

function setCameraOrientation(camera, controls, sphere, yawDeg, pitchDeg, rollDeg) {
    if (!camera || !controls || !sphere) return;
    const r = 0.1;
    const phi = Math.PI / 2 + THREE.MathUtils.degToRad(pitchDeg);
    const theta = -THREE.MathUtils.degToRad(yawDeg);

    camera.position.set(
        r * Math.sin(phi) * Math.sin(theta),
        r * Math.cos(phi),
        r * Math.sin(phi) * Math.cos(theta)
    );
    controls.target.set(0, 0, 0);
    camera.lookAt(0, 0, 0);
    controls.update();

    sphere.rotation.set(0, -Math.PI / 2, THREE.MathUtils.degToRad(rollDeg), 'YXZ');
}

function handleControlsChange(playerKey) {
    if (isSyncingRotation) return;
    const sourcePlayer = getVRPlayer(playerKey);
    if (!sourcePlayer || !sourcePlayer.ctrl) return;

    const { yaw, pitch } = getAnglesFromControls(sourcePlayer.ctrl);
    const roll = parseFloat(sourcePlayer.rollEl?.value || 0) || 0;

    if (sourcePlayer.yawEl && document.activeElement !== sourcePlayer.yawEl) {
        sourcePlayer.yawEl.value = yaw;
    }
    if (sourcePlayer.pitchEl && document.activeElement !== sourcePlayer.pitchEl) {
        sourcePlayer.pitchEl.value = pitch;
    }
    if (sourcePlayer.rollEl && document.activeElement !== sourcePlayer.rollEl && !sourcePlayer.rollEl.value) {
        sourcePlayer.rollEl.value = 0;
    }

    if (isAnyPopupOpen()) {
        isSyncingRotation = true;
        try {
            const players = getVRPlayersList();
            players.forEach(p => {
                if (p.name !== playerKey && p.cam && p.ctrl && p.sphere) {
                    setCameraOrientation(p.cam, p.ctrl, p.sphere, yaw, pitch, roll);
                    if (p.yawEl && document.activeElement !== p.yawEl) p.yawEl.value = yaw;
                    if (p.pitchEl && document.activeElement !== p.pitchEl) p.pitchEl.value = pitch;
                    if (p.rollEl && document.activeElement !== p.rollEl) p.rollEl.value = roll;
                }
            });
        } finally {
            isSyncingRotation = false;
        }
    }
}

function updateVRPlayerOrientation(playerKey) {
    if (isSyncingRotation) return;
    const player = getVRPlayer(playerKey);
    if (!player || !player.cam || !player.ctrl || !player.sphere) return;

    const yaw = parseFloat(player.yawEl?.value || 0) || 0;
    const pitch = parseFloat(player.pitchEl?.value || 0) || 0;
    const roll = parseFloat(player.rollEl?.value || 0) || 0;
    const fov = parseFloat(player.fovEl?.value || 75) || 75;

    isSyncingRotation = true;
    try {
        setCameraOrientation(player.cam, player.ctrl, player.sphere, yaw, pitch, roll);
        player.cam.fov = Math.max(1, Math.min(175, fov));
        player.cam.updateProjectionMatrix();

        if (isAnyPopupOpen()) {
            const players = getVRPlayersList();
            players.forEach(p => {
                if (p.name !== playerKey && p.cam && p.ctrl && p.sphere) {
                    setCameraOrientation(p.cam, p.ctrl, p.sphere, yaw, pitch, roll);
                    p.cam.fov = Math.max(1, Math.min(175, fov));
                    p.cam.updateProjectionMatrix();
                    if (p.yawEl && document.activeElement !== p.yawEl) p.yawEl.value = yaw;
                    if (p.pitchEl && document.activeElement !== p.pitchEl) p.pitchEl.value = pitch;
                    if (p.rollEl && document.activeElement !== p.rollEl) p.rollEl.value = roll;
                    if (p.fovEl && document.activeElement !== p.fovEl) p.fovEl.value = fov;
                }
            });
        }
    } finally {
        isSyncingRotation = false;
    }
}

function resetView360() {
    const vrYaw = document.getElementById('vr-yaw');
    const vrPitch = document.getElementById('vr-pitch');
    const vrRoll = document.getElementById('vr-roll');
    const vrFov = document.getElementById('vr-fov');
    if (vrYaw) vrYaw.value = 0;
    if (vrPitch) vrPitch.value = 0;
    if (vrRoll) vrRoll.value = 0;
    if (vrFov) vrFov.value = 75;
    if (isThreeInitialized && threeControls && threeCamera && threeSphere) {
        threeControls.reset();
        threeCamera.position.set(0, 0, 0.1);
        threeCamera.fov = 75;
        threeCamera.updateProjectionMatrix();
        threeSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeControls.update();
    }
}

function resetViewStitched() {
    const vrStitchedYaw = document.getElementById('vr-stitched-yaw');
    const vrStitchedPitch = document.getElementById('vr-stitched-pitch');
    const vrStitchedRoll = document.getElementById('vr-stitched-roll');
    const vrStitchedFov = document.getElementById('vr-stitched-fov');
    if (vrStitchedYaw) vrStitchedYaw.value = 0;
    if (vrStitchedPitch) vrStitchedPitch.value = 0;
    if (vrStitchedRoll) vrStitchedRoll.value = 0;
    if (vrStitchedFov) vrStitchedFov.value = 75;
    if (isThreeStitchedInitialized && threeStitchedControls && threeStitchedCamera && threeStitchedSphere) {
        threeStitchedControls.reset();
        threeStitchedCamera.position.set(0, 0, 0.1);
        threeStitchedCamera.fov = 75;
        threeStitchedCamera.updateProjectionMatrix();
        threeStitchedSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeStitchedControls.update();
    }
}

function resetViewTelemetry() {
    const vrTelemetryYaw = document.getElementById('vr-telemetry-yaw');
    const vrTelemetryPitch = document.getElementById('vr-telemetry-pitch');
    const vrTelemetryRoll = document.getElementById('vr-telemetry-roll');
    const vrTelemetryFov = document.getElementById('vr-telemetry-fov');
    if (vrTelemetryYaw) vrTelemetryYaw.value = 0;
    if (vrTelemetryPitch) vrTelemetryPitch.value = 0;
    if (vrTelemetryRoll) vrTelemetryRoll.value = 0;
    if (vrTelemetryFov) vrTelemetryFov.value = 75;
    if (isThreeTelemetryInitialized && threeTelemetryControls && threeTelemetryCamera && threeTelemetrySphere) {
        threeTelemetryControls.reset();
        threeTelemetryCamera.position.set(0, 0, 0.1);
        threeTelemetryCamera.fov = 75;
        threeTelemetryCamera.updateProjectionMatrix();
        threeTelemetrySphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeTelemetryControls.update();
    }
}

function resetViewKabsch() {
    const vrKabschYaw = document.getElementById('vr-kabsch-yaw');
    const vrKabschPitch = document.getElementById('vr-kabsch-pitch');
    const vrKabschRoll = document.getElementById('vr-kabsch-roll');
    const vrKabschFov = document.getElementById('vr-kabsch-fov');
    if (vrKabschYaw) vrKabschYaw.value = 0;
    if (vrKabschPitch) vrKabschPitch.value = 0;
    if (vrKabschRoll) vrKabschRoll.value = 0;
    if (vrKabschFov) vrKabschFov.value = 75;
    if (isThreeKabschInitialized && threeKabschControls && threeKabschCamera && threeKabschSphere) {
        threeKabschControls.reset();
        threeKabschCamera.position.set(0, 0, 0.1);
        threeKabschCamera.fov = 75;
        threeKabschCamera.updateProjectionMatrix();
        threeKabschSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeKabschControls.update();
    }
}

function resetViewKopf() {
    const vrKopfYaw = document.getElementById('vr-kopf-yaw');
    const vrKopfPitch = document.getElementById('vr-kopf-pitch');
    const vrKopfRoll = document.getElementById('vr-kopf-roll');
    const vrKopfFov = document.getElementById('vr-kopf-fov');
    if (vrKopfYaw) vrKopfYaw.value = 0;
    if (vrKopfPitch) vrKopfPitch.value = 0;
    if (vrKopfRoll) vrKopfRoll.value = 0;
    if (vrKopfFov) vrKopfFov.value = 75;
    if (isThreeKopfInitialized && threeKopfControls && threeKopfCamera && threeKopfSphere) {
        threeKopfControls.reset();
        threeKopfCamera.position.set(0, 0, 0.1);
        threeKopfCamera.fov = 75;
        threeKopfCamera.updateProjectionMatrix();
        threeKopfSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeKopfControls.update();
    }
}

function resetViewVidstab() {
    const vrVidstabYaw = document.getElementById('vr-vidstab-yaw');
    const vrVidstabPitch = document.getElementById('vr-vidstab-pitch');
    const vrVidstabRoll = document.getElementById('vr-vidstab-roll');
    const vrVidstabFov = document.getElementById('vr-vidstab-fov');
    if (vrVidstabYaw) vrVidstabYaw.value = 0;
    if (vrVidstabPitch) vrVidstabPitch.value = 0;
    if (vrVidstabRoll) vrVidstabRoll.value = 0;
    if (vrVidstabFov) vrVidstabFov.value = 75;
    if (isThreeVidstabInitialized && threeVidstabControls && threeVidstabCamera && threeVidstabSphere) {
        threeVidstabControls.reset();
        threeVidstabCamera.position.set(0, 0, 0.1);
        threeVidstabCamera.fov = 75;
        threeVidstabCamera.updateProjectionMatrix();
        threeVidstabSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeVidstabControls.update();
    }
}
const resetViewOptical = resetViewVidstab;

function resetViewCinematic() {
    const vrCinematicYaw = document.getElementById('vr-cinematic-yaw');
    const vrCinematicPitch = document.getElementById('vr-cinematic-pitch');
    const vrCinematicRoll = document.getElementById('vr-cinematic-roll');
    const vrCinematicFov = document.getElementById('vr-cinematic-fov');
    if (vrCinematicYaw) vrCinematicYaw.value = 0;
    if (vrCinematicPitch) vrCinematicPitch.value = 0;
    if (vrCinematicRoll) vrCinematicRoll.value = 0;
    if (vrCinematicFov) vrCinematicFov.value = 75;
    if (isThreeCinematicInitialized && threeCinematicControls && threeCinematicCamera && threeCinematicSphere) {
        threeCinematicControls.reset();
        threeCinematicCamera.position.set(0, 0, 0.1);
        threeCinematicCamera.fov = 75;
        threeCinematicCamera.updateProjectionMatrix();
        threeCinematicSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeCinematicControls.update();
    }
}

function resetViewHorizon() {
    const vrHorizonYaw = document.getElementById('vr-horizon-yaw');
    const vrHorizonPitch = document.getElementById('vr-horizon-pitch');
    const vrHorizonRoll = document.getElementById('vr-horizon-roll');
    const vrHorizonFov = document.getElementById('vr-horizon-fov');
    if (vrHorizonYaw) vrHorizonYaw.value = 0;
    if (vrHorizonPitch) vrHorizonPitch.value = 0;
    if (vrHorizonRoll) vrHorizonRoll.value = 0;
    if (vrHorizonFov) vrHorizonFov.value = 75;
    if (isThreeHorizonInitialized && threeHorizonControls && threeHorizonCamera && threeHorizonSphere) {
        threeHorizonControls.reset();
        threeHorizonCamera.position.set(0, 0, 0.1);
        threeHorizonCamera.fov = 75;
        threeHorizonCamera.updateProjectionMatrix();
        threeHorizonSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeHorizonControls.update();
    }
}
const resetViewCheckpoints = resetViewHorizon;

function resetViewTraveldir() {
    const vrTraveldirYaw = document.getElementById('vr-traveldir-yaw');
    const vrTraveldirPitch = document.getElementById('vr-traveldir-pitch');
    const vrTraveldirRoll = document.getElementById('vr-traveldir-roll');
    const vrTraveldirFov = document.getElementById('vr-traveldir-fov');
    if (vrTraveldirYaw) vrTraveldirYaw.value = 0;
    if (vrTraveldirPitch) vrTraveldirPitch.value = 0;
    if (vrTraveldirRoll) vrTraveldirRoll.value = 0;
    if (vrTraveldirFov) vrTraveldirFov.value = 75;
    if (isThreeTraveldirInitialized && threeTraveldirControls && threeTraveldirCamera && threeTraveldirSphere) {
        threeTraveldirControls.reset();
        threeTraveldirCamera.position.set(0, 0, 0.1);
        threeTraveldirCamera.fov = 75;
        threeTraveldirCamera.updateProjectionMatrix();
        threeTraveldirSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeTraveldirControls.update();
    }
}

function resetViewOrig() {
    const vrOrigYaw = document.getElementById('vr-orig-yaw');
    const vrOrigPitch = document.getElementById('vr-orig-pitch');
    const vrOrigRoll = document.getElementById('vr-orig-roll');
    const vrOrigFov = document.getElementById('vr-orig-fov');
    if (vrOrigYaw) vrOrigYaw.value = 0;
    if (vrOrigPitch) vrOrigPitch.value = 0;
    if (vrOrigRoll) vrOrigRoll.value = 0;
    if (vrOrigFov) vrOrigFov.value = 75;
    if (isThreeOrigInitialized && threeOrigControls && threeOrigCamera && threeOrigSphere) {
        threeOrigControls.reset();
        threeOrigCamera.position.set(0, 0, 0.1);
        threeOrigCamera.fov = 75;
        threeOrigCamera.updateProjectionMatrix();
        threeOrigSphere.rotation.set(0, -Math.PI / 2, 0, 'YXZ');
        threeOrigControls.update();
    }
}

function syncResetView() {
    resetView360();
    resetViewStitched();
    resetViewTelemetry();
    resetViewKabsch();
    resetViewKopf();
    resetViewVidstab();
    resetViewCinematic();
    resetViewHorizon();
    resetViewTraveldir();
    resetViewOrig();
}

function handleResetView(targetKey) {
    if (isAnyPopupOpen()) {
        syncResetView();
    } else {
        if (targetKey === '360') resetView360();
        else if (targetKey === 'stitched') resetViewStitched();
        else if (targetKey === 'telemetry') resetViewTelemetry();
        else if (targetKey === 'kabsch') resetViewKabsch();
        else if (targetKey === 'kopf') resetViewKopf();
        else if (targetKey === 'vidstab' || targetKey === 'optical') resetViewVidstab();
        else if (targetKey === 'cinematic') resetViewCinematic();
        else if (targetKey === 'horizon' || targetKey === 'checkpoints') resetViewHorizon();
        else if (targetKey === 'traveldir') resetViewTraveldir();
        else if (targetKey === 'orig' || targetKey === 'original') resetViewOrig();
    }
}

function syncZoom(delta) {
    const list = [
        { cam: threeCamera, fovEl: document.getElementById('vr-fov') },
        { cam: threeStitchedCamera, fovEl: document.getElementById('vr-stitched-fov') },
        { cam: threeTelemetryCamera, fovEl: document.getElementById('vr-telemetry-fov') },
        { cam: threeKabschCamera, fovEl: document.getElementById('vr-kabsch-fov') },
        { cam: threeKopfCamera, fovEl: document.getElementById('vr-kopf-fov') },
        { cam: threeVidstabCamera, fovEl: document.getElementById('vr-vidstab-fov') },
        { cam: threeCinematicCamera, fovEl: document.getElementById('vr-cinematic-fov') },
        { cam: threeHorizonCamera, fovEl: document.getElementById('vr-horizon-fov') },
        { cam: threeTraveldirCamera, fovEl: document.getElementById('vr-traveldir-fov') },
        { cam: threeOrigCamera, fovEl: document.getElementById('vr-orig-fov') }
    ];
    list.forEach(item => {
        if (item.cam) {
            item.cam.fov += delta;
            item.cam.fov = Math.max(1, Math.min(175, item.cam.fov));
            item.cam.updateProjectionMatrix();
            if (item.fovEl) item.fovEl.value = Math.round(item.cam.fov);
        }
    });
}

function zoomContainer(camera, fovEl, delta) {
    if (isAnyPopupOpen()) {
        syncZoom(delta);
    } else {
        if (!camera) return;
        camera.fov += delta;
        camera.fov = Math.max(1, Math.min(175, camera.fov));
        camera.updateProjectionMatrix();
        if (fovEl) fovEl.value = Math.round(camera.fov);
    }
}

function formatTimeHMS(secs) {
    if (secs === null || secs === undefined || isNaN(secs) || secs < 0) return '00:00:00';
    const totalSecs = Math.floor(secs);
    const h = Math.floor(totalSecs / 3600);
    const m = Math.floor((totalSecs % 3600) / 60);
    const s = Math.floor(totalSecs % 60);
    const pad = (num) => String(num).padStart(2, '0');
    return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function parseTimeHMS(str) {
    if (!str || typeof str !== 'string') return null;
    const parts = str.trim().split(':').map(p => parseFloat(p.trim()));
    if (parts.length === 0 || parts.some(isNaN)) return null;
    if (parts.length === 3) {
        return Math.max(0, parts[0] * 3600 + parts[1] * 60 + parts[2]);
    } else if (parts.length === 2) {
        return Math.max(0, parts[0] * 60 + parts[1]);
    } else if (parts.length === 1) {
        return Math.max(0, parts[0]);
    }
    return null;
}

function updateGotoFrameInputValue(inputId, curFrame) {
    const el = document.getElementById(inputId);
    if (el && document.activeElement !== el) {
        el.value = curFrame;
    }
}

function updateGotoTimeInputValue(inputId, curSec) {
    const el = document.getElementById(inputId);
    if (el && document.activeElement !== el) {
        el.value = formatTimeHMS(curSec);
    }
}

function bindGotoFrameInput(inputId, videoEl, timeInputId = null) {
    const inputEl = document.getElementById(inputId);
    if (!inputEl) return;

    function doSeek() {
        const frameVal = parseInt(inputEl.value);
        if (isNaN(frameVal) || frameVal < 0) return;
        const fps = currentVideoMeta.fps || 30.0;
        const targetSec = frameVal / fps;

        if (timeInputId) {
            const timeEl = document.getElementById(timeInputId);
            if (timeEl && document.activeElement !== timeEl) {
                timeEl.value = formatTimeHMS(targetSec);
            }
        }

        if (isAnyPopupOpen()) {
            syncSeekToTime(targetSec);
        } else if (videoEl) {
            const maxDur = videoEl.duration || Infinity;
            videoEl.currentTime = Math.max(0, Math.min(maxDur, targetSec));
            updateVRTimeDisplay();
            updateVRStitchedTimeDisplay();
            updateTelemetryTimeDisplay();
            updateKopfTimeDisplay();
            updateKabschTimeDisplay();
            updateVidstabTimeDisplay();
            updateHorizonTimeDisplay();
            updateCinematicTimeDisplay();
            updateTraveldirTimeDisplay();
            updateOrigTimeDisplay();
        }
    }

    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            doSeek();
            inputEl.blur();
        }
    });

    inputEl.addEventListener('change', () => {
        doSeek();
    });
}

function bindGotoTimeInput(timeInputId, frameInputId, videoEl) {
    const inputEl = document.getElementById(timeInputId);
    if (!inputEl) return;

    function doSeek() {
        const targetSec = parseTimeHMS(inputEl.value);
        if (targetSec === null || targetSec < 0) return;
        const fps = currentVideoMeta.fps || 30.0;
        const maxDur = (videoEl && isFinite(videoEl.duration) && videoEl.duration > 0) ? videoEl.duration : Infinity;
        const clampedSec = Math.max(0, Math.min(maxDur, targetSec));
        const targetFrame = Math.round(clampedSec * fps);

        if (frameInputId) {
            const frameEl = document.getElementById(frameInputId);
            if (frameEl && document.activeElement !== frameEl) {
                frameEl.value = targetFrame;
            }
        }
        if (document.activeElement !== inputEl) {
            inputEl.value = formatTimeHMS(clampedSec);
        }

        if (isAnyPopupOpen()) {
            syncSeekToTime(clampedSec);
        } else if (videoEl) {
            videoEl.currentTime = clampedSec;
            updateVRTimeDisplay();
            updateVRStitchedTimeDisplay();
            updateTelemetryTimeDisplay();
            updateKopfTimeDisplay();
            updateKabschTimeDisplay();
            updateVidstabTimeDisplay();
            updateHorizonTimeDisplay();
            updateCinematicTimeDisplay();
            updateTraveldirTimeDisplay();
            updateOrigTimeDisplay();
        }
    }

    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            doSeek();
            inputEl.blur();
        }
    });

    inputEl.addEventListener('change', () => {
        doSeek();
    });
}

function initAllGotoFrameInputs() {
    bindGotoFrameInput('orig-goto-frame', originalVideo, 'orig-goto-time');
    bindGotoTimeInput('orig-goto-time', 'orig-goto-frame', originalVideo);

    bindGotoFrameInput('vr-stitched-goto-frame', videoStitched, 'vr-stitched-goto-time');
    bindGotoTimeInput('vr-stitched-goto-time', 'vr-stitched-goto-frame', videoStitched);

    bindGotoFrameInput('vr-telemetry-goto-frame', videoTelemetry, 'vr-telemetry-goto-time');
    bindGotoTimeInput('vr-telemetry-goto-time', 'vr-telemetry-goto-frame', videoTelemetry);

    bindGotoFrameInput('vr-kopf-goto-frame', videoKopf, 'vr-kopf-goto-time');
    bindGotoTimeInput('vr-kopf-goto-time', 'vr-kopf-goto-frame', videoKopf);

    bindGotoFrameInput('vr-kabsch-goto-frame', videoKabsch, 'vr-kabsch-goto-time');
    bindGotoTimeInput('vr-kabsch-goto-time', 'vr-kabsch-goto-frame', videoKabsch);

    bindGotoFrameInput('vr-vidstab-goto-frame', videoVidstab, 'vr-vidstab-goto-time');
    bindGotoTimeInput('vr-vidstab-goto-time', 'vr-vidstab-goto-frame', videoVidstab);

    bindGotoFrameInput('vr-horizon-goto-frame', videoHorizon, 'vr-horizon-goto-time');
    bindGotoTimeInput('vr-horizon-goto-time', 'vr-horizon-goto-frame', videoHorizon);

    bindGotoFrameInput('vr-cinematic-goto-frame', videoCinematic, 'vr-cinematic-goto-time');
    bindGotoTimeInput('vr-cinematic-goto-time', 'vr-cinematic-goto-frame', videoCinematic);

    bindGotoFrameInput('vr-traveldir-goto-frame', videoTraveldir, 'vr-traveldir-goto-time');
    bindGotoTimeInput('vr-traveldir-goto-time', 'vr-traveldir-goto-frame', videoTraveldir);

    bindGotoFrameInput('vr-goto-frame', video360, 'vr-goto-time');
    bindGotoTimeInput('vr-goto-time', 'vr-goto-frame', video360);
}

function updatePreviewFrameDisplay(frameVal, secVal = null) {
    const fps = currentVideoMeta.fps || 30.0;
    if (secVal === null) {
        secVal = frameVal / fps;
    }
    const totalFrames = currentVideoMeta.total_frames || 0;
    const dur = currentVideoMeta.duration || 0;

    const sideInput = document.getElementById('preview-frame-input');
    const overlayInput = document.getElementById('preview-goto-frame');
    const overlayTimeInput = document.getElementById('preview-goto-time');
    const sideLbl = document.getElementById('preview-frame-time-lbl');
    const timeDisplay = document.getElementById('preview-time-display');
    const frameSlider = document.getElementById('preview-frame-slider');

    if (sideInput) {
        if (totalFrames > 0) sideInput.max = totalFrames;
        if (parseInt(sideInput.value) !== frameVal) sideInput.value = frameVal;
    }
    if (overlayInput) {
        if (totalFrames > 0) overlayInput.max = totalFrames;
        if (parseInt(overlayInput.value) !== frameVal) overlayInput.value = frameVal;
    }
    if (overlayTimeInput && document.activeElement !== overlayTimeInput) {
        overlayTimeInput.value = formatTimeHMS(secVal);
    }
    if (frameSlider) {
        if (totalFrames > 0) frameSlider.max = totalFrames;
        if (parseInt(frameSlider.value) !== frameVal) frameSlider.value = frameVal;
    }
    if (sideLbl) sideLbl.textContent = `(${secVal.toFixed(2)}s)`;
    if (timeDisplay) {
        const timeStr = `${Math.floor(secVal / 60)}:${String(Math.floor(secVal % 60)).padStart(2, '0')}`;
        const totalTimeStr = `${Math.floor(dur / 60)}:${String(Math.floor(dur % 60)).padStart(2, '0')}`;
        timeDisplay.textContent = `/ ${totalFrames} (${timeStr} / ${totalTimeStr})`;
    }
}

function setPreviewFrame(targetFrame) {
    const totalFrames = currentVideoMeta.total_frames || Infinity;
    const frame = Math.max(0, Math.min(totalFrames, Math.round(targetFrame)));
    updatePreviewFrameDisplay(frame);
    generatePreview();
}

function initPreviewFrameControls() {
    const sideInput = document.getElementById('preview-frame-input');
    const overlayInput = document.getElementById('preview-goto-frame');
    const btnFramePrev = document.getElementById('preview-frame-prev');
    const btnFrameNext = document.getElementById('preview-frame-next');

    const btnFirst = document.getElementById('preview-first-frame');
    const btnPrev10 = document.getElementById('preview-prev-10');
    const btnPrev = document.getElementById('preview-prev-frame');
    const btnRefresh = document.getElementById('preview-refresh-btn');
    const btnNext = document.getElementById('preview-next-frame');
    const btnNext10 = document.getElementById('preview-next-10');
    const btnLast = document.getElementById('preview-last-frame');

    function getCurrentFrame() {
        if (sideInput && sideInput.value !== '') return parseInt(sideInput.value) || 0;
        if (overlayInput && overlayInput.value !== '') return parseInt(overlayInput.value) || 0;
        return 0;
    }

    if (sideInput) {
        sideInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                setPreviewFrame(getCurrentFrame());
                sideInput.blur();
            }
        });
        sideInput.addEventListener('change', () => {
            setPreviewFrame(getCurrentFrame());
        });
    }

    if (overlayInput) {
        overlayInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                setPreviewFrame(parseInt(overlayInput.value) || 0);
                overlayInput.blur();
            }
        });
        overlayInput.addEventListener('change', () => {
            setPreviewFrame(parseInt(overlayInput.value) || 0);
        });
    }

    const overlayTimeInput = document.getElementById('preview-goto-time');
    if (overlayTimeInput) {
        function doSeekPreviewTime() {
            const secVal = parseTimeHMS(overlayTimeInput.value);
            if (secVal !== null && secVal >= 0) {
                const fps = currentVideoMeta.fps || 30.0;
                setPreviewFrame(Math.round(secVal * fps));
            }
        }
        overlayTimeInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                doSeekPreviewTime();
                overlayTimeInput.blur();
            }
        });
        overlayTimeInput.addEventListener('change', () => {
            doSeekPreviewTime();
        });
    }

    const frameSlider = document.getElementById('preview-frame-slider');
    if (frameSlider) {
        frameSlider.addEventListener('input', () => {
            updatePreviewFrameDisplay(parseInt(frameSlider.value) || 0);
        });
        frameSlider.addEventListener('change', () => {
            setPreviewFrame(parseInt(frameSlider.value) || 0);
        });
    }

    if (btnFramePrev) btnFramePrev.addEventListener('click', () => setPreviewFrame(getCurrentFrame() - 1));
    if (btnFrameNext) btnFrameNext.addEventListener('click', () => setPreviewFrame(getCurrentFrame() + 1));

    if (btnFirst) btnFirst.addEventListener('click', () => setPreviewFrame(0));
    if (btnPrev10) btnPrev10.addEventListener('click', () => setPreviewFrame(getCurrentFrame() - 10));
    if (btnPrev) btnPrev.addEventListener('click', () => setPreviewFrame(getCurrentFrame() - 1));
    if (btnRefresh) btnRefresh.addEventListener('click', () => setPreviewFrame(getCurrentFrame()));
    if (btnNext) btnNext.addEventListener('click', () => setPreviewFrame(getCurrentFrame() + 1));
    if (btnNext10) btnNext10.addEventListener('click', () => setPreviewFrame(getCurrentFrame() + 10));
    if (btnLast) btnLast.addEventListener('click', () => setPreviewFrame(currentVideoMeta.total_frames || 0));

    const btnToggleGuide = document.getElementById('preview-toggle-guide');
    const horizonLine = document.getElementById('preview-horizon-line');
    if (btnToggleGuide && horizonLine) {
        btnToggleGuide.addEventListener('click', (e) => {
            e.stopPropagation();
            const isHidden = horizonLine.classList.toggle('hidden');
            btnToggleGuide.classList.toggle('active', !isHidden);
            btnToggleGuide.style.opacity = isHidden ? '0.4' : '1.0';
            btnToggleGuide.title = isHidden ? 'Toggle Horizon Guide Line (Off - click to show)' : 'Toggle Horizon Guide Line (On - click to hide)';
        });
    }
}

function copyVideoLink(videoEl, btnEl) {
    if (!videoEl) return;
    let rawPath = videoEl.currentSrc || videoEl.src || '';
    if (!rawPath) {
        rawPath = videoEl.getAttribute('data-loaded-src') ||
                  videoEl.getAttribute('data-file-path') ||
                  videoEl.getAttribute('data-pending-src') || '';
    }
    if (!rawPath && videoEl.id === 'original-video') {
        const origImg = document.getElementById('original-img');
        if (origImg && (origImg.currentSrc || origImg.src || origImg.getAttribute('data-orig-path') || origImg.getAttribute('data-loaded-src'))) {
            rawPath = origImg.currentSrc || origImg.src || origImg.getAttribute('data-orig-path') || origImg.getAttribute('data-loaded-src') || '';
        }
    }
    if (!rawPath) {
        alert("No video source is loaded yet.");
        return;
    }
    let url = rawPath;
    try {
        url = new URL(rawPath, window.location.href).href;
    } catch (_) {
        url = rawPath;
    }
    const cleanUrl = url.replace(/\?t=\d+$/, '');

    function onCopied() {
        if (btnEl) {
            const origText = btnEl.innerHTML;
            btnEl.innerHTML = btnEl.classList.contains('vr-popup-sync-btn') ? '&#10003; Copied Link!' : '&#10003;';
            setTimeout(() => {
                btnEl.innerHTML = origText;
            }, 1500);
        }
    }

    function copyFallback(text) {
        try {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            onCopied();
        } catch (e) {
            console.error("Failed to copy video link:", e);
        }
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(cleanUrl).then(onCopied).catch(err => {
            console.warn("navigator.clipboard write failed, using fallback:", err);
            copyFallback(cleanUrl);
        });
    } else {
        copyFallback(cleanUrl);
    }
}

function initFlipHorizontalToggle(btnId, targetIds) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        let isFlipped = false;
        targetIds.forEach(id => {
            const el = (typeof id === 'string') ? document.getElementById(id) : id;
            if (el) {
                el.classList.toggle('video-flipped-h');
                isFlipped = el.classList.contains('video-flipped-h');
            }
        });
        btn.classList.toggle('active', isFlipped);
        btn.title = isFlipped ? 'Flip Horizontally (Flipped - click to unflip)' : 'Flip Horizontally (Mirror)';
    });
}

function initAllFlipControls() {
    initFlipHorizontalToggle('orig-flip-h', ['player-orig-container', 'original-video', 'original-img']);
    initFlipHorizontalToggle('preview-flip-h', ['preview-img']);
    initFlipHorizontalToggle('vr-stitched-flip-h', ['player-stitched-container', 'video-stitched']);
    initFlipHorizontalToggle('vr-telemetry-flip-h', ['player-telemetry-container', 'video-telemetry']);
    initFlipHorizontalToggle('vr-kopf-flip-h', ['player-kopf-container', 'video-kopf']);
    initFlipHorizontalToggle('vr-kabsch-flip-h', ['player-kabsch-container', 'video-kabsch']);
    initFlipHorizontalToggle('vr-vidstab-flip-h', ['player-vidstab-container', 'video-vidstab']);
    initFlipHorizontalToggle('vr-horizon-flip-h', ['player-horizon-container', 'video-horizon']);
    initFlipHorizontalToggle('vr-cinematic-flip-h', ['player-cinematic-container', 'video-cinematic']);
    initFlipHorizontalToggle('vr-traveldir-flip-h', ['player-traveldir-container', 'video-traveldir']);
    initFlipHorizontalToggle('vr-flip-h', ['player-360-container', 'video-360']);
}

function initPlayerVRFlatToggle(cfg) {
    const toggleBtn = document.getElementById(cfg.toggleBtnId);
    const toggleText = document.getElementById(cfg.toggleTextId);
    const playerContainer = document.getElementById(cfg.playerContainerId);
    const videoEl = document.getElementById(cfg.videoElId);
    const vrInputsContainer = document.getElementById(cfg.vrInputsContainerId);
    const zoomIn = document.getElementById(cfg.zoomInId);
    const zoomOut = document.getElementById(cfg.zoomOutId);
    const resetView = document.getElementById(cfg.resetViewId);

    if (!toggleBtn || !playerContainer || !videoEl) return;

    let isVR = true;

    function applyMode(vrMode) {
        isVR = !!vrMode;
        if (isVR) {
            playerContainer.style.display = 'block';
            videoEl.style.display = 'none';
            toggleBtn.classList.add('active');
            toggleBtn.style.background = 'rgba(56,189,248,0.25)';
            toggleBtn.style.borderColor = '#38bdf8';
            if (toggleText) toggleText.textContent = '2D';
            if (vrInputsContainer) vrInputsContainer.style.display = '';
            if (zoomIn) zoomIn.style.display = '';
            if (zoomOut) zoomOut.style.display = '';
            if (resetView) resetView.style.display = '';
            if (typeof cfg.onResize === 'function') {
                cfg.onResize();
                setTimeout(cfg.onResize, 80);
                setTimeout(cfg.onResize, 250);
            }
        } else {
            playerContainer.style.display = 'none';
            videoEl.style.display = 'block';
            videoEl.style.maxWidth = '100%';
            videoEl.style.maxHeight = 'calc(100% - 40px)';
            videoEl.style.width = '100%';
            videoEl.style.height = 'calc(100% - 40px)';
            videoEl.style.objectFit = 'contain';
            toggleBtn.classList.remove('active');
            toggleBtn.style.background = '';
            toggleBtn.style.borderColor = 'rgba(56,189,248,0.4)';
            if (toggleText) toggleText.textContent = '360';
            if (vrInputsContainer) vrInputsContainer.style.display = 'none';
            if (zoomIn) zoomIn.style.display = 'none';
            if (zoomOut) zoomOut.style.display = 'none';
            if (resetView) resetView.style.display = 'none';
        }
    }

    videoEl.addEventListener('click', () => {
        if (!isVR) {
            if (videoEl.paused) videoEl.play();
            else videoEl.pause();
        }
    });

    toggleBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        applyMode(!isVR);
    });

    // Default to 360 VR
    applyMode(true);
}

function initAllPlayerVRFlatToggles() {
    const list = [
        {
            key: 'stitched',
            toggleBtnId: 'vr-stitched-toggle-flat',
            toggleTextId: 'vr-stitched-toggle-flat-text',
            playerContainerId: 'player-stitched-container',
            videoElId: 'video-stitched',
            vrInputsContainerId: 'vr-stitched-inputs-container',
            zoomInId: 'vr-stitched-zoom-in',
            zoomOutId: 'vr-stitched-zoom-out',
            resetViewId: 'vr-stitched-reset-view',
            onResize: () => { if (typeof onWindowResizeStitched === 'function') onWindowResizeStitched(); }
        },
        {
            key: 'telemetry',
            toggleBtnId: 'vr-telemetry-toggle-flat',
            toggleTextId: 'vr-telemetry-toggle-flat-text',
            playerContainerId: 'player-telemetry-container',
            videoElId: 'video-telemetry',
            vrInputsContainerId: 'vr-telemetry-inputs-container',
            zoomInId: 'vr-telemetry-zoom-in',
            zoomOutId: 'vr-telemetry-zoom-out',
            resetViewId: 'vr-telemetry-reset-view',
            onResize: () => { if (typeof onWindowResizeTelemetry === 'function') onWindowResizeTelemetry(); }
        },
        {
            key: 'vidstab',
            toggleBtnId: 'vr-vidstab-toggle-flat',
            toggleTextId: 'vr-vidstab-toggle-flat-text',
            playerContainerId: 'player-vidstab-container',
            videoElId: 'video-vidstab',
            vrInputsContainerId: 'vr-vidstab-inputs-container',
            zoomInId: 'vr-vidstab-zoom-in',
            zoomOutId: 'vr-vidstab-zoom-out',
            resetViewId: 'vr-vidstab-reset-view',
            onResize: () => { if (typeof onWindowResizeVidstab === 'function') onWindowResizeVidstab(); }
        },
        {
            key: 'kabsch',
            toggleBtnId: 'vr-kabsch-toggle-flat',
            toggleTextId: 'vr-kabsch-toggle-flat-text',
            playerContainerId: 'player-kabsch-container',
            videoElId: 'video-kabsch',
            vrInputsContainerId: 'vr-kabsch-inputs-container',
            zoomInId: 'vr-kabsch-zoom-in',
            zoomOutId: 'vr-kabsch-zoom-out',
            resetViewId: 'vr-kabsch-reset-view',
            onResize: () => { if (typeof onWindowResizeKabsch === 'function') onWindowResizeKabsch(); }
        },
        {
            key: 'kopf',
            toggleBtnId: 'vr-kopf-toggle-flat',
            toggleTextId: 'vr-kopf-toggle-flat-text',
            playerContainerId: 'player-kopf-container',
            videoElId: 'video-kopf',
            vrInputsContainerId: 'vr-kopf-inputs-container',
            zoomInId: 'vr-kopf-zoom-in',
            zoomOutId: 'vr-kopf-zoom-out',
            resetViewId: 'vr-kopf-reset-view',
            onResize: () => { if (typeof onWindowResizeKopf === 'function') onWindowResizeKopf(); }
        },
        {
            key: 'horizon',
            toggleBtnId: 'vr-horizon-toggle-flat',
            toggleTextId: 'vr-horizon-toggle-flat-text',
            playerContainerId: 'player-horizon-container',
            videoElId: 'video-horizon',
            vrInputsContainerId: 'vr-horizon-inputs-container',
            zoomInId: 'vr-horizon-zoom-in',
            zoomOutId: 'vr-horizon-zoom-out',
            resetViewId: 'vr-horizon-reset-view',
            onResize: () => { if (typeof onWindowResizeHorizon === 'function') onWindowResizeHorizon(); }
        },
        {
            key: 'cinematic',
            toggleBtnId: 'vr-cinematic-toggle-flat',
            toggleTextId: 'vr-cinematic-toggle-flat-text',
            playerContainerId: 'player-cinematic-container',
            videoElId: 'video-cinematic',
            vrInputsContainerId: 'vr-cinematic-inputs-container',
            zoomInId: 'vr-cinematic-zoom-in',
            zoomOutId: 'vr-cinematic-zoom-out',
            resetViewId: 'vr-cinematic-reset-view',
            onResize: () => { if (typeof onWindowResizeCinematic === 'function') onWindowResizeCinematic(); }
        },
        {
            key: 'traveldir',
            toggleBtnId: 'vr-traveldir-toggle-flat',
            toggleTextId: 'vr-traveldir-toggle-flat-text',
            playerContainerId: 'player-traveldir-container',
            videoElId: 'video-traveldir',
            vrInputsContainerId: 'vr-traveldir-inputs-container',
            zoomInId: 'vr-traveldir-zoom-in',
            zoomOutId: 'vr-traveldir-zoom-out',
            resetViewId: 'vr-traveldir-reset-view',
            onResize: () => { if (typeof onWindowResizeTraveldir === 'function') onWindowResizeTraveldir(); }
        },
        {
            key: '360',
            toggleBtnId: 'vr-360-toggle-flat',
            toggleTextId: 'vr-360-toggle-flat-text',
            playerContainerId: 'player-360-container',
            videoElId: 'video-360',
            vrInputsContainerId: 'vr-360-inputs-container',
            zoomInId: 'vr-zoom-in',
            zoomOutId: 'vr-zoom-out',
            resetViewId: 'vr-reset-view',
            onResize: () => { if (typeof onWindowResize === 'function') onWindowResize(); }
        }
    ];
    list.forEach(initPlayerVRFlatToggle);
}

function initAllVRControls() {
    initOrigVRControls();
    initStitchedVRControls();
    initTelemetryVRControls();
    initKopfVRControls();
    initKabschVRControls();
    initVidstabVRControls();
    initHorizonVRControls();
    initCinematicVRControls();
    initTraveldirVRControls();
}

// ── Run setup on load ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    if (options.input) detectInputFeatures(options.input.value);
    init();
    initAllVRControls();
    initAllPlayerVRFlatToggles();
    initAllFlipControls();
    initAllGotoFrameInputs();

    const btnInjectStandalone = document.getElementById('btn-inject-standalone');
    const inputSelect = document.getElementById('input_name');
    const standaloneInjectStatus = document.getElementById('standalone_inject_status');

    if (btnInjectStandalone && inputSelect) {
        btnInjectStandalone.addEventListener('click', async () => {
            const selectedFile = inputSelect.value;
            if (!selectedFile) return;

            btnInjectStandalone.disabled = true;
            if (standaloneInjectStatus) {
                standaloneInjectStatus.style.display = 'block';
                standaloneInjectStatus.style.color = '#38bdf8';
                standaloneInjectStatus.textContent = 'Injecting 360° metadata...';
            }

            try {
                const formData = new FormData();
                /* action:inject_standalone_meta → api/v1/videos/inject-meta */
                formData.append('input', selectedFile);

                const response = await fetch('api/v1/videos/inject-meta', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();

                if (data.status === 'success') {
                    if (standaloneInjectStatus) {
                        standaloneInjectStatus.style.color = '#4ade80';
                        standaloneInjectStatus.textContent = `Success! Saved as: ${data.output}`;
                    }
                } else {
                    if (standaloneInjectStatus) {
                        standaloneInjectStatus.style.color = '#f87171';
                        standaloneInjectStatus.textContent = `Error: ${data.error || 'Failed'}`;
                    }
                }
            } catch (err) {
                console.error(err);
                if (standaloneInjectStatus) {
                    standaloneInjectStatus.style.color = '#f87171';
                    standaloneInjectStatus.textContent = `Error: ${err.message}`;
                }
            } finally {
                btnInjectStandalone.disabled = false;
            }
        });
    }

    initStreetViewUI();
});

function updateStreetViewVisibility() {
    const svEnabled = document.getElementById('streetview_enabled');
    const svSettings = document.getElementById('streetview-settings');
    const modeAPanel = document.getElementById('streetview-mode-a-panel');
    const modeBPanel = document.getElementById('streetview-mode-b-panel');

    if (svEnabled && svSettings) {
        svSettings.style.display = svEnabled.checked ? 'block' : 'none';
    }

    const activeRadio = document.querySelector('input[name="streetview_mode"]:checked');
    if (activeRadio && modeAPanel && modeBPanel) {
        if (activeRadio.value === 'A') {
            modeAPanel.style.display = 'block';
            modeBPanel.style.display = 'none';
        } else {
            modeAPanel.style.display = 'none';
            modeBPanel.style.display = 'block';
        }
    }
}

function populateStreetViewAdditionalVideos() {
    const inputSelect = document.getElementById('input_name');
    const multiSelect = document.getElementById('streetview_additional_videos');
    if (!inputSelect || !multiSelect) return;

    const currentSelected = Array.from(multiSelect.selectedOptions).map(o => o.value);
    const primaryFile = inputSelect.value;
    multiSelect.innerHTML = '';

    Array.from(inputSelect.options).forEach(opt => {
        if (!opt.value || opt.value === primaryFile) return;
        const newOpt = document.createElement('option');
        newOpt.value = opt.value;
        newOpt.textContent = opt.textContent;
        if (currentSelected.includes(opt.value)) {
            newOpt.selected = true;
        }
        multiSelect.appendChild(newOpt);
    });
}

function initStreetViewUI() {
    const svEnabled = document.getElementById('streetview_enabled');
    const modeRadios = document.querySelectorAll('input[name="streetview_mode"]');
    const cpFilePicker = document.getElementById('streetview_cp_file_picker');
    const cpFileLbl = document.getElementById('streetview_cp_file_lbl');
    const cpText = document.getElementById('streetview_checkpoints');
    const gpxPathInput = document.getElementById('streetview_gpx_path');
    const inputSelect = document.getElementById('input_name');

    if (inputSelect) {
        inputSelect.addEventListener('change', () => {
            populateStreetViewAdditionalVideos();
            // Automatically reset Mode A checkpoints when switching to a different input video
            const cpText = document.getElementById('streetview_checkpoints');
            if (cpText) cpText.value = '';
            localStorage.removeItem('streetview_checkpoints_transfer');
            localStorage.removeItem('streetview_checkpoints_result');
            if (typeof cpPickerPoints !== 'undefined') {
                cpPickerPoints = [];
            }
        });
    }
    populateStreetViewAdditionalVideos();

    if (svEnabled) {
        svEnabled.addEventListener('change', updateStreetViewVisibility);
    }

    if (modeRadios) {
        modeRadios.forEach(radio => {
            radio.addEventListener('change', updateStreetViewVisibility);
        });
    }

    updateStreetViewVisibility();
    initCheckpointPickerModal();

    if (cpFilePicker && cpText) {
        cpFilePicker.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;
            if (cpFileLbl) cpFileLbl.textContent = file.name;

            const reader = new FileReader();
            reader.onload = (evt) => {
                const content = evt.target.result;
                cpText.value = content;
            };
            reader.readAsText(file);
        });
    }

    const btnStandalone = document.getElementById('btn-streetview-standalone');
    const btnViewMap = document.getElementById('btn-streetview-view-map');
    const standaloneStatus = document.getElementById('streetview_standalone_status');

    let currentMapUrl = '';
    
    async function updateActiveMapUrl() {
        const inputSelect = document.getElementById('input_name');
        const selectedFile = inputSelect ? inputSelect.value : '';
        if (selectedFile) {
            try {
                const formData = new FormData();
                /* action:find_streetview_map → api/v1/streetview/find-map */
                formData.append('input', selectedFile);
                
                const res = await fetch(`api/v1/streetview/find-map?input=${encodeURIComponent(formData.get('input') || '')}`);
                const data = await res.json();
                if (data && data.status === 'success' && data.map_url) {
                    currentMapUrl = data.map_url.replace(/^\//, '');
                    return;
                }
            } catch (e) {}
        }
        currentMapUrl = 'data/output/preview_map.html';
    }
    
    if (inputSelect) {
        inputSelect.addEventListener('change', updateActiveMapUrl);
    }
    updateActiveMapUrl();

    if (btnViewMap) {
        btnViewMap.addEventListener('click', async () => {
            const inputSelect = document.getElementById('input_name');
            const selectedFile = inputSelect ? inputSelect.value : '';
            
            const modeRadio = document.querySelector('input[name="streetview_mode"]:checked');
            const mode = modeRadio ? modeRadio.value : 'A';
            const checkpoints = document.getElementById('streetview_checkpoints')?.value?.trim() || '';
            const gpxPath = document.getElementById('streetview_gpx_path')?.value || '';
            const startCoord = document.getElementById('streetview_start_coord')?.value?.trim() || '';
            const endCoord = document.getElementById('streetview_end_coord')?.value?.trim() || '';
            const timeOffset = document.getElementById('streetview_time_offset')?.value || '0';
            const startTime = document.getElementById('streetview_start_time')?.value?.trim() || 'auto';
            const smoothGps = document.getElementById('streetview_smooth_gps')?.checked ? '1' : '0';
            const autoPad = document.getElementById('streetview_auto_pad')?.checked ? '1' : '0';

            // 1. If user filled checkpoints or selected GPX, generate on-the-fly preview instantly!
            if ((mode === 'A' && checkpoints) || (mode === 'B' && gpxPath)) {
                try {
                    const formData = new FormData();
                    /* action:preview_streetview_map → api/v1/streetview/preview-map */
                    if (selectedFile) formData.append('input', selectedFile);
                    formData.append('streetview_mode', mode);
                    formData.append('streetview_checkpoints', checkpoints);
                    formData.append('streetview_gpx_path', gpxPath);
                    formData.append('streetview_start_coord', startCoord);
                    formData.append('streetview_end_coord', endCoord);
                    formData.append('streetview_time_offset', timeOffset);
                    formData.append('streetview_start_time', startTime);
                    formData.append('streetview_smooth_gps', smoothGps);
                    formData.append('streetview_auto_pad', autoPad);

                    const res = await fetch('api/v1/streetview/preview-map', { method: 'POST', body: formData });
                    const data = await res.json();
                    if (data && data.status === 'success' && data.map_url) {
                        const mapUrl = data.map_url.replace(/^\//, '') + '?t=' + Date.now();
                        window.open(mapUrl, '_blank');
                        return;
                    } else if (data && data.error) {
                        alert(data.error);
                        return;
                    }
                } catch (e) {
                    console.warn('Error generating live preview map:', e);
                }
            }

            // 2. Check if existing stitched map exists
            if (selectedFile) {
                try {
                    const formData = new FormData();
                    /* action:find_streetview_map → api/v1/streetview/find-map */
                    formData.append('input', selectedFile);
                    
                const res = await fetch(`api/v1/streetview/find-map?input=${encodeURIComponent(formData.get('input') || '')}`);
                    const data = await res.json();
                    if (data && data.status === 'success' && data.map_url) {
                        currentMapUrl = data.map_url.replace(/^\//, '');
                        window.open(currentMapUrl + '?t=' + Date.now(), '_blank');
                        return;
                    }
                } catch (e) {}
            }

            alert('No Street View map generated yet for this video.');
        });
    }

    const btnMatchGpx = document.getElementById('btn-match-gpx-coords');
    const gpxMatchResult = document.getElementById('streetview_gpx_match_result');
    if (btnMatchGpx) {
        btnMatchGpx.addEventListener('click', async () => {
            const gpxFile = document.getElementById('streetview_gpx_path')?.value || '';
            const startCoord = document.getElementById('streetview_start_coord')?.value?.trim() || '';
            const endCoord = document.getElementById('streetview_end_coord')?.value?.trim() || '';
            
            if (!gpxFile) {
                alert('Please select a GPX Log File first.');
                return;
            }
            if (!startCoord) {
                alert('Please enter Video Start (lat, lon) coordinates (e.g. 40.78120, -73.96650).');
                return;
            }
            
            btnMatchGpx.disabled = true;
            btnMatchGpx.textContent = 'Matching...';
            if (gpxMatchResult) {
                gpxMatchResult.style.display = 'block';
                gpxMatchResult.style.color = '#38bdf8';
                gpxMatchResult.textContent = 'Searching GPX trackpoints for closest match...';
            }
            
            try {
                const fd = new FormData();
                /* action:match_gpx_coords → api/v1/gpx/match-coords */
                fd.append('gpx_path', gpxFile);
                fd.append('start_coord', startCoord);
                if (endCoord) fd.append('end_coord', endCoord);
                
                const res = await fetch('api/v1/gpx/match-coords', { method: 'POST', body: fd });
                const data = await res.json();
                
                if (data && data.status === 'success' && data.matched_start) {
                    const ms = data.matched_start;
                    const offsetInput = document.getElementById('streetview_time_offset');
                    if (offsetInput && ms.offset_sec !== undefined) {
                        offsetInput.value = ms.offset_sec;
                    }
                    let msg = `🎯 Matched Point #${escapeHtml(ms.index)} at ${escapeHtml(ms.time)} (Offset: <strong>+${escapeHtml(ms.offset_sec)}s</strong>, Accuracy: <strong>${escapeHtml(ms.distance_m)}m</strong>)`;
                    if (data.matched_end) {
                        const me = data.matched_end;
                        msg += `<br>🏁 End Point #${escapeHtml(me.index)} at ${escapeHtml(me.time)} (Span: <strong>${escapeHtml(data.span_duration_sec)}s</strong>, Accuracy: <strong>${escapeHtml(me.distance_m)}m</strong>)`;
                    }
                    if (gpxMatchResult) {
                        gpxMatchResult.style.color = '#4ade80';
                        gpxMatchResult.innerHTML = msg;
                    }
                } else {
                    if (gpxMatchResult) {
                        gpxMatchResult.style.color = '#f87171';
                        gpxMatchResult.textContent = `Matching error: ${data.error || 'No matching point found'}`;
                    }
                }
            } catch (e) {
                if (gpxMatchResult) {
                    gpxMatchResult.style.color = '#f87171';
                    gpxMatchResult.textContent = `Error: ${e.message}`;
                }
            } finally {
                btnMatchGpx.disabled = false;
                btnMatchGpx.textContent = 'Match in GPX';
            }
        });
    }

    if (btnStandalone) {
        btnStandalone.addEventListener('click', async () => {
            const selectedFile = inputSelect ? inputSelect.value : '';
            if (!selectedFile) {
                alert('Please select an Input Video File first.');
                return;
            }

            if (!validateStreetViewOptions(true)) {
                return;
            }

            btnStandalone.disabled = true;
            if (standaloneStatus) {
                standaloneStatus.style.color = '#38bdf8';
                standaloneStatus.textContent = 'Generating Google Street View export files & route map...';
            }

            // Immediately activate and display the main Processing Status bar
            if (progressCard) {
                progressCard.style.display = 'flex';
                progressCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
            updateStatusHeaderText('exporting');
            if (statusBadge) {
                statusBadge.className = 'status-pill exporting';
                statusBadge.textContent = 'EXPORTING';
            }
            if (phaseLabel) {
                phaseLabel.innerHTML = 'Phase: <strong style="color: #facc15;">Exporting Google Street View directly from input video...</strong>';
            }
            if (progressBarFill) progressBarFill.style.width = '10%';
            if (progressPercent) progressPercent.textContent = '10%';
            if (metaSpeed) metaSpeed.textContent = 'Processing';
            if (metaEta) metaEta.textContent = 'Calculating...';

            let startExportTs = Date.now();
            let timerInterval = setInterval(() => {
                const elSec = Math.floor((Date.now() - startExportTs) / 1000);
                if (metaElapsed) metaElapsed.textContent = typeof formatTimeHHMMSS === 'function' ? formatTimeHHMMSS(elSec) : `${elSec}s`;
            }, 1000);

            let localPoll = setInterval(checkStatus, 800);

            try {
                const formData = new FormData();
                /* action:export_streetview_standalone → api/v1/streetview/export */
                formData.append('input', selectedFile);
                if (currentJobId) formData.append('job_id', currentJobId);

                const isMultiVideo = document.getElementById('streetview_multivideo_enabled')?.checked;
                if (isMultiVideo) {
                    const multiSelect = document.getElementById('streetview_additional_videos');
                    if (multiSelect) {
                        const additionalFiles = Array.from(multiSelect.selectedOptions).map(o => o.value);
                        if (additionalFiles.length > 0) {
                            formData.append('additional_videos', additionalFiles.join(','));
                        }
                    }
                }

                const modeRadio = document.querySelector('input[name="streetview_mode"]:checked');
                const modeVal = modeRadio ? modeRadio.value : 'A';
                formData.append('streetview_mode', modeVal);
                formData.append('streetview_checkpoints', document.getElementById('streetview_checkpoints')?.value || '');
                formData.append('streetview_gpx_path', document.getElementById('streetview_gpx_path')?.value || '');
                formData.append('streetview_start_coord', document.getElementById('streetview_start_coord')?.value?.trim() || '');
                formData.append('streetview_end_coord', document.getElementById('streetview_end_coord')?.value?.trim() || '');
                const svSmoothGps = document.getElementById('streetview_smooth_gps');
                formData.append('streetview_smooth_gps', svSmoothGps && svSmoothGps.checked ? '1' : '0');
                formData.append('streetview_start_time', document.getElementById('streetview_start_time')?.value || '');
                formData.append('streetview_time_offset', document.getElementById('streetview_time_offset')?.value || '0');
                const autoPad = document.getElementById('streetview_auto_pad');
                formData.append('streetview_auto_pad', autoPad && autoPad.checked ? '1' : '0');
                const svBitrate = document.getElementById('streetview_bitrate');
                if (svBitrate) formData.append('streetview_bitrate', svBitrate.value);
                const svStripAudio = document.getElementById('streetview_strip_audio');
                formData.append('streetview_strip_audio', svStripAudio && svStripAudio.checked ? '1' : '0');

                const response = await fetch('api/v1/streetview/export', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();

                if (data.command) logCommand(data.command);

                if (data.status === 'success') {
                    if (data.map_url) {
                        currentMapUrl = data.map_url;
                    }
                    if (btnViewMap) {
                        btnViewMap.style.display = 'inline-flex';
                    }
                    if (standaloneStatus) {
                        standaloneStatus.style.color = '#4ade80';
                        standaloneStatus.innerHTML = `Success! Video: <strong>${escapeHtml(data.output_video)}</strong> | GPX: <strong>${escapeHtml(data.output_gpx)}</strong><br><span style="color:#38bdf8; font-size:11px;">Interactive route map ready! Click "📍 View Route on Map" above to inspect.</span>`;
                    }
                } else {
                    if (standaloneStatus) {
                        standaloneStatus.style.color = '#f87171';
                        standaloneStatus.textContent = `Error: ${data.error || 'Failed'}`;
                    }
                }
            } catch (err) {
                console.error(err);
                if (standaloneStatus) {
                    standaloneStatus.style.color = '#f87171';
                    standaloneStatus.textContent = `Error: ${err.message}`;
                }
            } finally {
                clearInterval(localPoll);
                clearInterval(timerInterval);
                btnStandalone.disabled = false;
                await checkStatus();
            }
        });
    }
}


document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('btn-launch-horizon-editor')?.addEventListener('click', launchHorizonEditor);
    document.getElementById('btn-resume-pipeline')?.addEventListener('click', resumePipelineAfterCheckpoints);
    document.getElementById('btn-continue-master-render')?.addEventListener('click', submitChosenTransforms);
    document.getElementById('btn-cancel-transforms-modal')?.addEventListener('click', async () => {
        const cancelled = await resetStitcher();
        if (cancelled) {
            const modal = document.getElementById('transformSelectModal');
            if (modal) modal.style.display = 'none';
            window._transformsSubmitted = false;
            window._lastRenderedTransformKey = null;
        }
    });
    document.getElementById('btn-close-report-box')?.addEventListener('click', () => {
        const rb = document.getElementById('modalTransformReportBox');
        if (rb) rb.style.display = 'none';
    });

    Object.entries(PRE_APPLIED_FEATURE_MAP).forEach(([preId, targetId]) => {
        document.getElementById(preId)?.addEventListener('change', (e) => {
            if (e.target.checked) {
                const targetEl = document.getElementById(targetId);
                if (targetEl && targetEl.checked) {
                    targetEl.checked = false;
                    targetEl.dispatchEvent(new Event('change'));
                }
            }
            if (STAB_METHOD_IDS.includes(targetId)) {
                syncStabMasterState();
            }
            updateInputFeaturesBadge();
        });

        document.getElementById(targetId)?.addEventListener('change', (e) => {
            if (e.target.checked) {
                const preEl = document.getElementById(preId);
                if (preEl && preEl.checked) {
                    preEl.checked = false;
                    updateInputFeaturesBadge();
                }
            }
            if (STAB_METHOD_IDS.includes(targetId)) {
                syncStabMasterState();
            }
        });
    });

    initSystemHealth();
});

