/**
 * Horizon Checkpoint Pause & Master Render Transform Selection Modals.
 * Manages interactive modal checkpoints, external horizon editor launch,
 * and transform candidate selection callbacks.
 */

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function getCurrentJobId() {
    if (typeof window !== 'undefined' && window.currentJobId) return window.currentJobId;
    if (typeof sessionStorage !== 'undefined') return sessionStorage.getItem('current_tab_job_id') || '';
    return '';
}

async function safeFetchJson(url, options = {}) {
    if (typeof window !== 'undefined' && typeof window.safeFetchJson === 'function') {
        return window.safeFetchJson(url, options);
    }
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

// ── Horizon Checkpoint Interactive Pause & Resume ────────────────────────────
let currentCheckpointModalInfo = null;

function showCheckpointPauseModal(outBase, videoPath) {
    const outNameVal = document.getElementById('output_name')?.value || '';
    if (!outBase && outNameVal) {
        const dotIdx = outNameVal.lastIndexOf('.');
        const baseNoExt = dotIdx === -1 ? outNameVal : outNameVal.substring(0, dotIdx);
        outBase = baseNoExt.replace(/^.*[\\\/]/, '');
    }
    currentCheckpointModalInfo = { outBase: outBase || '', videoPath: videoPath || '' };

    const modal = document.getElementById('checkpointPauseModal');
    const outBaseEl = document.getElementById('modalCpOutBase');
    const targetJsonEl = document.getElementById('modalCpTargetJson');
    const videoNameEl = document.getElementById('modalCpVideoName');

    if (outBaseEl) outBaseEl.textContent = outBase || '-';
    if (targetJsonEl) targetJsonEl.textContent = `data/runtime/work/${outBase ? outBase + '_' : ''}horizon_checkpoints.json`;
    if (videoNameEl) videoNameEl.textContent = videoPath ? videoPath.replace(/^.*[\\\/]/, '') : 'Active Stage Output Video';
    if (modal) modal.style.display = 'flex';
}

function launchHorizonEditor() {
    let outBase = currentCheckpointModalInfo ? currentCheckpointModalInfo.outBase : '';
    let videoPath = currentCheckpointModalInfo ? currentCheckpointModalInfo.videoPath : '';
    const outNameVal = document.getElementById('output_name')?.value || '';

    if (!outBase && outNameVal) {
        const dotIdx = outNameVal.lastIndexOf('.');
        const baseNoExt = dotIdx === -1 ? outNameVal : outNameVal.substring(0, dotIdx);
        outBase = baseNoExt.replace(/^.*[\\\/]/, '');
    }

    if (!videoPath) {
        const videoNameEl = document.getElementById('modalCpVideoName');
        if (videoNameEl && videoNameEl.textContent && videoNameEl.textContent !== '-') {
            videoPath = 'data/runtime/work/' + videoNameEl.textContent.trim();
        } else if (outNameVal) {
            videoPath = outNameVal;
        }
    }

    let url = 'horizon_editor.html';
    const params = [];
    if (videoPath) params.push('video=' + encodeURIComponent(videoPath));
    if (outBase) params.push('out_base=' + encodeURIComponent(outBase));
    const curPitch = document.getElementById('pitch_val')?.value || document.getElementById('pitch')?.value || '';
    const curRoll = document.getElementById('roll_val')?.value || document.getElementById('roll')?.value || '';
    if (curPitch) params.push('pitch=' + encodeURIComponent(curPitch));
    if (curRoll) params.push('roll=' + encodeURIComponent(curRoll));
    if (params.length > 0) url += '?' + params.join('&');

    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
        if (link.parentNode) link.parentNode.removeChild(link);
    }, 100);
}

async function resumePipelineAfterCheckpoints() {
    const modal = document.getElementById('checkpointPauseModal');
    if (modal) modal.style.display = 'none';
    const outBase = currentCheckpointModalInfo ? currentCheckpointModalInfo.outBase : '';
    window._checkpointModalDismissedFor = outBase || 'dismissed';
    try {
        await fetch(`api/v1/jobs/${encodeURIComponent(outBase)}/resume`, { method: 'POST' });
    } catch (e) {
        console.error('Failed to trigger pipeline resume', e);
    }
}

// ── Transform Selection Modal for Master Render ──────────────────────────────
let currentAwaitingTransforms = null;

