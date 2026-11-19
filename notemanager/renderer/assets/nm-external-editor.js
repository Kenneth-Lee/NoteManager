/* Open the source file of the current page in an external editor (default: gvim). */
(function () {
  "use strict";

  function projectFromPath() {
    var m = (window.location.pathname || "").match(/^\/p\/(\d+)\//);
    return m ? parseInt(m[1], 10) : -1;
  }

  function detectTarget() {
    var pageMeta = document.getElementById("nm-page-meta");
    if (pageMeta) {
      try {
        var cfg = JSON.parse(pageMeta.textContent || "{}");
        var project = projectFromPath();
        if (project >= 0 && cfg && cfg.editable && cfg.source_rel) {
          return {kind: "managed", project: project, source_rel: cfg.source_rel};
        }
      } catch (e) {}
    }
    var tempMeta = document.getElementById("nm-temp-meta");
    if (tempMeta) {
      var m = (window.location.pathname || "").match(/^\/temp\/([0-9a-f]{16})\.html$/);
      if (m) {
        return {kind: "temp", temp_key: m[1]};
      }
    }
    return null;
  }

  function init() {
    var btn = document.getElementById("nm-open-external");
    if (!btn) return;
    var target = detectTarget();
    if (!target) return;
    btn.style.display = "";

    btn.addEventListener("click", function () {
      if (btn.disabled) return;
      if (target.kind === "managed" && window.__nmDirty) {
        if (!window.confirm(
          "网页编辑器中有未保存修改；外部编辑器打开的是磁盘上的版本。继续？"
        )) {
          return;
        }
      }

      var payload = target.kind === "managed"
        ? {kind: "managed", project: target.project, source_rel_path: target.source_rel}
        : {kind: "temp", temp_key: target.temp_key};

      var oldText = btn.getAttribute("data-nm-label") || btn.textContent;
      btn.setAttribute("data-nm-label", oldText);
      btn.disabled = true;
      fetch("/api/open_editor", {
        method: "POST",
        headers: {"Content-Type": "application/json; charset=utf-8"},
        body: JSON.stringify(payload)
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return {ok: r.ok, data: data};
          });
        })
        .then(function (res) {
          if (!res.ok || !res.data || !res.data.ok) {
            throw new Error((res.data && res.data.error) || "打开外部编辑器失败");
          }
          btn.textContent = "已请求";
          setTimeout(function () { btn.textContent = oldText; }, 1500);
        })
        .catch(function (err) {
          window.alert(err && err.message ? err.message : "打开外部编辑器失败");
        })
        .then(function () {
          btn.disabled = false;
        }, function () {
          btn.disabled = false;
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
