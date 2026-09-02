"""
Build the static, browser-only version of the schedule generator into `docs/`.

GitHub Pages serves `docs/` on the main branch. The page runs the real
`schedule_backend.py` in the browser through Pyodide, so the scheduling engine
is never duplicated - this script only swaps the server calls for local ones:

    /generate             -> Pyodide, in the browser
    /save-schedule        -> localStorage
    /schedule-default-name-> localStorage
    /download-csv         -> a Blob download

Run it after changing templates/index.html or schedule_backend.py:

    python build_static.py
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TEMPLATE = ROOT / "templates" / "index.html"
ENGINE = ROOT / "schedule_backend.py"
OUT_DIR = ROOT / "docs"

PYODIDE_VERSION = "0.26.4"


def substitute(pattern, replacement, text, label, flags=re.DOTALL):
    """Apply exactly one replacement, failing loudly if the template moved."""
    # A lambda keeps the replacement literal, so backslashes in the injected
    # JavaScript are not read as regex escapes.
    new_text, count = re.subn(pattern, lambda _match: replacement, text, flags=flags)
    if count != 1:
        raise SystemExit(
            f"build_static.py: expected 1 match for {label!r}, found {count}. "
            f"templates/index.html has changed - update the pattern."
        )
    return new_text


EXTRA_CSS = """
        /* ---- static build additions ---- */
        .nav-button {
            font: inherit;
            cursor: pointer;
            background: none;
            border: 1px solid transparent;
        }

        .browser-note,
        .engine-status {
            padding: 13px 17px;
            border-radius: var(--radius-sm);
            border: 1px solid var(--line);
            border-left: 3px solid var(--primary);
            background: var(--primary-soft);
            color: var(--primary-soft-text);
            font-size: 0.87rem;
            line-height: 1.5;
            margin-bottom: 18px;
        }

        .browser-note strong {
            font-weight: 650;
        }

        .engine-status {
            display: none;
        }

        .saved-panel {
            display: none;
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            padding: 22px 24px;
            margin-bottom: 18px;
            box-shadow: var(--shadow-1);
        }

        .saved-panel h2 {
            font-family: var(--font-display);
            font-size: 1.16rem;
            font-weight: 650;
            letter-spacing: -0.015em;
            margin-bottom: 4px;
        }

        .saved-row {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
            padding: 13px 0;
            border-top: 1px solid var(--line);
        }

        .saved-row .saved-name {
            flex: 1 1 200px;
            font-weight: 600;
        }

        .saved-row .saved-date {
            color: var(--muted);
            font-size: 0.8rem;
            font-variant-numeric: tabular-nums;
        }

        .saved-empty {
            color: var(--muted);
            padding-top: 14px;
            font-size: 0.89rem;
        }
