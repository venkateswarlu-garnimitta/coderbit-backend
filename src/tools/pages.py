PREVIEW_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline' http://localhost:* http://127.0.0.1:* https://*; frame-src *;">
    <title>Preview</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,-apple-system,sans-serif;font-size:13px;}
        html,body{width:100%;height:100%;overflow:hidden;background:#fff;}
        #addr-bar{display:flex;gap:6px;padding:6px 8px;border-bottom:1px solid #ddd;align-items:center;flex-shrink:0;}
        #url-input{flex:1;padding:4px 8px;border:1px solid #ccc;border-radius:4px;outline:none;}
        #url-input:focus{border-color:#007acc;}
        #url-input.invalid{border-color:#e51400;}
        #go-btn{padding:4px 14px;background:#007acc;color:#fff;border:none;border-radius:4px;cursor:pointer;}
        #go-btn:hover{background:#005a9e;}
        #addr-error{color:#e51400;font-size:12px;white-space:nowrap;}
        #content-wrap{width:100%;height:calc(100vh - 37px);display:flex;}
        iframe{width:100%;height:100%;border:none;display:block;}
    </style>
</head>
<body>
    <div id="addr-bar">
        <input id="url-input" type="text" placeholder="http://127.0.0.1:8000">
        <button id="go-btn">Go</button>
        <span id="addr-error"></span>
    </div>
    <div id="content-wrap">
        <iframe id="preview-frame"></iframe>
    </div>
    <script>
        (function(){
            var PROXY_BASE = "{proxy_base}";
            if (!PROXY_BASE) PROXY_BASE = window.location.origin;
            var INTERVIEW_PATH = "{interview_path}";
            var BARE_LOCAL = /^https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?(\/.*)?$/;
            var input = document.getElementById('url-input');
            var goBtn = document.getElementById('go-btn');
            var errEl = document.getElementById('addr-error');
            var frame = document.getElementById('preview-frame');
            function resolveProxyUrl(raw) {
                var m = raw.match(BARE_LOCAL);
                if (m) {
                    var portNum = (m[2] || ':80').substring(1);
                    var p = m[3] || '/';
                    return PROXY_BASE + INTERVIEW_PATH + '/proxy/' + portNum + p;
                }
                return null;
            }
            function navigate() {
                var raw = input.value.trim();
                if (!raw) { return; }
                var resolved = resolveProxyUrl(raw);
                if (!resolved) {
                    input.classList.add('invalid');
                    errEl.textContent = 'Only localhost URLs are allowed.';
                    return;
                }
                input.classList.remove('invalid');
                errEl.textContent = '';
                frame.src = resolved.replace(/\/+\//, '/');
            }
            var urlParam = new URLSearchParams(window.location.search).get('url');
            if (urlParam) {
                input.value = urlParam;
                navigate();
            }
            goBtn.addEventListener('click', navigate);
            input.addEventListener('keydown', function(e) { if (e.key === 'Enter') navigate(); });
            input.addEventListener('input', function() { input.classList.remove('invalid'); errEl.textContent = ''; });
        })();
    </script>
</body>
</html>"""

API_TESTER_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:* http://127.0.0.1:* https://*;">
    <title>API Tester</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,-apple-ui,sans-serif;font-size:13px;}
        html,body{width:100%;height:100%;background:#f5f5f5;overflow-y:auto;}
        #app{padding:12px;display:flex;flex-direction:column;gap:10px;max-width:100%;}
        #request-row{display:flex;gap:6px;align-items:center;}
        #method{width:90px;flex-shrink:0;padding:4px 6px;border:1px solid #ccc;border-radius:4px;background:#fff;}
        #url-input{flex:1;padding:4px 8px;border:1px solid #ccc;border-radius:4px;outline:none;}
        #url-input:focus{border-color:#007acc;}
        #url-input.invalid{border-color:#e51400;}
        #send-btn{padding:4px 16px;border:none;border-radius:4px;cursor:pointer;font-weight:600;color:#fff;}
        #send-btn.get{background:#007acc;}
        #send-btn.post{background:#2ea44f;}
        #send-btn.put{background:#ca8a04;}
        #send-btn.delete{background:#e51400;}
        #send-btn.patch{background:#8250df;}
        #send-btn:disabled{opacity:.5;cursor:not-allowed;}
        #addr-error{color:#e51400;font-size:12px;display:block;min-height:1.2em;}
        section{border:1px solid #ddd;border-radius:6px;background:#fff;overflow:hidden;}
        section h3{padding:8px 12px;background:#f0f0f0;font-size:12px;font-weight:600;border-bottom:1px solid #ddd;}
        .section-body{padding:8px 12px;}
        #headers-table{width:100%;border-collapse:collapse;}
        #headers-table td{padding:3px 4px;}
        #headers-table input{width:100%;padding:3px 6px;border:1px solid #ddd;border-radius:3px;outline:none;}
        #headers-table input:focus{border-color:#007acc;}
        .header-row-remove{background:none;border:none;color:#999;cursor:pointer;padding:2px 6px;font-size:14px;}
        .header-row-remove:hover{color:#e51400;}
        .add-header-btn{background:none;border:1px dashed #ccc;border-radius:4px;padding:4px 10px;cursor:pointer;color:#666;font-size:12px;margin-top:4px;width:100%;}
        .add-header-btn:hover{border-color:#007acc;color:#007acc;}
        #body-ta{width:100%;min-height:100px;padding:8px;border:1px solid #ddd;border-radius:4px;font-family:monospace;font-size:12px;resize:vertical;outline:none;}
        #body-ta:focus{border-color:#007acc;}
        #response-section .section-body{padding:0;}
        #response-meta{display:flex;gap:12px;padding:8px 12px;border-bottom:1px solid #eee;align-items:center;flex-wrap:wrap;}
        .status-badge{padding:2px 10px;border-radius:10px;font-weight:600;font-size:12px;}
        .status-2xx{background:#dafbe1;color:#1a7f37;}
        .status-3xx{background:#ddf4ff;color:#0969da;}
        .status-4xx{background:#ffebe9;color:#e51400;}
        .status-5xx{background:#fff1e5;color:#bc4c00;}
        .status-pending{background:#f0f0f0;color:#666;}
        #response-timing{color:#666;font-size:12px;}
        #response-headers-toggle{cursor:pointer;color:#0969da;font-size:12px;background:none;border:none;padding:0;}
        #response-headers-body{display:none;padding:8px 12px;border-top:1px solid #eee;font-family:monospace;font-size:12px;white-space:pre-wrap;max-height:200px;overflow-y:auto;background:#f8f8f8;}
        #response-body{padding:12px;font-family:monospace;font-size:12px;white-space:pre-wrap;overflow-x:auto;max-height:400px;overflow-y:auto;}
        .hidden{display:none!important;}
    </style>
</head>
<body>
    <div id="app">
        <div id="request-row">
            <select id="method">
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="PATCH">PATCH</option>
                <option value="DELETE">DELETE</option>
            </select>
            <input id="url-input" type="text" placeholder="http://localhost:3000/path">
            <button id="send-btn" class="get">Send</button>
        </div>
        <span id="addr-error"></span>

        <section id="headers-section">
            <h3>Headers</h3>
            <div class="section-body">
                <table id="headers-table">
                    <tbody id="headers-tbody">
                        <tr class="header-row" data-idx="0">
                            <td><input class="header-key" placeholder="Key"></td>
                            <td><input class="header-value" placeholder="Value"></td>
                            <td><button class="header-row-remove" onclick="removeHeaderRow(this)">\u00d7</button></td>
                        </tr>
                    </tbody>
                </table>
                <button class="add-header-btn" onclick="addHeaderRow()">+ Add header</button>
            </div>
        </section>

        <section id="body-section">
            <h3>Body <span id="body-label" style="font-weight:400;color:#888;">(none)</span></h3>
            <div class="section-body">
                <textarea id="body-ta" placeholder='{"key": "value"}'></textarea>
            </div>
        </section>

        <section id="response-section">
            <h3>Response</h3>
            <div id="response-meta">
                <span class="status-badge status-pending" id="status-badge">Ready</span>
                <span id="response-timing"></span>
                <button id="response-headers-toggle" class="hidden" onclick="toggleRespHeaders()">Headers \u25bc</button>
            </div>
            <pre id="response-headers-body"></pre>
            <pre id="response-body"></pre>
        </section>
    </div>

    <script>
        (function(){
            var PROXY_BASE = "{proxy_base}";
            if (!PROXY_BASE) PROXY_BASE = window.location.origin;
            var INTERVIEW_PATH = "{interview_path}";
            var API_TOKEN = "{api_token}";
            var BARE_LOCAL = /^https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?(\/.*)?$/;
            var method = document.getElementById('method');
            var urlInput = document.getElementById('url-input');
            var sendBtn = document.getElementById('send-btn');
            var errEl = document.getElementById('addr-error');
            var bodySection = document.getElementById('body-section');
            var bodyLabel = document.getElementById('body-label');
            var bodyTa = document.getElementById('body-ta');
            var statusBadge = document.getElementById('status-badge');
            var timingEl = document.getElementById('response-timing');
            var respBody = document.getElementById('response-body');
            var respHeadersBody = document.getElementById('response-headers-body');
            var respHeadersToggle = document.getElementById('response-headers-toggle');

            function updateMethodUI() {
                var m = method.value;
                sendBtn.className = m.toLowerCase();
                var showBody = m === 'POST' || m === 'PUT' || m === 'PATCH';
                bodySection.style.display = showBody ? '' : 'none';
                bodyLabel.textContent = showBody ? '(raw JSON)' : '(none)';
            }
            method.addEventListener('change', updateMethodUI);
            updateMethodUI();

            function addHeaderRow() {
                var tbody = document.getElementById('headers-tbody');
                var row = document.createElement('tr');
                row.className = 'header-row';
                row.innerHTML = '<td><input class="header-key" placeholder="Key"></td><td><input class="header-value" placeholder="Value"></td><td><button class="header-row-remove" onclick="removeHeaderRow(this)">\u00d7</button></td>';
                tbody.appendChild(row);
            }
            window.addHeaderRow = addHeaderRow;
            window.removeHeaderRow = function(btn) {
                var row = btn.closest('tr');
                if (document.querySelectorAll('.header-row').length > 1) row.remove();
            };

            function getHeaders() {
                var h = {};
                document.querySelectorAll('.header-row').forEach(function(row) {
                    var k = row.querySelector('.header-key').value.trim();
                    var v = row.querySelector('.header-value').value.trim();
                    if (k) h[k] = v;
                });
                return h;
            }

            function resolveUrl(raw) {
                if (!raw) return null;
                if (BARE_LOCAL.test(raw)) return raw;
                return null;
            }

            function renderResponse(status, elapsed, headersArr, bodyText) {
                var cls = 'status-pending';
                var label = String(status);
                if (status >= 200 && status < 300) cls = 'status-2xx';
                else if (status >= 300 && status < 400) cls = 'status-3xx';
                else if (status >= 400 && status < 500) cls = 'status-4xx';
                else if (status >= 500) cls = 'status-5xx';
                statusBadge.className = 'status-badge ' + cls;
                statusBadge.textContent = label;

                timingEl.textContent = elapsed + 'ms';
                if (elapsed > 1000) timingEl.textContent = (elapsed/1000).toFixed(1) + 's';

                var pretty = bodyText;
                try { pretty = JSON.stringify(JSON.parse(bodyText), null, 2); } catch(e) {}
                respBody.textContent = pretty;

                if (headersArr && headersArr.length) {
                    var hl = '';
                    headersArr.forEach(function(h) { hl += h[0] + ': ' + h[1] + '\\n'; });
                    respHeadersBody.textContent = hl;
                    respHeadersToggle.classList.remove('hidden');
                    respHeadersToggle.innerHTML = 'Headers \\u25b2';
                    respHeadersBody.style.display = 'none';
                } else {
                    respHeadersBody.textContent = '';
                    respHeadersToggle.classList.add('hidden');
                }
                sendBtn.disabled = false;
                sendBtn.textContent = 'Send';
            }

            function renderError(msg) {
                statusBadge.className = 'status-badge status-5xx';
                statusBadge.textContent = 'Error';
                timingEl.textContent = '';
                respBody.textContent = 'Request failed: ' + msg;
                respHeadersBody.textContent = '';
                respHeadersToggle.classList.add('hidden');
                sendBtn.disabled = false;
                sendBtn.textContent = 'Send';
            }

            function toProxyUrl(raw) {
                var parsed = new URL(raw);
                if (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1' || parsed.hostname === '0.0.0.0') {
                    var base = PROXY_BASE + INTERVIEW_PATH + '/proxy/' + parsed.port + parsed.pathname + parsed.search;
                    return base + (API_TOKEN ? '?token=' + encodeURIComponent(API_TOKEN) : '');
                }
                return raw;
            }

            sendBtn.addEventListener('click', async function() {
                var rawUrl = urlInput.value.trim();
                var resolved = resolveUrl(rawUrl);
                if (!resolved) {
                    urlInput.classList.add('invalid');
                    errEl.textContent = 'Only localhost URLs are allowed.';
                    return;
                }
                urlInput.classList.remove('invalid');
                errEl.textContent = '';

                sendBtn.disabled = true;
                sendBtn.textContent = 'Sending...';
                statusBadge.className = 'status-badge status-pending';
                statusBadge.textContent = '...';
                timingEl.textContent = '';
                respBody.textContent = '';
                respHeadersBody.textContent = '';
                respHeadersToggle.classList.add('hidden');

                var m = method.value;
                var headers = getHeaders();
                var body = null;
                if (m === 'POST' || m === 'PUT' || m === 'PATCH') {
                    var raw = bodyTa.value.trim();
                    if (raw) {
                        try { JSON.parse(raw); } catch(e) {
                            renderError('Invalid JSON in body');
                            return;
                        }
                        body = raw;
                        if (!headers['Content-Type']) headers['Content-Type'] = 'application/json';
                    }
                }

                var urlToFetch = toProxyUrl(resolved);
                var start = performance.now();
                try {
                    var res = await fetch(urlToFetch, {
                        method: m,
                        headers: headers,
                        body: body,
                    });
                    var elapsed = Math.round(performance.now() - start);
                    var text = await res.text();
                    renderResponse(res.status, elapsed, [...res.headers], text);
                } catch (err) {
                    var elapsed = Math.round(performance.now() - start);
                    renderError(err.message + ' (' + elapsed + 'ms)');
                }
            });

            urlInput.addEventListener('keydown', function(e) { if (e.key === 'Enter') sendBtn.click(); });
            urlInput.addEventListener('input', function() { urlInput.classList.remove('invalid'); errEl.textContent = ''; });
        })();
    </script>
</body>
</html>"""


def get_preview_page(
    interview_id: str = "",
    token: str = "",
    url: str = "",
    proxy_base: str = "",
) -> str:
    interview_path = f"/api/interviews/{interview_id}/ide" if interview_id else ""
    page = PREVIEW_PAGE.replace("{proxy_base}", proxy_base)
    page = page.replace("{interview_path}", interview_path)
    return page


def get_api_tester_page(
    interview_id: str = "",
    token: str = "",
    proxy_base: str = "",
) -> str:
    interview_path = f"/api/interviews/{interview_id}/ide" if interview_id else ""
    page = API_TESTER_PAGE.replace("{proxy_base}", proxy_base)
    page = page.replace("{interview_path}", interview_path)
    page = page.replace("{api_token}", token)
    return page
