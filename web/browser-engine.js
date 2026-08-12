/*
 * Runs the Janggi engine inside the browser so the GitHub Pages build needs no
 * backend at all.
 *
 * The board UI (templates/index.html) talks to the engine over three POST
 * endpoints. Rather than fork the UI for static hosting, this intercepts
 * fetch() for /api/* and answers from a Python interpreter running in
 * WebAssembly (Pyodide) with the real janggi package loaded. The UI is byte
 * for byte the same file the Flask server serves.
 *
 * Two caveats it is honest about in the banner it renders:
 *   - WebAssembly cannot load the Cython extensions, so this is the pure-Python
 *     fallback: roughly two orders of magnitude slower, a few plies shallower.
 *   - Anyone who has the full engine deployed somewhere can paste that URL in
 *     and every /api/ call is forwarded there instead, at full strength.
 */
(function () {
  "use strict";

  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js";
  const MODULES = [
    "__init__", "board", "evaluate", "see", "search", "score",
    "repetition", "gibo", "book", "mcts", "nn_encode",
  ];
  const STORE_KEY = "janggi.serverUrl";
  const originalFetch = window.fetch.bind(window);

  let pyodidePromise = null;
  let statusEl = null;

  function serverUrl() {
    try {
      return (localStorage.getItem(STORE_KEY) || "").trim();
    } catch (err) {
      return "";
    }
  }

  function setStatus(text, tone) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.style.color = tone === "error" ? "#C0392B"
      : tone === "ready" ? "#2E7D32" : "#666";
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = function () { reject(new Error("could not load " + src)); };
      document.head.appendChild(s);
    });
  }

  async function bootPyodide() {
    setStatus("파이썬 런타임 내려받는 중… (최초 1회, 약 10MB)");
    await loadScript(PYODIDE);
    const pyodide = await loadPyodide();

    setStatus("엔진 모듈 로드 중…");
    pyodide.FS.mkdir("/app");
    pyodide.FS.mkdir("/app/janggi");
    const sources = await Promise.all(
      MODULES.map(function (name) {
        return originalFetch("janggi/" + name + ".py").then(function (r) {
          if (!r.ok) throw new Error("missing janggi/" + name + ".py");
          return r.text();
        });
      })
    );
    MODULES.forEach(function (name, i) {
      pyodide.FS.writeFile("/app/janggi/" + name + ".py", sources[i]);
    });
    const api = await originalFetch("engine_api.py").then(function (r) {
      if (!r.ok) throw new Error("missing engine_api.py");
      return r.text();
    });
    pyodide.FS.writeFile("/app/engine_api.py", api);

    pyodide.runPython("import sys; sys.path.insert(0, '/app')");
    pyodide.runPython("import engine_api");
    setStatus("브라우저 엔진 준비 완료 — 서버 없이 동작합니다.", "ready");
    return pyodide;
  }

  function ensurePyodide() {
    if (!pyodidePromise) {
      pyodidePromise = bootPyodide().catch(function (err) {
        setStatus("엔진 로드 실패: " + err.message, "error");
        pyodidePromise = null;
        throw err;
      });
    }
    return pyodidePromise;
  }

  function jsonResponse(text) {
    return new Response(text, {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const idx = url.indexOf("/api/");
    if (idx === -1) {
      return originalFetch(input, init);
    }

    // A real deployment beats the WebAssembly fallback by a wide margin, so
    // prefer one whenever the user has pointed us at it.
    const server = serverUrl();
    if (server) {
      const forwarded = server.replace(/\/+$/, "") + url.slice(idx);
      return originalFetch(forwarded, init);
    }

    const body = (init && init.body) || "{}";
    return ensurePyodide().then(function (pyodide) {
      pyodide.globals.set("_req_path", url);
      pyodide.globals.set("_req_body", typeof body === "string" ? body : String(body));
      const out = pyodide.runPython("engine_api.handle(_req_path, _req_body)");
      return jsonResponse(out);
    }).catch(function (err) {
      return jsonResponse(JSON.stringify({ error: String(err && err.message || err) }));
    });
  };

  // ------------------------------------------------------------------ banner
  function buildBanner() {
    const bar = document.createElement("div");
    bar.style.cssText =
      "max-width:520px;margin:0 auto 12px;padding:10px 12px;border-radius:8px;" +
      "background:#f1efe8;font:13px/1.5 system-ui,sans-serif;color:#444;";

    const title = document.createElement("div");
    title.innerHTML =
      "<b>브라우저 엔진</b> — 서버 없이 이 페이지 안에서 돕니다. " +
      "컴파일 가속을 못 쓰므로 정식 배포판보다 얕게 읽습니다.";
    bar.appendChild(title);

    statusEl = document.createElement("div");
    statusEl.style.cssText = "margin-top:6px;color:#666;";
    statusEl.textContent = "첫 분석 요청 때 엔진을 불러옵니다.";
    bar.appendChild(statusEl);

    const row = document.createElement("div");
    row.style.cssText = "margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;";
    const input = document.createElement("input");
    input.type = "url";
    input.placeholder = "정식 엔진 서버 주소 (선택) — 예: https://내앱.up.railway.app";
    input.value = serverUrl();
    input.style.cssText =
      "flex:1 1 240px;min-width:0;padding:7px 9px;border:1px solid #ccc;border-radius:7px;font:inherit;";
    const save = document.createElement("button");
    save.textContent = "적용";
    save.style.cssText =
      "padding:7px 14px;border:1px solid #ccc;border-radius:7px;background:#fff;cursor:pointer;font:inherit;";
    save.onclick = function () {
      try {
        const value = input.value.trim();
        if (value) {
          localStorage.setItem(STORE_KEY, value);
          setStatus("서버로 전달합니다: " + value, "ready");
        } else {
          localStorage.removeItem(STORE_KEY);
          setStatus("브라우저 엔진을 사용합니다.", "ready");
        }
      } catch (err) {
        setStatus("설정을 저장할 수 없습니다: " + err.message, "error");
      }
    };
    row.appendChild(input);
    row.appendChild(save);
    bar.appendChild(row);
    return bar;
  }

  function mount() {
    const wrap = document.querySelector(".wrap") || document.body;
    const banner = buildBanner();
    const heading = wrap.querySelector("h1");
    if (heading && heading.nextSibling) {
      wrap.insertBefore(banner, heading.nextSibling);
    } else {
      wrap.insertBefore(banner, wrap.firstChild);
    }
    if (!serverUrl()) {
      // Warm the runtime up now so the first "분석" click is not a cold start.
      ensurePyodide().catch(function () { /* already reported in the banner */ });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