"""

BROWSER_RUNTIME = r"""
        // ==============================================================
        // Browser-only runtime (static build - no server, no account).
        // The scheduling engine below is the real schedule_backend.py,
        // executed in this tab by Pyodide.
        // ==============================================================
        const SAVED_KEY = 'class-schedule-generator/saved';
        let pyodidePromise = null;

        function setEngineStatus(message) {
            const el = document.getElementById('engineStatus');
            if (!el) return;
            el.textContent = message;
            el.style.display = message ? 'block' : 'none';
        }

        async function ensureEngine() {
            if (!pyodidePromise) {
                pyodidePromise = (async () => {
                    setEngineStatus('Loading the scheduling engine. This happens once, then it is cached.');
                    const pyodide = await loadPyodide();
                    const source = await (await fetch('schedule_backend.py')).text();
                    pyodide.FS.writeFile('schedule_backend.py', source);
                    pyodide.runPython(`
import json
from schedule_backend import ScheduleGenerator, ScheduleConfigError

def _generate(config_json):
    try:
        generator = ScheduleGenerator(json.loads(config_json))
        generator.generate_schedule()
        return json.dumps({
            'success': True,
            'csv': generator.export_to_csv_string(),
            'summary': generator.get_summary(),
        })
    except ScheduleConfigError as error:
        return json.dumps({'success': False, 'error': str(error)})
    except Exception as error:
        return json.dumps({'success': False, 'error': f'Unexpected error: {error}'})
`);
                    setEngineStatus('');
                    return pyodide;
                })().catch(error => {
                    pyodidePromise = null;
                    setEngineStatus('');
                    throw new Error('Could not load the scheduling engine. Check your connection and reload.');
                });
            }
            return pyodidePromise;
        }

        async function generateLocally(config) {
            const pyodide = await ensureEngine();
            const generate = pyodide.globals.get('_generate');
            try {
                return JSON.parse(generate(JSON.stringify(config)));
            } finally {
                generate.destroy();
            }
        }

        // ---- saved schedules, kept in this browser ----
        function readSaved() {
            try {
                const raw = localStorage.getItem(SAVED_KEY);
                const parsed = raw ? JSON.parse(raw) : [];
                return Array.isArray(parsed) ? parsed : [];
            } catch (_error) {
                return [];
            }
        }

        function writeSaved(list) {
            try {
                localStorage.setItem(SAVED_KEY, JSON.stringify(list));
                return true;
            } catch (_error) {
                return false;
            }
        }

        function saveScheduleLocally(name, csv, summary, config) {
            const list = readSaved();
            const entry = {
                id: `s${list.length ? Math.max(...list.map(s => Number(s.id.slice(1)) || 0)) + 1 : 1}`,
                name: name || `Schedule ${list.length + 1}`,
                created_at: new Date().toLocaleString(),
                csv: csv,
                summary: summary,
                config: config
            };
            list.push(entry);
            if (!writeSaved(list)) {
                throw new Error('Your browser storage is full. Delete a saved schedule and try again.');
            }
            return entry;
        }

        function deleteSaved(id) {
            if (!confirm('Delete this saved schedule? This cannot be undone.')) return;
            writeSaved(readSaved().filter(s => s.id !== id));
            renderSavedList();
        }

        function viewSaved(id) {
            const entry = readSaved().find(s => s.id === id);
            if (!entry) return;
            generatedCSV = entry.csv;
            generatedSummary = entry.summary;
            generatedConfig = entry.config;
            displayResults(entry.csv, entry.summary);
            document.getElementById('scheduleName').value = entry.name;
            setSaveScheduleStatus(`Showing "${entry.name}".`);
        }

        function downloadSaved(id) {
            const entry = readSaved().find(s => s.id === id);
            if (entry) downloadCsvText(entry.csv, `${entry.name.replace(/\s+/g, '_')}.csv`);
        }

        function downloadCsvText(csvText, filename) {
            const blob = new Blob([csvText], {type: 'text/csv;charset=utf-8;'});
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            URL.revokeObjectURL(url);
            link.remove();
        }

        function renderSavedList() {
            const container = document.getElementById('savedList');
            if (!container) return;
            const list = readSaved();
            if (!list.length) {
                container.innerHTML = '<p class="saved-empty">Nothing saved yet. Generate a schedule and press Save Schedule.</p>';
                return;
            }
            container.innerHTML = list.slice().reverse().map(entry => `
                <div class="saved-row">
                    <span class="saved-name">${escapeHtml(entry.name)}</span>
                    <span class="saved-date">${escapeHtml(entry.created_at)}</span>
                    <button type="button" class="add-btn" onclick="viewSaved('${entry.id}')">View</button>
                    <button type="button" class="add-btn" onclick="downloadSaved('${entry.id}')">CSV</button>
                    <button type="button" class="remove-btn" onclick="deleteSaved('${entry.id}')">Delete</button>
                </div>
            `).join('');
        }

        function openSavedPanel() {
            const panel = document.getElementById('savedPanel');
            if (!panel) return;
            const showing = panel.style.display === 'block';
            panel.style.display = showing ? 'none' : 'block';
            if (!showing) {
                renderSavedList();
                panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
            }
        }

        // ---- page furniture that the Flask version got from the server ----
        function injectStaticChrome() {
            const container = document.querySelector('.container');
            const form = document.getElementById('scheduleForm');
            if (!container || !form) return;

            const note = document.createElement('div');
            note.className = 'browser-note';
            note.innerHTML = 'No account needed. Everything runs in this browser, and saved schedules stay on this device — clearing your browser data removes them, and they will not appear on another computer. Use <strong>Download CSV</strong> to keep a copy.';
            container.insertBefore(note, form);

            const status = document.createElement('div');
            status.className = 'engine-status';
            status.id = 'engineStatus';
            container.insertBefore(status, form);

            const panel = document.createElement('div');
            panel.className = 'saved-panel';
            panel.id = 'savedPanel';
            panel.innerHTML = '<h2>Saved Schedules</h2><p class="help-text">Stored in this browser.</p><div id="savedList"></div>';
            container.insertBefore(panel, form);
        }

        document.addEventListener('DOMContentLoaded', () => {
            injectStaticChrome();
            // Warm the engine up so the first Generate is not a long wait.
            ensureEngine().catch(() => { /* surfaced when the user generates */ });
        });
