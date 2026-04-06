/**
 * Valoboros — Validation tab.
 *
 * Upload model ZIPs, view validation status, read reports in-browser.
 */

let pollTimer = null;
let currentPage = null;

export function initValidation({ ws, state }) {
    const page = document.createElement('div');
    page.id = 'page-validation';
    page.className = 'page';
    page.innerHTML = `
        <div class="validation-layout">
            <section class="upload-section">
                <h2>Upload Model for Validation</h2>
                <div class="upload-zone" id="val-drop-zone">
                    <div class="upload-icon">
                        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                             stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                            <polyline points="17 8 12 3 7 8"/>
                            <line x1="12" y1="3" x2="12" y2="15"/>
                        </svg>
                    </div>
                    <p>Drag &amp; drop a model <strong>.zip</strong> here</p>
                    <p class="upload-hint">or click to browse</p>
                    <input type="file" id="val-file-input" accept=".zip" style="display:none">
                </div>
                <textarea id="val-task" rows="3"
                    placeholder="Describe what this model does (e.g., 'Predict early repayment rate for consumer loans')"></textarea>
                <div class="upload-actions">
                    <button id="val-upload-btn" class="btn-primary" disabled>Upload &amp; Validate</button>
                    <span id="val-upload-status"></span>
                </div>
            </section>

            <section class="validations-section">
                <h2>Validations</h2>
                <div id="val-list-empty" class="empty-state">No validations yet. Upload a model to get started.</div>
                <table id="val-table" style="display:none">
                    <thead>
                        <tr>
                            <th>Bundle</th>
                            <th>Task</th>
                            <th>Status</th>
                            <th>Verdict</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="val-table-body"></tbody>
                </table>
            </section>

            <section class="report-section" id="val-report-section" style="display:none">
                <div class="report-header">
                    <h2>Validation Report</h2>
                    <button id="val-report-close" class="btn-small">Close</button>
                </div>
                <pre id="val-report-content" class="report-viewer"></pre>
            </section>
        </div>
    `;

    document.getElementById('content').appendChild(page);
    currentPage = page;

    // --- Elements ---
    const dropZone = page.querySelector('#val-drop-zone');
    const fileInput = page.querySelector('#val-file-input');
    const taskInput = page.querySelector('#val-task');
    const uploadBtn = page.querySelector('#val-upload-btn');
    const uploadStatus = page.querySelector('#val-upload-status');
    const tableEl = page.querySelector('#val-table');
    const tbodyEl = page.querySelector('#val-table-body');
    const emptyEl = page.querySelector('#val-list-empty');
    const reportSection = page.querySelector('#val-report-section');
    const reportContent = page.querySelector('#val-report-content');
    const reportClose = page.querySelector('#val-report-close');

    let selectedFile = null;

    // --- Drop zone ---
    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        const files = e.dataTransfer.files;
        if (files.length > 0) selectFile(files[0]);
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) selectFile(fileInput.files[0]);
    });

    function selectFile(file) {
        if (!file.name.toLowerCase().endsWith('.zip')) {
            uploadStatus.textContent = 'Only .zip files are accepted.';
            uploadStatus.className = 'status-error';
            selectedFile = null;
            uploadBtn.disabled = true;
            return;
        }
        selectedFile = file;
        uploadBtn.disabled = false;
        uploadStatus.textContent = `Selected: ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
        uploadStatus.className = '';
    }

    // --- Upload ---
    uploadBtn.addEventListener('click', async () => {
        if (!selectedFile) return;
        uploadBtn.disabled = true;
        uploadStatus.textContent = 'Uploading...';
        uploadStatus.className = 'status-validating';

        const form = new FormData();
        form.append('file', selectedFile);
        form.append('task', taskInput.value || '');

        try {
            const resp = await fetch('/api/validation/upload', { method: 'POST', body: form });
            const data = await resp.json();
            if (data.ok) {
                uploadStatus.textContent = `Uploaded! Starting validation...`;
                uploadStatus.className = 'status-validating';
                selectedFile = null;
                fileInput.value = '';
                taskInput.value = '';
                refreshList();

                // Trigger validation pipeline automatically
                try {
                    const runResp = await fetch('/api/validation/run', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ bundle_id: data.bundle_id }),
                    });
                    const runData = await runResp.json();
                    if (runData.ok) {
                        uploadStatus.textContent = `Validation complete: ${runData.verdict || 'done'}`;
                        uploadStatus.className = 'status-completed';
                    } else {
                        uploadStatus.textContent = `Validation error: ${runData.error || 'unknown'}`;
                        uploadStatus.className = 'status-error';
                    }
                } catch (runErr) {
                    uploadStatus.textContent = `Upload OK, but validation failed to start: ${runErr.message}`;
                    uploadStatus.className = 'status-error';
                }
                uploadBtn.disabled = false;
                refreshList();
            } else {
                uploadStatus.textContent = `Error: ${data.error}`;
                uploadStatus.className = 'status-error';
                uploadBtn.disabled = false;
            }
        } catch (err) {
            uploadStatus.textContent = `Upload failed: ${err.message}`;
            uploadStatus.className = 'status-error';
            uploadBtn.disabled = false;
        }
    });

    // --- Validation list ---
    async function refreshList() {
        try {
            const resp = await fetch('/api/validation/list');
            const data = await resp.json();
            const items = Array.isArray(data) ? data : [];

            if (items.length === 0) {
                tableEl.style.display = 'none';
                emptyEl.style.display = '';
                return;
            }

            tableEl.style.display = '';
            emptyEl.style.display = 'none';
            tbodyEl.innerHTML = '';

            for (const item of items) {
                const tr = document.createElement('tr');
                const statusClass = `status-${item.status || 'pending'}`;
                const verdictClass = item.verdict === 'approved' ? 'verdict-approved'
                    : item.verdict === 'rejected' ? 'verdict-rejected'
                    : item.verdict === 'conditional' ? 'verdict-conditional'
                    : '';

                tr.innerHTML = `
                    <td class="bundle-id">${item.bundle_id || '?'}</td>
                    <td class="task-cell">${escapeHtml(item.task || '').substring(0, 60)}</td>
                    <td><span class="status-badge ${statusClass}">${item.status || 'pending'}</span></td>
                    <td><span class="${verdictClass}">${item.verdict || '-'}</span></td>
                    <td class="actions-cell"></td>
                `;

                const actionsCell = tr.querySelector('.actions-cell');

                if (item.status === 'completed') {
                    const reportBtn = document.createElement('button');
                    reportBtn.className = 'btn-small';
                    reportBtn.textContent = 'View Report';
                    reportBtn.addEventListener('click', () => viewReport(item.bundle_id));
                    actionsCell.appendChild(reportBtn);
                } else if (item.status === 'pending') {
                    const runBtn = document.createElement('button');
                    runBtn.className = 'btn-small btn-primary';
                    runBtn.textContent = 'Validate';
                    runBtn.addEventListener('click', () => runValidation(item.bundle_id, runBtn));
                    actionsCell.appendChild(runBtn);
                }

                // Download button — always available (downloads inferred, methodology, results, log)
                const dlBtn = document.createElement('button');
                dlBtn.className = 'btn-small';
                dlBtn.textContent = 'Download';
                dlBtn.addEventListener('click', () => {
                    window.open(`/api/validation/download?bundle_id=${item.bundle_id}`, '_blank');
                });
                actionsCell.appendChild(dlBtn);

                tbodyEl.appendChild(tr);
            }
        } catch (err) {
            console.error('Failed to refresh validation list:', err);
        }
    }

    // --- Run validation ---
    async function runValidation(bundleId, btn) {
        btn.disabled = true;
        btn.textContent = 'Running...';
        try {
            const resp = await fetch('/api/validation/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bundle_id: bundleId }),
            });
            const data = await resp.json();
            if (data.ok) {
                btn.textContent = data.verdict || 'Done';
            } else {
                btn.textContent = 'Error';
            }
            refreshList();
        } catch (err) {
            btn.textContent = 'Failed';
            console.error('Validation run failed:', err);
        }
    }

    // --- View report ---
    async function viewReport(bundleId) {
        try {
            const resp = await fetch(`/api/validation/report?bundle_id=${bundleId}&format=md`);
            if (!resp.ok) {
                reportContent.textContent = `Error: ${resp.statusText}`;
            } else {
                reportContent.textContent = await resp.text();
            }
            reportSection.style.display = '';
            reportSection.scrollIntoView({ behavior: 'smooth' });
        } catch (err) {
            reportContent.textContent = `Failed to load report: ${err.message}`;
            reportSection.style.display = '';
        }
    }

    reportClose.addEventListener('click', () => {
        reportSection.style.display = 'none';
    });

    // --- Polling ---
    function startPolling() {
        stopPolling();
        refreshList();
        pollTimer = setInterval(refreshList, 10000);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    // --- Page lifecycle ---
    // Listen for page show/hide via the nav system (uses .page.active class)
    window.addEventListener('ouro:page-shown', (e) => {
        if (e.detail.page === 'validation') {
            startPolling();
        } else {
            stopPolling();
        }
    });

    // --- Helpers ---
    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}
