/* NoteManager sidebar search (mapped + async grep) */
(function () {
  function projectFromPath() {
    var m = (window.location.pathname || "").match(/^\/p\/(\d+)\//);
    return m ? parseInt(m[1], 10) : -1;
  }

  var searchInput = document.getElementById("nm-search");
  if (!searchInput) return;

  var sidebarToc = document.getElementById("nm-sidebar-toc");
  var sidebarResults = document.getElementById("nm-sidebar-search-results");
  var prefix = window.NM_PREFIX || "";
  var searchData = null;
  var searchDataPrefix = null;
  var debounceTimer = null;
  var grepState = { token: 0, jobId: null, since: 0 };

  var TYPE_LABELS = {
    chapter: "章节",
    label: "标签",
    index: "索引",
    dtag: "标记",
    grep: "全文",
    fname: "文件名",
  };

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  function escAttr(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  }
  function showIndexLoadError(err) {
    console.error(err || "search index load failed");
    if (sidebarToc) sidebarToc.style.display = "none";
    if (sidebarResults) {
      sidebarResults.innerHTML = '<div class="nm-no-results">无法加载搜索索引</div>';
      sidebarResults.style.display = "block";
    }
  }

  function loadIndex() {
    if (searchData && searchDataPrefix === prefix) return Promise.resolve(searchData);
    return fetch(prefix + "_index/search_index.json")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        searchData = d || { documents: [] };
        searchDataPrefix = prefix;
        return searchData;
      });
  }

  function tokenizeQuery(query) {
    var s = String(query || "").trim();
    var out = [];
    var cur = "";
    var inQuote = false;
    var esc = false;
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (esc) {
        cur += ch;
        esc = false;
        continue;
      }
      if (ch === "\\") {
        // Keep regex escapes such as \b, \d, \.; only consume the
        // backslash when it is escaping whitespace, a quote, or another
        // backslash (the quote/space escaping rules of the search box).
        var next = s.charAt(i + 1);
        if (next && next !== '"' && next !== "\\" && !/\s/.test(next)) {
          cur += "\\";
        }
        esc = true;
        continue;
      }
      if (ch === '"') {
        inQuote = !inQuote;
        continue;
      }
      if (!inQuote && /\s/.test(ch)) {
        if (cur) out.push(cur), (cur = "");
        continue;
      }
      cur += ch;
    }
    if (cur) out.push(cur);
    return out;
  }

  function parseSearchTerms(query) {
    var toks = tokenizeQuery(query);
    var terms = [];
    for (var i = 0; i < toks.length; i++) {
      var t = toks[i];
      var m = t.match(/^(chapter|label|index|dtag|fname|grep):(.*)$/i);
      if (m) {
        var core = (m[2] || "").trim();
        if (core) terms.push({ type: m[1].toLowerCase(), query: core });
      } else if (t) {
        terms.push({ type: null, query: t });
      }
    }
    return terms;
  }

  function buildRegex(q) {
    try {
      return new RegExp(q, "i");
    } catch (e) {
      return new RegExp(String(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
    }
  }

  function termMatchDoc(doc, term) {
    if (!term || !term.query) return true;
    if (term.type && term.type !== "fname" && term.type !== "grep" && doc.type !== term.type) return false;
    var r = buildRegex(term.query);
    if (term.type === "fname") {
      return r.test(doc.text || "") || r.test(doc.path || "") || r.test(doc.title || "");
    }
    var blob = [doc.title || "", doc.text || "", doc.snippet || "", doc.path || ""].join(" ");
    return r.test(blob);
  }

  function mappedSearch(terms) {
    if (!searchData || !searchData.documents) return [];
    var docs = searchData.documents;
    var mapped = terms.filter(function (t) {
      return t.type !== "grep";
    });
    if (!mapped.length) return [];
    var out = [];
    for (var i = 0; i < docs.length; i++) {
      var d = docs[i];
      var ok = true;
      for (var j = 0; j < mapped.length; j++) {
        if (!termMatchDoc(d, mapped[j])) {
          ok = false;
          break;
        }
      }
      if (ok) {
        out.push(d);
        if (out.length >= 200) break;
      }
    }
    return out;
  }

  function docHref(path) {
    return prefix + path;
  }
  function resultHref(doc) {
    var base = docHref(doc.path || "");
    if (doc.type === "grep" && doc.source_path && doc.line_start) {
      var sp = encodeURIComponent(String(doc.source_path));
      var ln = encodeURIComponent(String(doc.line_start));
      var sep = base.indexOf("?") >= 0 ? "&" : "?";
      return base + sep + "src=" + sp + "&line=" + ln;
    }
    return base;
  }
  function typeBadge(type) {
    var label = TYPE_LABELS[type] || type;
    return '<span class="nm-type-badge nm-type-' + type + '">' + label + "</span>";
  }

  function renderResults(query, results, extraTip) {
    if (!results.length) {
      return '<div class="nm-no-results">' + escapeHtml(extraTip || "无搜索结果") + "</div>";
    }
    var html = '<div class="nm-result-count">找到 ' + results.length + " 个结果</div>";
    var re = buildRegex(query || "");
    for (var i = 0; i < results.length; i++) {
      var doc = results[i];
      var snippet = escapeHtml(doc.snippet || "");
      if (query) {
        try {
          snippet = snippet.replace(new RegExp("(" + re.source + ")", "gi"), "<mark>$1</mark>");
        } catch (e) {}
      }
      html +=
        '<a class="nm-result-item" href="' +
        escAttr(resultHref(doc)) +
        '">' +
        '<div class="nm-result-title">' +
        typeBadge(doc.type || "grep") +
        escapeHtml(doc.title || "") +
        "</div>" +
        '<div class="nm-result-path">' +
        escapeHtml(doc.path || "") +
        "</div>" +
        '<div class="nm-result-snippet">' +
        snippet +
        "</div>" +
        "</a>";
    }
    return html;
  }

  function showResultsHtml(html) {
    if (sidebarToc) sidebarToc.style.display = "none";
    if (sidebarResults) {
      sidebarResults.innerHTML = html;
      sidebarResults.style.display = "block";
    }
  }
  function hideResults() {
    if (sidebarToc) sidebarToc.style.display = "";
    if (sidebarResults) {
      sidebarResults.style.display = "none";
      sidebarResults.innerHTML = "";
    }
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function stopGrepJob() {
    grepState.token += 1;
    if (grepState.jobId != null) {
      postJSON("/api/grep_cancel", { job_id: grepState.jobId }).catch(function () {});
    }
    grepState.jobId = null;
    grepState.since = 0;
  }

  function runGrepSearch(terms) {
    var grepTerm = null;
    var filters = [];
    for (var i = 0; i < terms.length; i++) {
      if (terms[i].type === "grep") grepTerm = terms[i].query;
      else filters.push({ type: terms[i].type, query: terms[i].query });
    }
    if (!grepTerm) return;
    var project = projectFromPath();
    if (project < 0) {
      showResultsHtml('<div class="nm-no-results">仅 /p/<工程>/ 页面支持 grep 搜索</div>');
      return;
    }
    stopGrepJob();
    var myToken = grepState.token;
    showResultsHtml('<div class="nm-no-results">grep 搜索中…</div>');
    postJSON("/api/grep_start", { project: project, query: grepTerm, filters: filters })
      .then(function (d) {
        if (!d.ok) throw new Error(d.error || "grep_start failed");
        if (grepState.token !== myToken) return;
        grepState.jobId = d.job_id;
        grepState.since = 0;
        poll();
      })
      .catch(function (e) {
        showResultsHtml('<div class="nm-no-results">' + escapeHtml(e.message || "grep 启动失败") + "</div>");
      });

    function poll() {
      if (grepState.token !== myToken || grepState.jobId == null) return;
      postJSON("/api/grep_status", { job_id: grepState.jobId, since: grepState.since })
        .then(function (d) {
          if (!d.ok) throw new Error(d.error || "grep_status failed");
          grepState.since = d.total_results || grepState.since;
          var rs = (sidebarResults && sidebarResults._grepResults) || [];
          rs = rs.concat(d.results || []);
          if (sidebarResults) sidebarResults._grepResults = rs;
          var tip = "已扫描 " + (d.scanned || 0) + "/" + (d.total || 0);
          showResultsHtml(renderResults(grepTerm, rs, d.done ? "" : tip));
          if (!d.done) setTimeout(poll, 220);
        })
        .catch(function (e) {
          showResultsHtml('<div class="nm-no-results">' + escapeHtml(e.message || "grep 失败") + "</div>");
        });
    }
  }

  function runMappedSearch(query, openFirst) {
    var terms = parseSearchTerms(query);
    var hasGrep = terms.some(function (t) {
      return t.type === "grep";
    });
    var mappedTerms = terms.filter(function (t) {
      return t.type !== "grep";
    });
    var rs = mappedSearch(mappedTerms);
    if (hasGrep && !openFirst) {
      showResultsHtml(renderResults(query, rs, "含 grep 条件，按 Enter 执行全局扫描"));
      return;
    }
    if (openFirst && rs.length === 1) {
      window.location.href = docHref(rs[0].path);
      return;
    }
    showResultsHtml(renderResults(query, rs));
  }

  searchInput.addEventListener("input", function () {
    stopGrepJob();
    clearTimeout(debounceTimer);
    var q = this.value.trim();
    if (!q) {
      hideResults();
      return;
    }
    debounceTimer = setTimeout(function () {
      loadIndex()
        .then(function () {
          runMappedSearch(searchInput.value.trim(), false);
        })
        .catch(showIndexLoadError);
    }, 180);
  });

  searchInput.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    clearTimeout(debounceTimer);
    var q = searchInput.value.trim();
    if (!q) return;
    var terms = parseSearchTerms(q);
    var hasGrep = terms.some(function (t) {
      return t.type === "grep";
    });
    loadIndex()
      .then(function () {
        if (hasGrep) runGrepSearch(terms);
        else runMappedSearch(q, true);
      })
      .catch(showIndexLoadError);
  });

  (function initQParam() {
    var params = new URLSearchParams(window.location.search);
    var q = params.get("q");
    if (!q) return;
    searchInput.value = q;
    loadIndex()
      .then(function () {
        runMappedSearch(q, true);
      })
      .catch(showIndexLoadError);
  })();

  document.querySelectorAll(".nm-dtag-badge").forEach(function (badge) {
    badge.addEventListener("click", function () {
      var tag = this.getAttribute("data-dtag");
      if (!tag) return;
      stopGrepJob();
      searchInput.value = "dtag:" + tag;
      loadIndex()
        .then(function () {
          runMappedSearch(searchInput.value, true);
        })
        .catch(showIndexLoadError);
    });
  });

  (function bindSSE() {
    var evtSource;
    try {
      evtSource = new EventSource("/api/events");
    } catch (e) {
      return;
    }
    evtSource.addEventListener("cli_search", function (e) {
      var data = JSON.parse(e.data || "{}");
      if (data.open_url) {
        window.location.href = data.open_url;
        return;
      }
      var query = data.query || "";
      if (!query) return;
      searchInput.value = query;
      stopGrepJob();
      loadIndex().then(function () {
        runMappedSearch(query, true);
      });
      window.focus();
    });
    evtSource.addEventListener("cli_view", function (e) {
      var data = JSON.parse(e.data || "{}");
      if (data && data.url) window.location.href = data.url;
    });
    evtSource.addEventListener("build_complete", function (e) {
      if (window.__nmSuppressBuildReload) return;
      var data;
      try {
        data = JSON.parse(e.data);
      } catch (err) {
        return;
      }
      if (data.project === undefined || data.project === null) return;
      var parts = (window.location.pathname || "").split("/").filter(function (s) {
        return s;
      });
      if (parts.length < 2 || parts[0] !== "p") return;
      var cur = parseInt(parts[1], 10);
      if (!isNaN(cur) && cur === data.project) window.location.reload();
    });
  })();
})();

/* Document tab shortcuts: Ctrl/⌘+Alt+1/2/3. */
(function () {
  'use strict';
  var TAB_KEYS = {Digit1: 'render', Digit2: 'source', Digit3: 'warnings'};
  document.addEventListener('keydown', function (event) {
    if (!(event.ctrlKey || event.metaKey) || !event.altKey || event.shiftKey) return;
    var name = TAB_KEYS[String(event.code || '')];
    if (!name) return;
    var tab = document.querySelector('.nm-tab[data-tab="' + name + '"]');
    if (!tab) return;
    event.preventDefault();
    tab.click();
    if (name === 'source') {
      setTimeout(function () {
        if (window.__nmCM) window.__nmCM.focus();
      }, 0);
    }
  });
})();