"""


def main():
    if not TEMPLATE.exists():
        raise SystemExit(f"build_static.py: {TEMPLATE} not found")
    if not ENGINE.exists():
        raise SystemExit(f"build_static.py: {ENGINE} not found")

    html = TEMPLATE.read_text(encoding="utf-8")

    # 1. Replace the account nav with a local Saved Schedules toggle.
    html = substitute(
        r'\s*<span class="account-chip">\{\{ username \}\}</span>\s*'
        r'<a class="nav-link" href="\{\{ url_for\(\'saved_schedules\'\) \}\}">Saved Schedules</a>\s*'
        r'<a class="nav-link" href="\{\{ url_for\(\'logout\'\) \}\}">Sign Out</a>',
        '\n            <button type="button" class="nav-link nav-button" '
        'onclick="openSavedPanel()">Saved Schedules</button>',
        html,
        "account nav",
    )

    # 2. Generate in the browser instead of POSTing to Flask.
    html = substitute(
        r"const response = await fetch\('/generate'.*?const data = await response\.json\(\);",
        "const data = await generateLocally(config);",
        html,
        "/generate call",
    )

    # 3. Next default name comes from localStorage.
    html = substitute(
        r"async function loadDefaultScheduleName\(\) \{.*?\n        \}",
        "async function loadDefaultScheduleName() {\n"
        "            const nameInput = document.getElementById('scheduleName');\n"
        "            if (nameInput) nameInput.value = `Schedule ${readSaved().length + 1}`;\n"
        "        }",
        html,
        "loadDefaultScheduleName",
    )

    # 4. Saving writes to localStorage.
    html = substitute(
        r"                const response = await fetch\('/save-schedule'.*?"
        r"await loadDefaultScheduleName\(\);",
        "                const entry = saveScheduleLocally(\n"
        "                    payload.name, payload.csv, payload.summary, payload.config\n"
        "                );\n\n"
        "                nameInput.value = entry.name;\n"
        "                setSaveScheduleStatus(`Saved as \"${entry.name}\" in this browser.`, 'success');\n"
        "                renderSavedList();\n"
        "                await loadDefaultScheduleName();",
        html,
        "/save-schedule call",
    )

    # 5. CSV download happens client side.
    html = substitute(
        r"function downloadCSV\(\) \{.*?\n        \}",
        "function downloadCSV() {\n"
        "            if (!generatedCSV) return;\n"
        "            downloadCsvText(generatedCSV, 'class_schedule.csv');\n"
        "        }",
        html,
        "downloadCSV",
    )

    # 6. Styling for the pieces the static build adds.
    html = substitute(r"\n    </style>", EXTRA_CSS + "    </style>", html, "style block")

    # 7. Pyodide, then the browser runtime at the end of the script block.
    html = substitute(
        r"\n</head>",
        f'\n    <script src="https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/pyodide.js"></script>\n</head>',
        html,
        "head close",
    )
    html = substitute(
        r"\n    </script>\s*</body>",
        BROWSER_RUNTIME + "    </script>\n</body>",
        html,
        "script close",
    )

    OUT_DIR.mkdir(exist_ok=True)
    # Always write LF, so the build is byte-identical on Windows and Linux and
    # CI can meaningfully diff it.
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8", newline="\n")
    shutil.copy2(ENGINE, OUT_DIR / "schedule_backend.py")
    # Stop GitHub Pages running the output through Jekyll.
    (OUT_DIR / ".nojekyll").write_text("", encoding="utf-8", newline="\n")

    print(f"Built {OUT_DIR / 'index.html'} ({len(html):,} bytes)")
    print(f"Copied {ENGINE.name} -> {OUT_DIR / 'schedule_backend.py'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
