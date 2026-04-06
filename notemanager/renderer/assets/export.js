/* 仅首页 index.html 加载：索引检索 + 逐文件 grep 扫描（可中止） */
(function () {
  var searchInput = document.getElementById("nm-export-search-input");
  var searchResults = document.getElementById("nm-export-search-results");
  var grepStatus = document.getElementById("nm-export-grep-status");
  var grepCancel = document.getElementById("nm-export-grep-cancel");
  if (!searchInput || !searchResults) return;
  if (!document.body.classList.contains("nm-export-index")) return;

  var prefix = typeof window.NM_PREFIX === "string" ? window.NM_PREFIX : "";
  var searchData = null;
  var debounceTimer = null;
  var grepRun = { token: 0, aborter: null };
  var latestResults = [];
  var latestQuery = "";
  var STATE_KEY = "nm_export_index_state_v1";

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

  function loadIndex() {
    if (searchData) return Promise.resolve(searchData);
    return fetch(prefix + "_index/search_index.json")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        searchData = d || { documents: [] };
        return searchData;
      });
  }

  function htmlPath(pathWithHash) {
    return String(pathWithHash || "").split("#")[0];
  }

  function uniquePathsFromDocs(docs) {
    var seen = {};
    var out = [];
    for (var i = 0; i < docs.length; i++) {
      var p = htmlPath(docs[i].path);
      if (!p || seen[p]) continue;
      seen[p] = true;
      out.push(p);
    }
    return out;
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
    if (term.type === "grep") return false;
    if (term.type && term.type !== "fname" && doc.type !== term.type) return false;
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

  function typeBadge(type) {
    var label = TYPE_LABELS[type] || type;
    return '<span class="nm-type-badge nm-type-' + escapeHtml(type || "") + '">' + escapeHtml(label) + "</span>";
  }

  function renderResults(query, results, extraTip) {
    if (!results.length) {
      return (
        '<div class="nm-exp-no-results">' +
        escapeHtml(extraTip || "无搜索结果") +
        "</div>"
      );
    }
    var html = '<div class="nm-exp-result-count">找到 ' + results.length + " 条 · 点击打开整页笔记</div>";
    var re = buildRegex(query || "");
    for (var i = 0; i < results.length; i++) {
      var doc = results[i];
      var snippet = escapeHtml(doc.snippet || "");
      if (query) {
        try {
          snippet = snippet.replace(new RegExp("(" + re.source + ")", "gi"), "<mark>$1</mark>");
        } catch (e) {}
      }
      var href = escAttr((doc.path || "").trim() || "#");
      html +=
        '<a class="nm-exp-result-item" href="' +
        href +
        '">' +
        '<div class="nm-exp-result-title">' +
        typeBadge(doc.type || "") +
        escapeHtml(doc.title || "") +
        "</div>" +
        '<div class="nm-exp-result-path">' +
        escapeHtml(doc.path || "") +
        "</div>" +
        '<div class="nm-exp-result-snippet">' +
        snippet +
        "</div>" +
        "</a>";
    }
    return html;
  }

  function persistState() {
    try {
      sessionStorage.setItem(
        STATE_KEY,
        JSON.stringify({
          query: latestQuery,
          results: latestResults,
          grepStatus: grepStatus ? grepStatus.textContent || "" : "",
        })
      );
    } catch (e) {}
  }

  function restoreState() {
    try {
      var raw = sessionStorage.getItem(STATE_KEY);
      if (!raw) return;
      var st = JSON.parse(raw);
      if (st && typeof st.query === "string") {
        searchInput.value = st.query;
        latestQuery = st.query;
      }
      if (st && Array.isArray(st.results)) {
        latestResults = st.results;
        searchResults.innerHTML = renderResults(latestQuery, latestResults);
      }
      if (st && typeof st.grepStatus === "string") {
        setGrepStatus(st.grepStatus);
      }
    } catch (e) {}
  }

  function setGrepStatus(msg) {
    if (grepStatus) grepStatus.textContent = msg || "";
  }

  function stopGrep() {
    grepRun.token += 1;
    if (grepRun.aborter) {
      try {
        grepRun.aborter.abort();
      } catch (e) {}
      grepRun.aborter = null;
    }
    if (grepCancel) grepCancel.hidden = true;
  }

  function extractRenderText(fullHtml) {
    var doc = new DOMParser().parseFromString(fullHtml, "text/html");
    var root = doc.getElementById("nm-panel-render") || doc.body;
    var text = root ? (root.textContent || "") : "";
    return text.replace(/\r/g, "");
  }

  function extractRenderMatchInfo(fullHtml, re) {
    var doc = new DOMParser().parseFromString(fullHtml, "text/html");
    var root = doc.getElementById("nm-panel-render") || doc.body;
    if (!root) return { text: "", matchIndex: -1, anchor: "" };

    var textLen = 0;
    var marks = []; // {id, offset}

    function walk(node) {
      if (!node) return;
      if (node.nodeType === 1) {
        var id = node.getAttribute && node.getAttribute("id");
        if (id) marks.push({ id: id, offset: textLen });
        var ch = node.childNodes || [];
        for (var i = 0; i < ch.length; i++) walk(ch[i]);
        return;
      }
      if (node.nodeType === 3) {
        textLen += (node.nodeValue || "").replace(/\r/g, "").length;
      }
    }

    walk(root);
    var text = (root.textContent || "").replace(/\r/g, "");
    var m = re.exec(text);
    var idx = m ? m.index : -1;
    var anchor = "";
    if (idx >= 0 && marks.length) {
      for (var j = 0; j < marks.length; j++) {
        if (marks[j].offset <= idx) anchor = marks[j].id;
        else break;
      }
    }
    return { text: text, matchIndex: idx, anchor: anchor };
  }

  function snippetAround(text, idx, len) {
    var start = Math.max(0, idx - 40);
    var end = Math.min(text.length, idx + len + 60);
    var snip = text.slice(start, end).replace(/\s+/g, " ").trim();
    return snip;
  }

  async function runGrepScan(terms, mappedFilteredPaths, queryForRender) {
    var grepTerms = terms.filter(function (t) {
      return t.type === "grep";
    });
    if (!grepTerms.length) return null;

    var token = ++grepRun.token;
    if (grepCancel) grepCancel.hidden = false;

    var allPaths = uniquePathsFromDocs((searchData && searchData.documents) || []);
    var candidates = mappedFilteredPaths && mappedFilteredPaths.length
      ? allPaths.filter(function (p) { return mappedFilteredPaths.indexOf(p) >= 0; })
      : allPaths;
    var total = candidates.length;
    var results = [];
    var scanned = 0;

    if (!total) {
      setGrepStatus("grep: 0/0");
      if (grepCancel) grepCancel.hidden = true;
      return [];
    }

    for (var i = 0; i < candidates.length; i++) {
      if (token !== grepRun.token) {
        setGrepStatus("grep 已中止");
        if (grepCancel) grepCancel.hidden = true;
        return { cancelled: true, results: results };
      }
      var p = candidates[i];
      grepRun.aborter = new AbortController();
      try {
        var res = await fetch(p, { signal: grepRun.aborter.signal });
        if (!res.ok) throw new Error("HTTP " + res.status);
        var fullHtml = await res.text();
        var firstRe = buildRegex(grepTerms[0].query);
        var info = extractRenderMatchInfo(fullHtml, firstRe);
        var txt = info.text;
        var ok = true;
        for (var j = 0; j < grepTerms.length; j++) {
          if (!buildRegex(grepTerms[j].query).test(txt)) {
            ok = false;
            break;
          }
        }
        if (ok) {
          var first = grepTerms[0].query;
          var hitIdx = info.matchIndex >= 0 ? info.matchIndex : 0;
          var target = p + (info.anchor ? "#" + encodeURIComponent(info.anchor) : "");
          results.push({
            type: "grep",
            title: p.split("/").pop().replace(/\.html$/i, ""),
            path: target,
            snippet: snippetAround(txt, hitIdx, String(first).length || 1),
            text: "",
          });
        }
      } catch (e) {
        if (token !== grepRun.token) {
          setGrepStatus("grep 已中止");
          if (grepCancel) grepCancel.hidden = true;
          return { cancelled: true, results: results };
        }
      } finally {
        grepRun.aborter = null;
      }
      scanned += 1;
      setGrepStatus("grep 扫描中: " + scanned + "/" + total);
      searchResults.innerHTML = renderResults(
        queryForRender,
        results,
        "grep 扫描中: " + scanned + "/" + total
      );
      latestResults = results.slice();
      latestQuery = queryForRender;
      persistState();
      await new Promise(function (r) { setTimeout(r, 0); });
    }

    if (token === grepRun.token) {
      setGrepStatus("grep 完成: " + scanned + "/" + total);
      if (grepCancel) grepCancel.hidden = true;
    }
    return { cancelled: false, results: results };
  }

  async function runSearch(query) {
    latestQuery = query;
    var terms = parseSearchTerms(query);
    var mapped = terms.filter(function (t) {
      return t.type !== "grep";
    });
    var rsMapped = mapped.length ? mappedSearch(mapped) : [];
    var mappedPaths = uniquePathsFromDocs(rsMapped);
    var grepRet = await runGrepScan(terms, mapped.length ? mappedPaths : null, query);
    if (grepRet === null && terms.some(function (t) { return t.type === "grep"; })) return;

    var finalRs;
    if (terms.some(function (t) { return t.type === "grep"; })) {
      finalRs = (grepRet && grepRet.results) || [];
      if (mapped.length) {
        finalRs = finalRs.filter(function (d) {
          return mappedPaths.indexOf(htmlPath(d.path)) >= 0;
        });
      }
    } else {
      stopGrep();
      setGrepStatus("");
      finalRs = rsMapped;
    }
    latestResults = finalRs.slice();
    searchResults.innerHTML = renderResults(query, finalRs);
    persistState();
  }

  if (grepCancel) {
    grepCancel.addEventListener("click", function () {
      stopGrep();
      setGrepStatus("grep 已中止");
      persistState();
    });
  }

  searchInput.addEventListener("input", function () {
    stopGrep();
    clearTimeout(debounceTimer);
    var q = this.value.trim();
    if (!q) {
      setGrepStatus("");
      searchResults.innerHTML = "";
      latestResults = [];
      latestQuery = "";
      persistState();
      return;
    }
    debounceTimer = setTimeout(function () {
      loadIndex()
        .then(function () {
          runSearch(searchInput.value.trim());
        })
        .catch(function () {
          setGrepStatus("");
          searchResults.innerHTML =
            '<div class="nm-exp-no-results">无法加载搜索索引</div>';
          latestResults = [];
          persistState();
        });
    }, 200);
  });

  searchInput.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    var q = this.value.trim();
    if (!q) return;
    stopGrep();
    loadIndex()
      .then(function () {
        runSearch(q);
      })
      .catch(function () {
        setGrepStatus("");
        searchResults.innerHTML =
          '<div class="nm-exp-no-results">无法加载搜索索引</div>';
      });
  });

  restoreState();
})();
