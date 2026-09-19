/**
 * System Health & Diagnostic Suite.
 * Background status polling and detail modal for system dependencies and storage.
 */

// ── System Health & Diagnostic Suite ─────────────────────────────────────────

async function fetchSystemHealth() {
    const healthDot = document.getElementById('health-dot');
    const healthLabel = document.getElementById('health-label');
    const healthBtn = document.getElementById('btn-health-check');
    if (!healthDot || !healthLabel) return;

    healthDot.style.background = '#94a3b8';
    healthLabel.textContent = 'Checking...';

    try {
        const resp = await fetch('/api/v1/system/health');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        renderSystemHealthUI(data);
    } catch (err) {
        console.warn('Failed to fetch system health:', err);
        healthDot.style.background = '#f87171';
        healthLabel.textContent = 'Check Failed';
        if (healthBtn) healthBtn.title = 'Failed to connect to backend health diagnostic.';
    }
}

function renderSystemHealthUI(data) {
    const healthDot = document.getElementById('health-dot');
    const healthLabel = document.getElementById('health-label');
    const healthBtn = document.getElementById('btn-health-check');
    const summaryBox = document.getElementById('health-overall-summary');
    const container = document.getElementById('health-items-container');
    const recBox = document.getElementById('health-recommendations-box');
    const recList = document.getElementById('health-recommendations-list');

    if (!healthDot || !healthLabel || !container) return;

    // 1. Update Header Badge
    if (data.status === 'ready') {
        healthDot.style.background = '#34d399';
        healthLabel.textContent = 'System Ready';
        if (healthBtn) {
            healthBtn.style.borderColor = 'rgba(52, 211, 153, 0.4)';
            healthBtn.title = 'All dependencies and disk space are healthy. Click for details.';
        }
    } else if (data.status === 'warning') {
        healthDot.style.background = '#fbbf24';
        healthLabel.textContent = 'System Warning';
        if (healthBtn) {
            healthBtn.style.borderColor = 'rgba(251, 191, 36, 0.5)';
            healthBtn.title = 'System operational with warnings. Click for details.';
        }
    } else {
        healthDot.style.background = '#f87171';
        healthLabel.textContent = 'Missing Tools';
        if (healthBtn) {
            healthBtn.style.borderColor = 'rgba(248, 113, 113, 0.6)';
            healthBtn.title = 'Required system tools are missing! Click to view install commands.';
        }
    }

    // 2. Summary in modal
    if (summaryBox) {
        if (data.status === 'ready') {
            summaryBox.style.background = 'rgba(16, 185, 129, 0.15)';
            summaryBox.style.border = '1px solid rgba(16, 185, 129, 0.35)';
            summaryBox.style.color = '#6ee7b7';
            summaryBox.innerHTML = '<span>✅</span> All core dependencies are installed and storage is healthy.';
        } else if (data.status === 'warning') {
            summaryBox.style.background = 'rgba(245, 158, 11, 0.15)';
            summaryBox.style.border = '1px solid rgba(245, 158, 11, 0.35)';
            summaryBox.style.color = '#fcd34d';
            summaryBox.innerHTML = '<span>⚠️</span> System is usable, but some capabilities or storage need attention.';
        } else {
            summaryBox.style.background = 'rgba(239, 68, 68, 0.15)';
            summaryBox.style.border = '1px solid rgba(239, 68, 68, 0.4)';
            summaryBox.style.color = '#fca5a5';
            summaryBox.innerHTML = '<span>❌</span> Essential components are missing. Complete remediation below.';
        }
    }

    // 3. Item rows
    const items = [
        { name: 'Python Runtime', data: data.python, icon: '🐍' },
        { name: 'FFmpeg Video Encoder', data: data.ffmpeg, icon: '🎬' },
        { name: 'FFprobe Metadata Inspector', data: data.ffprobe, icon: '🔍' },
        { name: 'ExifTool Tag Injector', data: data.exiftool, icon: '🏷️' },
        { name: 'Storage / Disk Space', data: data.disk, icon: '💾' }
    ];

    container.innerHTML = items.map(item => {
        let badgeBg = 'rgba(16, 185, 129, 0.15)';
        let badgeColor = '#34d399';
        let badgeBorder = 'rgba(16, 185, 129, 0.3)';
        let badgeText = 'OK';

        if (item.data.status === 'warning') {
            badgeBg = 'rgba(245, 158, 11, 0.15)';
            badgeColor = '#fbbf24';
            badgeBorder = 'rgba(245, 158, 11, 0.3)';
            badgeText = 'Warning';
        } else if (item.data.status === 'error') {
            badgeBg = 'rgba(239, 68, 68, 0.15)';
            badgeColor = '#f87171';
            badgeBorder = 'rgba(239, 68, 68, 0.3)';
            badgeText = 'Missing';
        }

        return `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: rgba(30, 41, 59, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 6px;">
                <div style="display: flex; flex-direction: column; gap: 2px;">
                    <div style="display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: #f1f5f9;">
                        <span>${item.icon}</span>
                        <span>${item.name}</span>
                    </div>
                    <span style="font-size: 11px; color: #94a3b8;">${item.data.message || (item.data.version ? 'v' + item.data.version : '')}</span>
                </div>
                <span style="font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; background: ${badgeBg}; color: ${badgeColor}; border: 1px solid ${badgeBorder};">
                    ${badgeText}
                </span>
            </div>
        `;
    }).join('');

    // 4. Recommendations Box
    if (recBox && recList) {
        if (data.recommendations && data.recommendations.length > 0) {
            recBox.style.display = 'block';
            recList.textContent = data.recommendations.join('\n');
        } else {
            recBox.style.display = 'none';
            recList.textContent = '';
        }
    }
}

function initSystemHealth() {
    const healthBtn = document.getElementById('btn-health-check');
    const healthModal = document.getElementById('modal-system-health');
    const btnClose = document.getElementById('btn-close-health-modal');
    const btnOk = document.getElementById('btn-ok-health-modal');
    const btnRecheck = document.getElementById('btn-recheck-health');

    if (healthBtn && healthModal) {
        healthBtn.addEventListener('click', () => {
            healthModal.style.display = 'flex';
        });
    }

    const closeModal = () => {
        if (healthModal) healthModal.style.display = 'none';
    };

    if (btnClose) btnClose.addEventListener('click', closeModal);
    if (btnOk) btnOk.addEventListener('click', closeModal);
    if (healthModal) {
        healthModal.addEventListener('click', (e) => {
            if (e.target === healthModal) closeModal();
        });
    }

    if (btnRecheck) {
        btnRecheck.addEventListener('click', () => {
            fetchSystemHealth();
        });
    }

    // Trigger initial health check
    fetchSystemHealth();
}


export { fetchSystemHealth, renderSystemHealthUI, initSystemHealth };

// Global window attachments for backward compatibility
window.initSystemHealth = initSystemHealth;
window.fetchSystemHealth = fetchSystemHealth;
