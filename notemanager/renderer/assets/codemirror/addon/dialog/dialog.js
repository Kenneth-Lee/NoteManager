// CodeMirror, copyright (c) by Marijn Haverbeke and others
// Distributed under an MIT license: https://codemirror.net/5/LICENSE

// Open simple dialogs on top of an editor. Relies on dialog.css.

(function(mod) {
  if (typeof exports == "object" && typeof module == "object") // CommonJS
    mod(require("../../lib/codemirror"));
  else if (typeof define == "function" && define.amd) // AMD
    define(["../../lib/codemirror"], mod);
  else // Plain browser env
    mod(CodeMirror);
})(function(CodeMirror) {
  function dialogDiv(cm, template, bottom) {
    var wrap = cm.getWrapperElement();
    var dialog;
    dialog = wrap.appendChild(document.createElement("div"));
    if (bottom)
      dialog.className = "CodeMirror-dialog CodeMirror-dialog-bottom";
    else
      dialog.className = "CodeMirror-dialog CodeMirror-dialog-top";

    if (typeof template == "string") {
      dialog.innerHTML = template;
    } else { // Assuming it's a detached DOM element.
      dialog.appendChild(template);
    }
    CodeMirror.addClass(wrap, 'dialog-opened');
    pinDialog(cm, wrap, dialog);
    return dialog;
  }

  // NoteManager 补丁: 分屏/height:auto 长文档布局下, 对话框的 absolute+bottom:0
  // 会钉在 wrapper 末尾(往往在视口外数千像素), 视觉上“输入条消失”。
  // 改为 fixed 定位, 始终钉在编辑器挂载点的可视底部/顶部。
  function pinDialog(cm, wrap, dialog) {
    if (!wrap.closest || !wrap.closest('#nm-editor-mount')) return;
    var isBottom = dialog.className.indexOf('dialog-bottom') >= 0;
    dialog.style.position = 'fixed';
    dialog.style.zIndex = '30';
    dialog.style.top = 'auto';
    dialog.style.bottom = 'auto';
    var mounted = true;
    function place() {
      var mount = wrap.closest('#nm-editor-mount');
      if (!mount) return;
      var r = mount.getBoundingClientRect();
      if (r.height <= 0) return; // 面板隐藏时不重排
      dialog.style.left = r.left + 'px';
      dialog.style.width = r.width + 'px';
      if (isBottom) dialog.style.bottom = Math.max(window.innerHeight - r.bottom, 0) + 'px';
      else dialog.style.top = r.top + 'px';
    }
    place();
    window.addEventListener('resize', place);
    document.addEventListener('nm-layout-change', place);
    var mo = new MutationObserver(function() {
      if (!dialog.parentNode && mounted) {
        mounted = false;
        mo.disconnect();
        window.removeEventListener('resize', place);
        document.removeEventListener('nm-layout-change', place);
      }
    });
    mo.observe(wrap, { childList: true });
  }

  function closeNotification(cm, newVal) {
    if (cm.state.currentNotificationClose)
      cm.state.currentNotificationClose();
    cm.state.currentNotificationClose = newVal;
  }

  CodeMirror.defineExtension("openDialog", function(template, callback, options) {
    if (!options) options = {};

    closeNotification(this, null);

    var dialog = dialogDiv(this, template, options.bottom);
    var closed = false, me = this;
    function close(newVal) {
      if (typeof newVal == 'string') {
        inp.value = newVal;
      } else {
        if (closed) return;
        closed = true;
        CodeMirror.rmClass(dialog.parentNode, 'dialog-opened');
        dialog.parentNode.removeChild(dialog);
        me.focus();

        if (options.onClose) options.onClose(dialog);
      }
    }

    var inp = dialog.getElementsByTagName("input")[0], button;
    if (inp) {
      inp.focus();

      if (options.value) {
        inp.value = options.value;
        if (options.selectValueOnOpen !== false) {
          inp.select();
        }
      }

      if (options.onInput)
        CodeMirror.on(inp, "input", function(e) { options.onInput(e, inp.value, close);});
      if (options.onKeyUp)
        CodeMirror.on(inp, "keyup", function(e) {options.onKeyUp(e, inp.value, close);});

      // NoteManager 补丁: IME(中文输入法)兼容。
      // 1) 组合输入期间(keyCode 229 / isComposing)的 keydown 不当作 Esc/Enter 处理,
      //    否则确认候选的回车会直接关闭输入框,导致中文无法输入;
      // 2) 组合期间忽略 focusout 关闭(部分平台输入法会瞬时转移焦点)。
      var composing = false;
      var composingUntil = 0;
      CodeMirror.on(inp, "compositionstart", function() { composing = true; });
      CodeMirror.on(inp, "compositionupdate", function() { composingUntil = Date.now() + 300; });
      CodeMirror.on(inp, "compositionend", function() {
        setTimeout(function() { composing = false; }, 0);
        // fcitx/ibus 等输入法的提交键(回车上屏/空格选词)在 compositionend 之后才送达,
        // 且 isComposing=false —— 宽限期内不把 Enter/Esc 当作搜索执行/取消键。
        composingUntil = Date.now() + 300;
      });

      CodeMirror.on(inp, "keydown", function(e) {
        if (e.isComposing || e.keyCode === 229) return;
        if (Date.now() < composingUntil && (e.keyCode == 13 || e.keyCode == 27)) return;
        if (options && options.onKeyDown && options.onKeyDown(e, inp.value, close)) { return; }
        if (e.keyCode == 27 || (options.closeOnEnter !== false && e.keyCode == 13)) {
          inp.blur();
          CodeMirror.e_stop(e);
          close();
        }
        if (e.keyCode == 13) callback(inp.value, e);
      });

      if (options.closeOnBlur !== false) CodeMirror.on(dialog, "focusout", function (evt) {
        if (composing) return;
        if (evt.relatedTarget !== null) close();
      });
      else {
        // NoteManager 补丁: closeOnBlur=false 时(如 vim 命令行),改用
        // “点击输入框外部任意处”关闭,彻底免疫输入法(fcitx/ibus)的瞬时焦点扰动。
        var onDocMouseDown = function(e) {
          if (closed) { document.removeEventListener('mousedown', onDocMouseDown, true); return; }
          if (dialog.contains(e.target)) return;
          document.removeEventListener('mousedown', onDocMouseDown, true);
          close();
        };
        document.addEventListener('mousedown', onDocMouseDown, true);
      }
    } else if (button = dialog.getElementsByTagName("button")[0]) {
      CodeMirror.on(button, "click", function() {
        close();
        me.focus();
      });

      if (options.closeOnBlur !== false) CodeMirror.on(button, "blur", close);

      button.focus();
    }
    return close;
  });

  CodeMirror.defineExtension("openConfirm", function(template, callbacks, options) {
    closeNotification(this, null);
    var dialog = dialogDiv(this, template, options && options.bottom);
    var buttons = dialog.getElementsByTagName("button");
    var closed = false, me = this, blurring = 1;
    function close() {
      if (closed) return;
      closed = true;
      CodeMirror.rmClass(dialog.parentNode, 'dialog-opened');
      dialog.parentNode.removeChild(dialog);
      me.focus();
    }
    buttons[0].focus();
    for (var i = 0; i < buttons.length; ++i) {
      var b = buttons[i];
      (function(callback) {
        CodeMirror.on(b, "click", function(e) {
          CodeMirror.e_preventDefault(e);
          close();
          if (callback) callback(me);
        });
      })(callbacks[i]);
      CodeMirror.on(b, "blur", function() {
        --blurring;
        setTimeout(function() { if (blurring <= 0) close(); }, 200);
      });
      CodeMirror.on(b, "focus", function() { ++blurring; });
    }
  });

  /*
   * openNotification
   * Opens a notification, that can be closed with an optional timer
   * (default 5000ms timer) and always closes on click.
   *
   * If a notification is opened while another is opened, it will close the
   * currently opened one and open the new one immediately.
   */
  CodeMirror.defineExtension("openNotification", function(template, options) {
    closeNotification(this, close);
    var dialog = dialogDiv(this, template, options && options.bottom);
    var closed = false, doneTimer;
    var duration = options && typeof options.duration !== "undefined" ? options.duration : 5000;

    function close() {
      if (closed) return;
      closed = true;
      clearTimeout(doneTimer);
      CodeMirror.rmClass(dialog.parentNode, 'dialog-opened');
      dialog.parentNode.removeChild(dialog);
    }

    CodeMirror.on(dialog, 'click', function(e) {
      CodeMirror.e_preventDefault(e);
      close();
    });

    if (duration)
      doneTimer = setTimeout(close, duration);

    return close;
  });
});
