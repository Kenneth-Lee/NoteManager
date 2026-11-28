/* Optional Vim-like navigation for the rendered document panel. */
(function () {
  'use strict';

  var VIM_LS = 'nm-editor-vim';
  var panel = document.getElementById('nm-panel-render');
  if (!panel) return;
  var enabled = false;
  try { enabled = window.localStorage.getItem(VIM_LS) === '1'; } catch (e) {}
  var pendingG = 0;
  var searchBox = null;
  var searchInput = null;
  var searchQuery = '';
  var searchDirection = 1;
  var currentMatch = null;

  function renderVisible() {
    if (!panel) return false;
    if (document.body.classList.contains('nm-split')) return true;
    return panel.classList.contains('active');
  }

  function isTypingTarget(target) {
    if (!target) return false;
    if (target.closest && target.closest('.CodeMirror')) return true;
    if (target.isContentEditable) return true;
    return /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || '');
  }

  function uiBlocked() {
    if (document.body.classList.contains('nm-note-open') ||
        document.body.classList.contains('nm-cmd-open')) return true;
    var overlays = document.querySelectorAll('.nm-note-overlay:not([hidden]), .nm-cmd-overlay:not([hidden])');
    return overlays.length > 0;
  }

  function clickTab(name) {
    var tab = document.querySelector('.nm-tab[data-tab="' + name + '"]');
    if (tab) tab.click();
  }

  function goParentDirectory() {
    var path = (document.body.getAttribute('data-nm-page-path') || '').replace(/\\/g, '/');
    if (!path) return;
    var slash = path.lastIndexOf('/');
    var dir = slash >= 0 ? path.slice(0, slash) : '';
    var prefix = window.NM_PREFIX || '';
    window.location.href = prefix + 'index.html' +
      (dir ? '#nm-dir=' + encodeURIComponent(dir) : '');
  }

  function scrollBy(delta) {
    panel.scrollTop = Math.max(0, Math.min(panel.scrollHeight, panel.scrollTop + delta));
  }

  function pageHeight() {
    return Math.max(80, panel.clientHeight * 0.85);
  }

  function collectMatches(query) {
    var needle = String(query || '').toLowerCase();
    var matches = [];
    if (!needle) return matches;
    var walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var parent = node.parentNode;
        if (!parent) return NodeFilter.FILTER_REJECT;
        if (parent.closest && parent.closest('script,style,noscript')) {
          return NodeFilter.FILTER_REJECT;
        }
        return node.nodeValue && node.nodeValue.trim()
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var node;
    while ((node = walker.nextNode())) {
      var value = node.nodeValue || '';
      var lower = value.toLowerCase();
      var pos = 0;
      while (pos < lower.length) {
        var index = lower.indexOf(needle, pos);
        if (index < 0) break;
        matches.push({node: node, index: index, length: needle.length});
        pos = index + Math.max(1, needle.length);
      }
    }
    return matches;
  }

  function highlightMatch(match) {
    if (!match) return false;
    var range = document.createRange();
    range.setStart(match.node, match.index);
    range.setEnd(match.node, match.index + match.length);
    var selection = window.getSelection();
    if (selection) {
      selection.removeAllRanges();
      selection.addRange(range);
    }
    var rect = range.getBoundingClientRect();
    var panelRect = panel.getBoundingClientRect();
    if (rect.top < panelRect.top + 8 || rect.bottom > panelRect.bottom - 8) {
      panel.scrollTop += rect.top - panelRect.top - panel.clientHeight / 2;
      panel.scrollTop = Math.max(0, Math.min(panel.scrollHeight, panel.scrollTop));
    }
    currentMatch = {node: match.node, index: match.index};
    return true;
  }

  function chooseMatch(matches, direction, advance) {
    if (!matches.length) return null;
    if (!advance || !currentMatch) {
      return direction < 0 ? matches[matches.length - 1] : matches[0];
    }
    var current = -1;
    for (var i = 0; i < matches.length; i++) {
      if (matches[i].node === currentMatch.node && matches[i].index === currentMatch.index) {
        current = i;
        break;
      }
    }
    if (current < 0) {
      return direction < 0 ? matches[matches.length - 1] : matches[0];
    }
    var next = (current + direction + matches.length) % matches.length;
    return matches[next];
  }

  function runSearch(query, direction, advance) {
    query = String(query || '');
    if (!query) return false;
    if (query !== searchQuery) {
      searchQuery = query;
      currentMatch = null;
    }
    searchDirection = direction < 0 ? -1 : 1;
    var matches = collectMatches(searchQuery);
    var match = chooseMatch(matches, searchDirection, !!advance);
    if (!match) {
      currentMatch = null;
      return false;
    }
    return highlightMatch(match);
  }

  function clearSelection() {
    var selection = window.getSelection();
    if (selection) selection.removeAllRanges();
  }

  function openSearch(direction) {
    searchDirection = direction < 0 ? -1 : 1;
    if (!searchBox) {
      searchBox = document.createElement('div');
      searchBox.className = 'nm-vim-search';
      searchBox.innerHTML =
        '<input type="text" class="nm-vim-search-input" placeholder="查找…" autocomplete="off">' +
        '<button type="button" class="nm-vim-search-next" title="下一个">↓</button>' +
        '<button type="button" class="nm-vim-search-close" title="关闭">×</button>';
      document.body.appendChild(searchBox);
      searchInput = searchBox.querySelector('.nm-vim-search-input');
      searchInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          runSearch(searchInput.value, searchDirection, true);
          // Vim closes the command line as soon as the search starts.
          closeSearch(true);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          closeSearch();
        }
      });
      searchBox.querySelector('.nm-vim-search-next').addEventListener('click', function () {
        runSearch(searchInput.value, searchDirection, true);
        closeSearch(true);
      });
      searchBox.querySelector('.nm-vim-search-close').addEventListener('click', function () {
        closeSearch(false);
      });
    }
    searchBox.hidden = false;
    searchInput.value = searchQuery;
    searchInput.focus();
    searchInput.select();
  }

  function closeSearch(keepSelection) {
    if (!searchBox) return;
    searchBox.hidden = true;
    if (!keepSelection) clearSelection();
    if (document.activeElement === searchInput) panel.focus();
  }

  function setEnabled(value) {
    enabled = !!value;
    if (!enabled) closeSearch();
  }

  document.addEventListener('keydown', function (event) {
    if (!enabled || !renderVisible() || isTypingTarget(event.target)) return;
    if (event.altKey || event.metaKey) return;
    var key = event.key;

    if (key === '/' || key === '?') {
      event.preventDefault();
      openSearch(key === '?' ? -1 : 1);
      return;
    }
    if (key === 'Escape' && searchBox && !searchBox.hidden) {
      event.preventDefault();
      closeSearch();
      return;
    }
    if (uiBlocked()) return;
    if (searchBox && !searchBox.hidden) return;
    if (!event.ctrlKey && !event.metaKey && !event.altKey) {
      if (key === '2' || key === 'c') {
        event.preventDefault();
        clickTab('source');
        pendingG = 0;
        return;
      }
      if (key === '3') {
        event.preventDefault();
        clickTab('warnings');
        pendingG = 0;
        return;
      }
      if (key === 'd') {
        event.preventDefault();
        var toc = document.getElementById('nm-sidebar-view-toc');
        if (toc) toc.click();
        pendingG = 0;
        return;
      }
      if (key === 'f') {
        event.preventDefault();
        var results = document.getElementById('nm-sidebar-view-results');
        if (results) results.click();
        pendingG = 0;
        return;
      }
      if (key === 's') {
        event.preventDefault();
        var split = document.getElementById('nm-split-btn');
        if (split) split.click();
        pendingG = 0;
        return;
      }
      if (key === 'e') {
        event.preventDefault();
        var external = document.getElementById('nm-open-external');
        if (external) external.click();
        pendingG = 0;
        return;
      }
      if (key === 'u') {
        event.preventDefault();
        goParentDirectory();
        pendingG = 0;
        return;
      }
    }
    if (event.ctrlKey) {
      if (key === 'u' || key === 'b') {
        event.preventDefault();
        scrollBy(-pageHeight());
        return;
      }
      if (key === 'd' || key === 'f') {
        event.preventDefault();
        scrollBy(pageHeight());
      }
      return;
    }
    if (key === 'n' || key === 'N') {
      if (!searchQuery) return;
      event.preventDefault();
      runSearch(searchQuery, key === 'n' ? searchDirection : -searchDirection, true);
      return;
    }
    if (key === 'j') {
      event.preventDefault();
      scrollBy(32);
      pendingG = 0;
    } else if (key === 'k') {
      event.preventDefault();
      scrollBy(-32);
      pendingG = 0;
    } else if (key === 'G') {
      event.preventDefault();
      panel.scrollTop = panel.scrollHeight;
      pendingG = 0;
    } else if (key === 'g') {
      event.preventDefault();
      if (pendingG) {
        pendingG = 0;
        panel.scrollTop = 0;
      } else {
        pendingG = Date.now();
        setTimeout(function () {
          if (Date.now() - pendingG > 450) pendingG = 0;
        }, 500);
      }
    }
  });

  document.addEventListener('nm-vim-pref', function (event) {
    setEnabled(event && event.detail && event.detail.on);
  });
  window.nmRenderVim = {
    setEnabled: setEnabled,
    enabled: function () { return enabled; },
    runSearch: runSearch,
    search: function (query, direction) { return runSearch(query, direction || 1, false); }
  };
})();