function showTransformSelectModal(transforms, skippedTransforms) {
    const modal = document.getElementById('transformSelectModal');
    const listContainer = document.getElementById('modalTransformsList');
    const skippedContainer = document.getElementById('modalSkippedTransformsNotice');
    const countBadge = document.getElementById('modalTransformCount');
    if (!modal || !listContainer) return;

    if (window._transformsSubmitted) {
        return;
    }

    const transformKey = JSON.stringify((transforms || []).map(t => t.id + ':' + t.sendcmd_file + ':' + (t.is_unstabilized ? '1' : '0'))) + '|' + JSON.stringify((skippedTransforms || []).map(s => s.id));
    if (window._lastRenderedTransformKey === transformKey && modal.style.display === 'flex') {
        return;
    }
    window._lastRenderedTransformKey = transformKey;
    currentAwaitingTransforms = transforms;

    function updateModalActiveCount() {
        const total = listContainer.querySelectorAll('.modal-transform-checkbox').length;
        const checked = listContainer.querySelectorAll('.modal-transform-checkbox:checked').length;
        if (countBadge) {
            countBadge.textContent = `${checked} / ${total} active`;
            if (checked < total) {
                countBadge.style.background = 'rgba(239, 68, 68, 0.2)';
                countBadge.style.color = '#fca5a5';
                countBadge.style.borderColor = 'rgba(239, 68, 68, 0.4)';
            } else {
                countBadge.style.background = 'rgba(250, 204, 21, 0.2)';
                countBadge.style.color = '#fde047';
                countBadge.style.borderColor = 'rgba(250, 204, 21, 0.4)';
            }
        }
    }

    const badgeMap = {
        'telemetry': { text: '[H]', color: '#38bdf8', bg: 'rgba(56,189,248,0.12)', border: 'rgba(56,189,248,0.3)', type: 'Hardware' },
        'kopf':      { text: '[V]', color: '#f472b6', bg: 'rgba(244,114,182,0.14)', border: 'rgba(244,114,182,0.35)', type: 'Visual 3D' },
        'kabsch':    { text: '[V]', color: '#a855f7', bg: 'rgba(168,85,247,0.12)', border: 'rgba(168,85,247,0.3)', type: 'Visual 2D' },
        'vidstab':   { text: '[V]', color: '#a855f7', bg: 'rgba(168,85,247,0.12)', border: 'rgba(168,85,247,0.3)', type: 'Visual 2D' },
        'cinematic': { text: '[P]', color: '#34d399', bg: 'rgba(52,211,153,0.12)', border: 'rgba(52,211,153,0.3)', type: 'Path' },
        'horizon':   { text: '[D]', color: '#fbbf24', bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.3)', type: 'Directional' },
        'traveldir': { text: '[D]', color: '#fbbf24', bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.3)', type: 'Directional' }
    };

    if (skippedContainer) {
        if (skippedTransforms && skippedTransforms.length > 0) {
            const skippedItemsHtml = skippedTransforms.map(s => {
                const sb = badgeMap[s.id] || { text: '[D]', color: '#fbbf24', bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.3)', type: 'Directional' };
                const sBadge = `<span style="display:inline-block; font-family:monospace; font-size:11px; font-weight:700; color:${sb.color}; background:${sb.bg}; border:1px solid ${sb.border}; border-radius:4px; padding:1px 5px; margin-right:4px;" title="${sb.type}">${sb.text}</span>`;
                const sReportBtn = s.report_file ? `
                    <button type="button" class="btn-secondary btn-view-report" data-file="${escapeHtml(s.report_file)}" data-title="${escapeHtml(s.name)}" style="padding: 2px 7px; font-size: 11px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.4); color: #38bdf8; cursor: pointer;">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg> View Report
                    </button>
                ` : '';
                return `
                    <div style="display: flex; justify-content: space-between; align-items: center; padding: 4px 0; border-top: 1px solid rgba(56, 189, 248, 0.15);">
                        <div style="font-size: 12px; color: #f8fafc;">
                            ${sBadge} <b>${escapeHtml(s.name)}</b>: <span style="color: #94a3b8; font-size: 11px;">${escapeHtml(s.reason || 'Bypassed as neutral identity transform')}</span>
                        </div>
                        <div>${sReportBtn}</div>
                    </div>
                `;
            }).join('');

            skippedContainer.innerHTML = `
                <div style="display: flex; flex-direction: column; gap: 6px;">
                    <div style="display: flex; align-items: center; gap: 7px; font-weight: 600; color: #38bdf8;">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                        <span>Bypassed Neutral Stages (Preserving 100% Video Quality)</span>
                    </div>
                    <div style="font-size: 11.5px; line-height: 1.4; color: #bae6fd;">
                        The following active stabilization stage(s) evaluated to neutral <b>0° rotation (identity)</b> and were bypassed during intermediate generation to preserve pristine visual quality and avoid lossy intermediate rendering. No intermediate video tab was created.
                    </div>
                    <div style="margin-top: 2px;">
                        ${skippedItemsHtml}
                    </div>
                </div>
            `;
            skippedContainer.style.display = 'block';
        } else {
            skippedContainer.style.display = 'none';
            skippedContainer.innerHTML = '';
        }
    }

    listContainer.innerHTML = transforms.map(t => {
        const hasReport = Boolean(t.report_file);
        const hasGraph = Boolean(t.graph_file);
        const isUnstabilized = Boolean(t.is_unstabilized || (t.status_text && /no\s*\(|unstabilized|degraded|failed/i.test(t.status_text)));
        const isChecked = (t.selected !== undefined) ? (Boolean(t.selected) && !isUnstabilized) : (!isUnstabilized);

        const cardBg = isUnstabilized ? 'rgba(239, 68, 68, 0.08)' : 'rgba(255,255,255,0.04)';
        const cardBorder = isUnstabilized ? '1.5px solid rgba(239, 68, 68, 0.5)' : '1px solid rgba(255,255,255,0.1)';
        const cardShadow = isUnstabilized ? 'box-shadow: 0 0 12px rgba(239, 68, 68, 0.15);' : '';

        const warningBadge = isUnstabilized ? `
            <span style="display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 700; color: #f87171; background: rgba(239, 68, 68, 0.18); border: 1px solid rgba(239, 68, 68, 0.4); border-radius: 4px; padding: 1px 7px; margin-left: 6px;" title="${escapeHtml(t.status_text || 'Unstabilized')}">
                ⚠️ ${escapeHtml(t.status_text || 'NO (UNSTABILIZED)')} — Auto-excluded
            </span>
        ` : '';

        const warningNotice = isUnstabilized ? `
            <div style="font-size: 11.5px; color: #fca5a5; line-height: 1.4; padding-left: 27px; display: flex; align-items: center; gap: 5px;">
                <span>Diagnostic report indicates motion was degraded or unstabilized. Automatically unchecked to protect final render quality. You may re-check if desired.</span>
            </div>
        ` : '';

        const reportBtnStyle = isUnstabilized 
            ? 'background: rgba(239, 68, 68, 0.18); border: 1px solid rgba(239, 68, 68, 0.45); color: #fca5a5;'
            : 'background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.4); color: #38bdf8;';

        const b = badgeMap[t.id] || { text: '[T]', color: '#94a3b8', bg: 'rgba(148,163,184,0.12)', border: 'rgba(148,163,184,0.3)', type: 'Transform' };
        const badgeHtml = `<span style="display:inline-block; font-family:monospace; font-size:11px; font-weight:700; color:${b.color}; background:${b.bg}; border:1px solid ${b.border}; border-radius:4px; padding:1px 5px; margin-right:4px;" title="${b.type}">${b.text}</span>`;

        return `
        <div class="modal-transform-card" data-id="${escapeHtml(t.id)}" data-unstabilized="${isUnstabilized ? '1' : '0'}" style="background: ${cardBg}; border: ${cardBorder}; ${cardShadow} border-radius: 8px; padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; transition: border-color 0.2s, background 0.2s;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <label style="display: flex; align-items: center; gap: 10px; cursor: pointer; font-size: 13px; font-weight: 600; color: #f8fafc; margin: 0;">
                    <input type="checkbox" class="modal-transform-checkbox" data-id="${escapeHtml(t.id)}" ${isChecked ? 'checked' : ''} style="width: 17px; height: 17px; cursor: pointer; accent-color: ${isUnstabilized ? '#ef4444' : '#eab308'};">
                    <span style="display: inline-flex; align-items: center; flex-wrap: wrap; gap: 4px;">${badgeHtml}${escapeHtml(t.name)}${warningBadge}</span>
                </label>
                <div style="display: flex; gap: 6px;">
                    ${hasReport ? `
                        <button type="button" class="btn-secondary btn-view-report" data-file="${escapeHtml(t.report_file)}" data-title="${escapeHtml(t.name)}" style="padding: 3px 8px; font-size: 11px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; ${reportBtnStyle} cursor: pointer;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg> View Report
                        </button>
                    ` : ''}
                    ${hasGraph ? `
                        <button type="button" class="btn-secondary btn-view-graph" data-file="${escapeHtml(t.graph_file)}" data-title="${escapeHtml(t.name)}" style="padding: 3px 8px; font-size: 11px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; background: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.4); color: #c084fc; cursor: pointer;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></svg> View Graph
                        </button>
                    ` : ''}
                </div>
            </div>
            ${warningNotice}
        </div>
        `;
    }).join('');

    listContainer.querySelectorAll('.modal-transform-checkbox').forEach(cb => {
        cb.addEventListener('change', () => {
            updateModalActiveCount();
            const card = cb.closest('.modal-transform-card');
            if (card && card.dataset.unstabilized === '1') {
                if (cb.checked) {
                    card.style.border = '1.5px solid rgba(250, 204, 21, 0.55)';
                    card.style.background = 'rgba(250, 204, 21, 0.05)';
                    card.style.boxShadow = '0 0 10px rgba(250, 204, 21, 0.12)';
                } else {
                    card.style.border = '1.5px solid rgba(239, 68, 68, 0.5)';
                    card.style.background = 'rgba(239, 68, 68, 0.08)';
                    card.style.boxShadow = '0 0 12px rgba(239, 68, 68, 0.15)';
                }
            }
        });
    });

    updateModalActiveCount();

    modal.querySelectorAll('.btn-view-report').forEach(btn => {
        btn.addEventListener('click', () => {
            fetchAndShowReport(btn.dataset.file, btn.dataset.title);
        });
    });
    listContainer.querySelectorAll('.btn-view-graph').forEach(btn => {
        btn.addEventListener('click', () => {
            showGraphImage(btn.dataset.file, btn.dataset.title);
        });
    });

    modal.style.display = 'flex';
}

async function fetchAndShowReport(filePath, title) {
    const reportBox = document.getElementById('modalTransformReportBox');
    const reportTitle = document.getElementById('modalReportTitle');
    const reportContent = document.getElementById('modalReportContent');
    const graphContainer = document.getElementById('modalGraphContainer');
    const graphImg = document.getElementById('modalGraphImg');

    if (!reportBox || !reportContent) return;
    if (graphContainer) graphContainer.style.display = 'none';

    if (reportTitle) reportTitle.textContent = `Diagnostic Report: ${title}`;
    reportContent.style.display = 'block';
    reportContent.textContent = 'Loading report...';
    reportBox.style.display = 'flex';

    try {
        const res = await safeFetchJson(`api/v1/reports?file=${encodeURIComponent(filePath)}`);
        if (res && res.status === 'success' && res.content) {
            reportContent.textContent = res.content;
        } else {
            reportContent.textContent = res?.error || 'Could not load report content.';
        }
    } catch (e) {
        reportContent.textContent = 'Failed to load report: ' + e.message;
    }
}

function showGraphImage(filePath, title) {
    const reportBox = document.getElementById('modalTransformReportBox');
    const reportTitle = document.getElementById('modalReportTitle');
    const reportContent = document.getElementById('modalReportContent');
    const graphContainer = document.getElementById('modalGraphContainer');
    const graphImg = document.getElementById('modalGraphImg');

    if (!reportBox || !graphContainer || !graphImg) return;

    if (reportTitle) reportTitle.textContent = `Correction Graph: ${title}`;
    if (reportContent) reportContent.style.display = 'none';
    graphImg.src = filePath.replace(/\\/g, '/');
    graphContainer.style.display = 'block';
    reportBox.style.display = 'flex';
}

async function submitChosenTransforms() {
    const modal = document.getElementById('transformSelectModal');
    const checkboxes = document.querySelectorAll('.modal-transform-checkbox');
    const chosen = [];
    checkboxes.forEach(cb => {
        if (cb.checked && cb.dataset.id) {
            chosen.push(cb.dataset.id);
        }
    });

    window._transformsSubmitted = true;
    if (modal) modal.style.display = 'none';

    try {
        const currentJobId = getCurrentJobId();
        const formData = new FormData();
        /* action:resume_transforms → api/v1/jobs/{job_id}/transforms */
        if (currentJobId) formData.append('job_id', currentJobId);
        formData.append('chosen_transforms', JSON.stringify(chosen));

        const resumeUrl = currentJobId ? `api/v1/jobs/${encodeURIComponent(currentJobId)}/transforms` : 'api/v1/jobs/_/transforms';
        const res = await fetch(resumeUrl, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if (typeof window !== 'undefined' && typeof window.checkStatus === 'function') {
            setTimeout(window.checkStatus, 300);
        }
    } catch (e) {
        console.error('Failed to submit chosen transforms', e);
        window._transformsSubmitted = false;
    }
}

window.launchHorizonEditor = launchHorizonEditor;
window.resumePipelineAfterCheckpoints = resumePipelineAfterCheckpoints;
window.showTransformSelectModal = showTransformSelectModal;
window.submitChosenTransforms = submitChosenTransforms;

export {
    showCheckpointPauseModal,
    launchHorizonEditor,
    resumePipelineAfterCheckpoints,
    showTransformSelectModal,
    submitChosenTransforms
};
