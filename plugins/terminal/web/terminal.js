/* 终端插件的前端。
 *
 * 面板本身不认识「终端」这个概念——它只是把这段脚本注入进去。所以这里
 * 自己做两件事：往标签栏里加一个「终端」入口，在 #pane 里加一个对应的页面。
 * 所有 id 都带 tplug- 前缀，避免和面板自己的 DOM 撞名。
 *
 * 数据流：xterm.js 负责渲染（ANSI 颜色、光标、TUI 重绘），我们只做
 * 1) 建会话 2) 定时拉增量 3) 把用户按键 POST 回去。
 */
(function () {
  "use strict";

  const PLUGIN_ID = "terminal";
  const API = "/api/plugin/" + PLUGIN_ID;
  const POLL_MS = 120;
  // 面板自己管的那些页面，切到终端时都要让开
  const PANEL_PANES = ["dash", "log", "bar", "files", "map", "desktop", "ai", "jars", "jump"];

  const state = {
    sessionId: null,
    term: null,
    fit: null,
    timer: null,
    seq: 0,
    busy: false,
  };

  function q(id) { return document.getElementById(id); }

  function api(path, body) {
    const opts = { method: body ? "POST" : "GET", headers: {} };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) { return r.json(); });
  }

  function note(text) {
    if (typeof window.toast === "function") window.toast(text);
    else console.log("[terminal]", text);
  }

  /* ---------------------------------------------------------------- 界面 */

  function buildUi() {
    const tabs = document.querySelector("#head .tabs");
    const pane = q("pane");
    if (!tabs || !pane) return false;

    if (!q("tplug-tab")) {
      const btn = document.createElement("button");
      btn.className = "tab";
      btn.id = "tplug-tab";
      btn.textContent = "终端";
      btn.addEventListener("click", show);
      const jarsTab = q("tab-jars");
      if (jarsTab && jarsTab.parentElement === tabs) {
        tabs.insertBefore(btn, jarsTab);
      } else {
        tabs.appendChild(btn);
      }
    }

    if (!q("tplug-style")) {
      // 插件自己的样式。用独立 <style> 而不是改 index.html，
      // 免得插件和面板互相覆盖。
      const style = document.createElement("style");
      style.id = "tplug-style";
      style.textContent = [
        "#tplug-pane{flex:1;min-height:0;display:flex;flex-direction:column}",
        "#tplug-bar{display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap;",
        "  padding:var(--s2) var(--s4);border-bottom:1px solid var(--line)}",
        "#tplug-shell,#tplug-server{min-width:130px;max-width:220px}",
        "#tplug-view{flex:1;min-height:0;overflow:hidden;background:#0b0a09;",
        "  padding:var(--s2) var(--s4)}",
        "#tplug-term{width:100%;height:100%}",
        "#tplug-hint{padding:0 var(--s4) var(--s2)}",
      ].join("\n");
      document.head.appendChild(style);
    }

    if (!q("tplug-pane")) {
      const box = document.createElement("div");
      box.id = "tplug-pane";
      box.hidden = true;
      box.innerHTML = [
        '<div id="tplug-bar">',
        '  <select id="tplug-shell"></select>',
        '  <select id="tplug-server"></select>',
        '  <button class="primary" id="tplug-start">新建终端</button>',
        '  <button id="tplug-kill" disabled>关闭</button>',
        '  <span class="sp" style="flex:1"></span>',
        '  <span id="tplug-stat" class="hint"></span>',
        "</div>",
        '<div id="tplug-view"><div id="tplug-term"></div></div>',
        '<div class="hint" id="tplug-hint">',
        "背后是一个真正的 cmd / PowerShell（ConPTY 伪控制台），",
        "支持彩色输出与方向键菜单。",
        "</div>",
      ].join("\n");
      pane.appendChild(box);
      q("tplug-start").addEventListener("click", createSession);
      q("tplug-kill").addEventListener("click", killSession);
    }
    return true;
  }

  function show() {
    if (!q("tplug-pane")) return;
    PANEL_PANES.forEach(function (id) {
      const el = q(id);
      if (el) el.hidden = true;
    });
    document.querySelectorAll("#pane .tab").forEach(function (el) {
      el.classList.toggle("on", el.id === "tplug-tab");
    });
    const details = q("details");
    if (details) details.hidden = true;
    q("tplug-pane").hidden = false;

    if (!state.sessionId) {
      loadOptions();
    } else if (state.fit) {
      setTimeout(function () { state.fit(); }, 50);
    }
  }

  function hideIfOtherTab(target) {
    if (!target || !target.classList) return;
    if (!target.classList.contains("tab")) return;
    if (target.id === "tplug-tab") return;
    const box = q("tplug-pane");
    if (box) box.hidden = true;
  }

  /* ---------------------------------------------------------------- 选项 */

  function loadOptions() {
    api(API + "/shells").then(function (data) {
      if (!data || !data.ok) return;
      const shellSel = q("tplug-shell");
      if (shellSel && !shellSel.options.length) {
        (data.shells || []).forEach(function (s) {
          const opt = document.createElement("option");
          opt.value = s.id;
          opt.textContent = s.label;
          shellSel.appendChild(opt);
        });
      }
      if (!data.supported) {
        q("tplug-stat").textContent = "当前系统不支持（需要 Windows 10 1809+）";
        q("tplug-start").disabled = true;
      }
      fillServers();
    }).catch(function () {});
  }

  function fillServers() {
    api("/api/state").then(function (data) {
      const sel = q("tplug-server");
      if (!sel || !data || !data.instances || sel.options.length) return;
      const root = document.createElement("option");
      root.value = "";
      root.textContent = "项目根目录";
      sel.appendChild(root);
      data.instances.forEach(function (item) {
        const opt = document.createElement("option");
        opt.value = item.name;
        opt.textContent = item.name + "（实例目录）";
        sel.appendChild(opt);
      });
    }).catch(function () {});
  }

  /* ---------------------------------------------------------------- 会话 */

  function createSession() {
    if (state.busy) return;
    state.busy = true;
    const shell = q("tplug-shell") ? q("tplug-shell").value : "cmd";
    const server = q("tplug-server") ? q("tplug-server").value : "";
    const view = q("tplug-view");
    const cols = view ? Math.max(40, Math.floor(view.clientWidth / 8)) : 100;
    const rows = view ? Math.max(10, Math.floor(view.clientHeight / 18)) : 30;

    api(API + "/create", { shell: shell, server: server, cols: cols, rows: rows })
      .then(function (data) {
        state.busy = false;
        if (!data || !data.ok) {
          note((data && data.message) || "启动失败");
          return;
        }
        state.sessionId = data.id;
        state.seq = 0;
        mountTerm();
        startPolling();
        q("tplug-kill").disabled = false;
        q("tplug-start").disabled = true;
        q("tplug-stat").textContent =
          (data.shell || "shell") + " · " + (data.cwd || "");
      })
      .catch(function () {
        state.busy = false;
        note("请求失败");
      });
  }

  function mountTerm() {
    const host = q("tplug-term");
    if (!host) return;
    // 必须 dispose 旧实例：xterm 的 onData 是挂在实例上的订阅，
    // 只清 DOM 不会解绑。多点几次「新建终端」就会有 N 个实例同时发按键，
    // 表现成「输入一条命令执行了 N 次」。
    if (state.term) {
      try { state.term.dispose(); } catch (e) { /* 已经销毁过就算了 */ }
      state.term = null;
    }
    host.innerHTML = "";
    if (!window.Terminal) {
      host.textContent = "xterm.js 未加载";
      return;
    }
    const term = new window.Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: '"Cascadia Mono", Consolas, monospace',
      scrollback: 3000,
      theme: {
        background: "#0b0a09",
        foreground: "#f4f0e8",
        cursor: "#a3adf7",
        selectionBackground: "#a3adf755",
      },
    });
    term.open(host);
    state.term = term;

    term.onData(function (text) {
      if (!state.sessionId) return;
      /* xterm 是按字符回调的：一个字一次 POST，打字快的时候每一击都要等
         一次往返，体感就是「延迟巨大」。这里攒一小段（15ms）再一起发，
         回车这类提交键立即发出去，不牺牲响应。 */
      pendingInput += text;
      const flushNow = text.indexOf("\r") >= 0 || text.indexOf("\n") >= 0;
      if (flushNow) {
        clearTimeout(inputTimer);
        inputTimer = null;
        flushInput();
      } else if (!inputTimer) {
        // 60ms 的合并窗口：人的连续击键通常间隔 80–200ms，
        // 这个值能把「快速打字」合成一两次请求，又不会让单字符输入感到迟滞
        inputTimer = setTimeout(flushInput, 60);
      }
    });
    term.onResize(function (size) {
      if (!state.sessionId) return;
      api(API + "/resize", {
        id: state.sessionId, cols: size.cols, rows: size.rows,
      }).catch(function () {});
    });

    /* xterm 5.x 把 fit 拆进了单独的 @xterm/addon-fit 包。为了一个二十行的
       小功能再引一个依赖不值，这里自己量：拿一行字格的宽高反推行列数。 */
    state.fit = function () {
      const view = q("tplug-view");
      if (!view) return;
      let cw = 8, ch = 18;
      try {
        const dims = term._core && term._core._renderService
          && term._core._renderService.dimensions;
        if (dims && dims.css && dims.css.cell) {
          cw = dims.css.cell.width || cw;
          ch = dims.css.cell.height || ch;
        }
      } catch (e) { /* 私有字段变了就退回估算值 */ }
      const cols = Math.max(20, Math.floor((view.clientWidth - 16) / cw));
      const rows = Math.max(8, Math.floor((view.clientHeight - 16) / ch));
      if (cols !== term.cols || rows !== term.rows) term.resize(cols, rows);
    };
    setTimeout(function () { state.fit(); term.focus(); }, 60);
  }

  let pendingInput = "";
  let inputTimer = null;

  function flushInput() {
    if (inputTimer) { clearTimeout(inputTimer); inputTimer = null; }
    const data = pendingInput;
    pendingInput = "";
    if (!data || !state.sessionId) return;
    api(API + "/input", { id: state.sessionId, data: data }).catch(function () {});
  }

  function startPolling() {
    stopPolling();
    state.timer = setInterval(function () {
      if (!state.sessionId || !state.term) return;
      api(API + "/output?id=" + encodeURIComponent(state.sessionId) +
          "&since=" + state.seq).then(function (data) {
        if (!data || !data.ok) return;
        (data.chunks || []).forEach(function (pair) {
          state.term.write(decodeUtf8(pair[1]));
        });
        state.seq = data.seq || state.seq;
        if (data.alive === false) {
          stopPolling();
          q("tplug-stat").textContent = "进程已退出";
          q("tplug-kill").disabled = true;
          q("tplug-start").disabled = false;
        }
      }).catch(function () {});
    }, POLL_MS);
  }

  function stopPolling() {
    if (state.timer) clearInterval(state.timer);
    state.timer = null;
  }

  /* 终端输出是任意字节（ANSI 序列 + UTF-8 文本）。
     atob 给出 latin1 字符串，要按 UTF-8 还原后再交给 xterm，
     否则中文会变成一堆乱码。 */
  function decodeUtf8(b64) {
    try {
      const raw = atob(b64);
      try {
        return decodeURIComponent(escape(raw));
      } catch (e) {
        return raw;
      }
    } catch (e) {
      return "";
    }
  }

  function killSession() {
    if (!state.sessionId) return;
    api(API + "/kill", { id: state.sessionId }).then(function () {
      stopPolling();
      state.sessionId = null;
      if (state.term) {
        try { state.term.dispose(); } catch (e) {}
        state.term = null;
      }
      const host = q("tplug-term");
      if (host) host.innerHTML = "";
      q("tplug-kill").disabled = true;
      q("tplug-start").disabled = false;
      q("tplug-stat").textContent = "";
    }).catch(function () {});
  }

  /* ---------------------------------------------------------------- 启动 */

  function init() {
    if (!buildUi()) {
      setTimeout(init, 300);        // 面板 DOM 可能还没建好
      return;
    }
    document.addEventListener("click", function (ev) {
      hideIfOtherTab(ev.target);
    }, true);
    window.addEventListener("resize", function () {
      const box = q("tplug-pane");
      if (box && !box.hidden && state.fit) state.fit();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
