/*!
 * 镜衡万象 UI 组件 v3.1.0（Symvolve）
 * 镜衡万象 · 原生 Web Components（零依赖，经典脚本，浏览器直接可用）
 *
 * 契约来源：
 *   06-design-language/contracts/components.json
 *   06-design-language/contracts/content.json
 *   06-design-language/generated/tokens.css（唯一色彩 / 字体 / 间距 / 形状 / 动效来源）
 *
 * 约束：
 *   - 组件内不出现任何裸色值 / 字体名；颜色只消费 --sv-* 语义令牌。
 *   - 无 box-shadow / 无渐变 / 无发光。
 *   - 动效只有 rotate(180deg)，时长 240 / 600ms，prefers-reduced-motion 下关闭。
 *   - 状态 = 形状 + 文字，颜色只是辅助；失败 = 实心方块 + 文案「失败」。
 *   - 本文件以经典脚本加载（非 ES module），file:// 直接打开也可运行。
 *   - 中文优先（DL-020）：组件内不出现英文眉标；API 名（sv-* / 属性 / 事件）保持英文。
 *   - W 符（DL-021）不进入组件内部：点缀只由页面顶栏 / 页脚承担。
 *   - DOMContentLoaded 后自动定义；重复引入不报错。
 *
 * 公开接口：window.SVComponents
 */
(function () {
  'use strict';

  if (typeof window === 'undefined' || !window.customElements || !window.ShadowRoot) return;

  var VERSION = '3.1.0';

  /* 重复引入：同版本直接退出；不同版本同名组件保留先定义者，不报错。 */
  if (window.SVComponents && window.SVComponents.version === VERSION) return;

  /* ---------------------------------------------------------------
   * 共享样式：所有组件只消费 --sv-* 令牌
   * ------------------------------------------------------------- */
  var COMMON = [
    ':host{box-sizing:border-box;}',
    ':host([hidden]){display:none !important;}',
    '*,*::before,*::after{box-sizing:border-box;}',
    ':host{font-family:var(--sv-font-zh);-webkit-font-smoothing:antialiased;}',
    ':host{--_ring:var(--sv-border-width-strong) solid var(--sv-focus-ring);',
    '--_ring-offset:calc(var(--sv-space-4) / 2);}',
    '.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;',
    'clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0;}',
  ].join('');

  /* 动效降级：reduce 下全部静态，信息由文字承载 */
  var REDUCED = '@media (prefers-reduced-motion: reduce){*,*::before,*::after{animation:none !important;transition:none !important;}}';

  /* 默认文案取自 contracts/content.json；均可用属性覆盖 */
  var DEFAULT_LABELS = {
    loading: '处理中',
    status: {
      running: '运行中',
      done: '已完成',
      failed: '失败',
      paused: '已暂停',
      idle: '空闲',
      attention: '待复核',
    },
    dismiss: '关闭',
    remove: '关闭',
  };

  /* 主标记 Σ（SYM 扁平母版，DL-019；与 ui/ 页面字标同源，ink 单色，16px 起同一形态） */
  var BRAND_MARK_SVG =
    '<svg viewBox="0 0 200 200" aria-hidden="true" focusable="false">' +
    '<path d="M52,34 H148 M52,34 L100,100 L52,166 M52,166 H148 M52,100 H148" fill="none" ' +
    'stroke="currentColor" stroke-width="7" stroke-linecap="butt" stroke-linejoin="miter"/></svg>';

  var CLOSE_ICON_SVG =
    '<svg viewBox="0 0 10 10" aria-hidden="true" focusable="false">' +
    '<path d="M2 2l6 6M8 2l-6 6" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';

  var CHEVRON_SVG =
    '<svg viewBox="0 0 10 6" aria-hidden="true" focusable="false">' +
    '<path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';

  /* ---------------------------------------------------------------
   * 工具
   * ------------------------------------------------------------- */
  var _uid = 0;
  function nextId(prefix) {
    _uid += 1;
    return prefix + '-' + _uid;
  }

  function define(name, ctor) {
    if (!customElements.get(name)) {
      try { customElements.define(name, ctor); } catch (err) { /* 同名已定义：忽略 */ }
    }
  }

  function emit(el, type, detail) {
    el.dispatchEvent(new CustomEvent(type, { detail: detail || null, bubbles: true, composed: true }));
  }

  function safeInternals(el) {
    try {
      if (typeof el.attachInternals === 'function') return el.attachInternals();
    } catch (err) { /* 浏览器不支持表单关联：降级为纯 UI 组件 */ }
    return null;
  }

  function setFormValue(internals, value) {
    if (!internals) return;
    try { internals.setFormValue(value == null ? '' : String(value)); } catch (err) { /* noop */ }
  }

  function firstText(el, fallback) {
    var text = (el.textContent || '').trim();
    return text || fallback || '';
  }

  /* ---------------------------------------------------------------
   * sv-button
   * variant=primary|secondary(默认)|quiet  size=sm|md(默认)|lg
   * 状态：default / hover / active / focus-visible / disabled / loading
   * 预览固定态（文档与截图用）：preview-state=hover|active|focus-visible
   * ------------------------------------------------------------- */
  var BUTTON_CSS = [
    ':host{display:inline-block;vertical-align:middle;max-width:100%;}',
    '.btn{position:relative;display:inline-flex;align-items:center;justify-content:center;',
    'gap:var(--sv-space-8);min-height:calc(var(--sv-space-32) + var(--sv-space-8));max-width:100%;',
    'padding:0 calc(var(--sv-space-16) + var(--sv-space-4));',
    'border:var(--sv-border-width-thin) solid var(--sv-border-control);',
    'border-radius:var(--sv-radius-sm);background:var(--sv-surface-canvas);color:var(--sv-text-primary);',
    'font-family:var(--sv-font-zh);font-size:var(--sv-text-body-m);font-weight:var(--sv-weight-medium);',
    'line-height:var(--sv-leading-heading);letter-spacing:var(--sv-tracking-body);',
    'text-align:center;white-space:nowrap;cursor:pointer;-webkit-appearance:none;appearance:none;}',
    '.btn:hover{background:var(--sv-surface-sunken);border-color:var(--sv-border-strong);}',
    '.btn:active,:host([preview-state="active"]) .btn{border-width:var(--sv-border-width-strong);}',
    '.btn:focus-visible,:host([preview-state="focus-visible"]) .btn{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    /* primary：反色填充，每屏至多一个 */
    ':host([variant="primary"]) .btn{background:var(--sv-surface-inverse);color:var(--sv-text-inverse-primary);',
    'border-color:var(--sv-surface-inverse);}',
    ':host([variant="primary"]) .btn:hover{background:var(--sv-surface-canvas);color:var(--sv-text-primary);',
    'border-color:var(--sv-border-strong);}',
    /* quiet：行内可逆操作，hover 加下划线 */
    ':host([variant="quiet"]) .btn{background:transparent;border-color:transparent;color:var(--sv-text-secondary);}',
    ':host([variant="quiet"]) .btn:hover,:host([variant="quiet"][preview-state="hover"]) .btn{',
    'color:var(--sv-text-primary);text-decoration:underline;',
    'text-underline-offset:calc(var(--sv-space-4) / 2);text-decoration-thickness:var(--sv-border-width-thin);}',
    /* sizes */
    ':host([size="sm"]) .btn{min-height:var(--sv-space-32);padding:0 var(--sv-space-12);font-size:var(--sv-text-caption);}',
    ':host([size="lg"]) .btn{min-height:var(--sv-space-48);padding:0 var(--sv-space-24);font-size:var(--sv-text-body-l);}',
    /* disabled：降对比 + 保留文案 + not-allowed 线索 */
    '.btn:disabled,:host([disabled]) .btn{background:var(--sv-disabled-surface);color:var(--sv-disabled-text);',
    'border-color:var(--sv-disabled-border);cursor:not-allowed;}',
    ':host([variant="primary"]) .btn:disabled,:host([variant="primary"][disabled]) .btn{',
    'background:var(--sv-disabled-surface);color:var(--sv-disabled-text);border-color:var(--sv-disabled-border);}',
    /* hover 预览固定态（variant 覆盖之上） */
    ':host([preview-state="hover"]) .btn{background:var(--sv-surface-sunken);border-color:var(--sv-border-strong);}',
    ':host([preview-state="hover"][variant="primary"]) .btn{background:var(--sv-surface-canvas);',
    'color:var(--sv-text-primary);border-color:var(--sv-border-strong);}',
    /* loading：保留原宽度，弧段 rotate（240ms 循环），文案换「处理中」 */
    '.arc{display:none;width:var(--sv-space-16);height:var(--sv-space-16);flex:none;',
    'border:var(--sv-border-width-mid) solid currentColor;border-radius:var(--sv-radius-full);',
    'border-right-color:transparent;border-bottom-color:transparent;}',
    ':host([loading]) .arc{display:inline-block;animation:sv-rotate-180 var(--sv-duration-micro) linear infinite;}',
    '.labels{display:grid;place-items:center;min-width:0;}',
    '.label,.label-loading{grid-area:1 / 1;white-space:nowrap;}',
    '.label-loading{visibility:hidden;}',
    ':host([loading]) .label{visibility:hidden;}',
    ':host([loading]) .label-loading{visibility:visible;}',
    '@keyframes sv-rotate-180{from{transform:rotate(0deg);}to{transform:rotate(180deg);}}',
  ].join('');

  var BUTTON_TEMPLATE =
    '<style>' + COMMON + BUTTON_CSS + REDUCED + '</style>' +
    '<button class="btn" part="button" type="button">' +
    '<span class="arc" part="arc" aria-hidden="true"></span>' +
    '<span class="labels">' +
    '<span class="label" part="label"><slot><span class="label-default"></span></slot></span>' +
    '<span class="label-loading" part="loading-label"></span>' +
    '</span>' +
    '</button>';

  class SvButton extends HTMLElement {
    static get formAssociated() { return true; }

    static get observedAttributes() {
      return ['variant', 'size', 'disabled', 'loading', 'label', 'loading-label', 'type', 'pressed', 'aria-label'];
    }

    constructor() {
      super();
      this._internals = safeInternals(this);
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = BUTTON_TEMPLATE;
      this._btn = root.querySelector('.btn');
      this._loadingLabel = root.querySelector('.label-loading');
      this._labelDefault = root.querySelector('.label-default');
      this._btn.addEventListener('click', this._onClick.bind(this));
    }

    connectedCallback() { this._update(); }
    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._btn) return;
      var type = (this.getAttribute('type') || 'button').toLowerCase();
      this._btn.setAttribute('type', /^(submit|reset|button)$/.test(type) ? type : 'button');
      var disabled = this.hasAttribute('disabled');
      var loading = this.hasAttribute('loading');
      this._btn.disabled = disabled;
      if (loading) this._btn.setAttribute('aria-busy', 'true');
      else this._btn.removeAttribute('aria-busy');
      if (this._labelDefault) this._labelDefault.textContent = this.getAttribute('label') || '';
      this._loadingLabel.textContent = this.getAttribute('loading-label') || DEFAULT_LABELS.loading;
      var ariaLabel = this.getAttribute('aria-label');
      if (ariaLabel) this._btn.setAttribute('aria-label', ariaLabel);
      else this._btn.removeAttribute('aria-label');
      if (this.hasAttribute('pressed')) {
        this._btn.setAttribute('aria-pressed', this.getAttribute('pressed') === 'false' ? 'false' : 'true');
      } else {
        this._btn.removeAttribute('aria-pressed');
      }
    }

    _onClick(event) {
      if (this.hasAttribute('disabled') || this.hasAttribute('loading')) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      var type = (this.getAttribute('type') || 'button').toLowerCase();
      var form = this._internals ? this._internals.form : null;
      if (!form) return;
      if (type === 'submit') {
        event.preventDefault();
        if (typeof form.requestSubmit === 'function') form.requestSubmit();
        else if (typeof form.submit === 'function') form.submit();
      } else if (type === 'reset') {
        event.preventDefault();
        if (typeof form.reset === 'function') form.reset();
      }
    }

    get disabled() { return this.hasAttribute('disabled'); }
    set disabled(value) { if (value) this.setAttribute('disabled', ''); else this.removeAttribute('disabled'); }
    get loading() { return this.hasAttribute('loading'); }
    set loading(value) { if (value) this.setAttribute('loading', ''); else this.removeAttribute('loading'); }
    get form() { return this._internals ? this._internals.form : null; }
  }

  /* ---------------------------------------------------------------
   * sv-input
   * 状态：default / hover / focus / filled / error / disabled
   * 属性：label hint error placeholder value name type size(required)
   *      disabled readonly required autocomplete inputmode maxlength preview-state
   * 事件：sv-input（每次输入）/ sv-change（失焦提交）/ sv-enter（回车）
   * ------------------------------------------------------------- */
  var FIELD_CSS = [
    '.field{display:flex;align-items:center;min-height:calc(var(--sv-space-32) + var(--sv-space-8));',
    'padding:0 var(--sv-space-12);background:var(--sv-surface-canvas);',
    'border:var(--sv-border-width-thin) solid var(--sv-border-control);border-radius:var(--sv-radius-sm);}',
    '.field:hover,:host([preview-state="hover"]) .field{border-color:var(--sv-border-strong);}',
    '.field:focus-within,:host([preview-state="focus"]) .field{border-color:var(--sv-border-strong);',
    'outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    '.input{flex:1;min-width:0;padding:0;border:0;outline:0;background:transparent;',
    'color:var(--sv-text-primary);font-family:var(--sv-font-zh);font-size:var(--sv-text-body-m);',
    'line-height:var(--sv-leading-body);-webkit-appearance:none;appearance:none;}',
    '.input::placeholder{color:var(--sv-text-secondary);opacity:1;}',
    '.input:read-only{color:var(--sv-text-secondary);}',
    ':host([disabled]) .field{background:var(--sv-disabled-surface);border-color:var(--sv-disabled-border);cursor:not-allowed;}',
    ':host([disabled]) .input{color:var(--sv-disabled-text);cursor:not-allowed;}',
    ':host([disabled]) .lbl{color:var(--sv-disabled-text);}',
    ':host([error]) .field{border-width:var(--sv-border-width-strong);border-color:var(--sv-border-strong);}',
    ':host([error]) .hint{display:none;}',
    '.lbl{display:block;font-size:var(--sv-text-caption);font-weight:var(--sv-weight-medium);',
    'line-height:var(--sv-leading-caption);color:var(--sv-text-secondary);}',
    '.hint{display:block;font-size:var(--sv-text-caption);line-height:var(--sv-leading-caption);',
    'color:var(--sv-text-secondary);}',
    '.error{display:none;align-items:center;gap:var(--sv-space-8);font-size:var(--sv-text-caption);',
    'line-height:var(--sv-leading-caption);color:var(--sv-text-primary);}',
    ':host([error]) .error{display:flex;}',
    '.err-mark{width:var(--sv-space-8);height:var(--sv-space-8);flex:none;background:var(--sv-border-strong);}',
    ':host([size="lg"]) .field{min-height:var(--sv-space-48);padding:0 var(--sv-space-16);}',
    ':host([size="lg"]) .input{font-size:var(--sv-text-body-l);}',
  ].join('');

  var INPUT_TEMPLATE =
    '<style>' + COMMON +
    ':host{display:block;}' +
    '.wrap{display:flex;flex-direction:column;gap:var(--sv-space-8);}' +
    FIELD_CSS + REDUCED + '</style>' +
    '<div class="wrap" part="wrap">' +
    '<label class="lbl" part="label"></label>' +
    '<span class="field" part="field"><input class="input" part="input" type="text"/></span>' +
    '<span class="hint" part="hint"></span>' +
    '<span class="error" part="error" role="alert">' +
    '<span class="err-mark" part="error-mark" aria-hidden="true"></span>' +
    '<span class="err-text" part="error-text"></span>' +
    '</span>' +
    '</div>';

  var INPUT_TYPES = ['text', 'email', 'password', 'search', 'url', 'tel', 'number'];

  class SvInput extends HTMLElement {
    static get formAssociated() { return true; }

    static get observedAttributes() {
      return ['label', 'hint', 'error', 'placeholder', 'value', 'name', 'type', 'size', 'required',
        'disabled', 'readonly', 'autocomplete', 'inputmode', 'maxlength', 'preview-state', 'aria-label'];
    }

    constructor() {
      super();
      this._uid = nextId('sv-input');
      this._internals = safeInternals(this);
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = INPUT_TEMPLATE;
      this._input = root.querySelector('.input');
      this._label = root.querySelector('.lbl');
      this._hint = root.querySelector('.hint');
      this._error = root.querySelector('.error');
      this._errText = root.querySelector('.err-text');
      this._uidEls = {
        input: this._uid + '-input',
        hint: this._uid + '-hint',
        error: this._uid + '-error',
      };
      this._input.id = this._uidEls.input;
      this._hint.id = this._uidEls.hint;
      this._error.id = this._uidEls.error;
      this._label.setAttribute('for', this._uidEls.input);
      this._input.addEventListener('input', this._onInput.bind(this));
      this._input.addEventListener('change', this._onChange.bind(this));
      this._input.addEventListener('keydown', this._onKeydown.bind(this));
    }

    connectedCallback() {
      this._update();
      this._syncForm();
    }

    attributeChangedCallback(name, oldValue, newValue) {
      if (name === 'value' && this._input && newValue !== null && this._input.value !== newValue) {
        this._input.value = newValue;
      }
      this._update();
      if (name === 'value' || name === 'required' || name === 'error') this._syncForm();
    }

    _update() {
      if (!this._input) return;
      var input = this._input;
      var labelText = this.getAttribute('label') || '';
      var hintText = this.getAttribute('hint') || '';
      var errorText = this.getAttribute('error') || '';
      var type = (this.getAttribute('type') || 'text').toLowerCase();
      input.type = INPUT_TYPES.indexOf(type) > -1 ? type : 'text';
      input.placeholder = this.getAttribute('placeholder') || '';
      input.required = this.hasAttribute('required');
      input.disabled = this.hasAttribute('disabled');
      input.readOnly = this.hasAttribute('readonly');
      var autocomplete = this.getAttribute('autocomplete');
      if (autocomplete) input.setAttribute('autocomplete', autocomplete);
      else input.removeAttribute('autocomplete');
      var inputmode = this.getAttribute('inputmode');
      if (inputmode) input.setAttribute('inputmode', inputmode);
      else input.removeAttribute('inputmode');
      var maxlength = this.getAttribute('maxlength');
      if (maxlength) input.setAttribute('maxlength', maxlength);
      else input.removeAttribute('maxlength');

      this._label.textContent = labelText;
      this._label.hidden = !labelText;
      this._hint.textContent = hintText;
      this._hint.hidden = !hintText;
      this._errText.textContent = errorText;

      var ariaLabel = this.getAttribute('aria-label') || labelText;
      if (ariaLabel) input.setAttribute('aria-label', ariaLabel);
      else input.removeAttribute('aria-label');

      if (errorText) input.setAttribute('aria-invalid', 'true');
      else input.removeAttribute('aria-invalid');

      var described = [];
      if (hintText) described.push(this._uidEls.hint);
      if (errorText) described.push(this._uidEls.error);
      if (described.length) input.setAttribute('aria-describedby', described.join(' '));
      else input.removeAttribute('aria-describedby');
    }

    _onInput() {
      emit(this, 'sv-input', { value: this.value });
      this._syncForm();
    }

    _onChange() { emit(this, 'sv-change', { value: this.value }); }

    _onKeydown(event) {
      if (event.key === 'Enter') emit(this, 'sv-enter', { value: this.value });
    }

    _syncForm() {
      var internals = this._internals;
      if (!internals || !this._input) return;
      try {
        setFormValue(internals, this._input.value);
        var errorText = this.getAttribute('error');
        if (errorText) {
          internals.setValidity({ customError: true }, errorText, this._input);
          return;
        }
        var validity = this._input.validity;
        if (validity.valid) {
          internals.setValidity({});
          return;
        }
        var flags = {};
        [
          'valueMissing', 'typeMismatch', 'patternMismatch', 'tooLong', 'tooShort',
          'rangeUnderflow', 'rangeOverflow', 'stepMismatch', 'badInput', 'customError',
        ].forEach(function (key) { if (validity[key]) flags[key] = true; });
        internals.setValidity(flags, this._input.validationMessage, this._input);
      } catch (err) { /* 浏览器不支持表单关联 */ }
    }

    get value() { return this._input ? this._input.value : ''; }
    set value(value) { if (this._input) this._input.value = value == null ? '' : String(value); this._syncForm(); }
    get form() { return this._internals ? this._internals.form : null; }
    focus() { if (this._input) this._input.focus(); }
    blur() { if (this._input) this._input.blur(); }
    select() { if (this._input) this._input.select(); }
    checkValidity() { return this._input ? this._input.checkValidity() : true; }
    reportValidity() { return this._input ? this._input.reportValidity() : true; }
  }

  /* ---------------------------------------------------------------
   * sv-select
   * 子元素 <option> 提供选项（与原生 select 同形）。
   * 状态：default / hover / open / selected / disabled
   * 属性：label value disabled open preview-state
   * 事件：sv-change / sv-open / sv-close
   * 键盘：Enter|Space 打开/选中，↑↓ 移动，Home/End，Esc 关闭，字母跳转
   * ------------------------------------------------------------- */
  var SELECT_CSS = [
    ':host{display:block;}',
    '.wrap{display:flex;flex-direction:column;gap:var(--sv-space-8);}',
    /* 选项由子元素 <option> 提供，只作数据源，不参与渲染 */
    '.src{display:none;}',
    '::slotted(option){display:none;}',
    '.lbl{display:block;font-size:var(--sv-text-caption);font-weight:var(--sv-weight-medium);',
    'line-height:var(--sv-leading-caption);color:var(--sv-text-secondary);}',
    '.lbl[hidden]{display:none;}',
    '.ctl{position:relative;}',
    '.trigger{display:flex;align-items:center;justify-content:space-between;gap:var(--sv-space-8);',
    'width:100%;min-height:calc(var(--sv-space-32) + var(--sv-space-8));padding:0 var(--sv-space-12);',
    'border:var(--sv-border-width-thin) solid var(--sv-border-control);border-radius:var(--sv-radius-sm);',
    'background:var(--sv-surface-canvas);color:var(--sv-text-primary);font-family:var(--sv-font-zh);',
    'font-size:var(--sv-text-body-m);font-weight:var(--sv-weight-regular);line-height:var(--sv-leading-heading);',
    'text-align:left;cursor:pointer;-webkit-appearance:none;appearance:none;}',
    '.trigger:hover,:host([preview-state="hover"]) .trigger{background:var(--sv-surface-sunken);',
    'border-color:var(--sv-border-strong);}',
    '.trigger:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    ':host([disabled]) .trigger{background:var(--sv-disabled-surface);color:var(--sv-disabled-text);',
    'border-color:var(--sv-disabled-border);cursor:not-allowed;}',
    ':host([disabled]) .lbl{color:var(--sv-disabled-text);}',
    '.value{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
    '.chev{display:inline-flex;width:calc(var(--sv-space-8) + var(--sv-space-4));',
    'height:calc(var(--sv-space-8) + var(--sv-space-4));flex:none;align-items:center;justify-content:center;',
    'color:var(--sv-text-primary);transform:rotate(0deg);',
    'transition:transform var(--sv-duration-state) var(--sv-ease-standard);}',
    '.chev svg{width:100%;height:auto;display:block;}',
    ':host([open]) .chev{transform:rotate(180deg);}',
    ':host([disabled]) .chev{color:var(--sv-disabled-text);}',
    '.menu{position:absolute;top:calc(100% + var(--sv-space-4));left:0;right:0;z-index:20;',
    'margin:0;padding:var(--sv-space-4) 0;list-style:none;background:var(--sv-surface-raised);',
    'border:var(--sv-border-width-thin) solid var(--sv-border-strong);border-radius:var(--sv-radius-sm);',
    'max-height:calc((var(--sv-space-32) + var(--sv-space-8)) * 6);overflow:auto;}',
    '.menu[hidden]{display:none;}',
    '.option{display:flex;align-items:center;min-height:var(--sv-space-32);padding:0 var(--sv-space-12);',
    'font-size:var(--sv-text-body-m);line-height:var(--sv-leading-heading);color:var(--sv-text-primary);',
    'border-left:var(--sv-border-width-strong) solid transparent;cursor:pointer;}',
    '.option:hover,.option[data-active]{background:var(--sv-surface-sunken);}',
    '.option[aria-selected="true"]{border-left-color:var(--sv-border-strong);}',
  ].join('');

  var SELECT_TEMPLATE =
    '<style>' + COMMON + SELECT_CSS + REDUCED + '</style>' +
    '<div class="wrap">' +
    '<span class="lbl" part="label"></span>' +
    '<div class="ctl">' +
    '<button class="trigger" part="trigger" type="button" role="combobox" aria-haspopup="listbox" ' +
    'aria-expanded="false" aria-controls="">' +
    '<span class="value" part="value"></span>' +
    '<span class="chev" part="chevron">' + CHEVRON_SVG + '</span>' +
    '</button>' +
    '<div class="menu" part="menu" role="listbox" tabindex="-1" hidden></div>' +
    '</div>' +
    '<slot class="src"></slot>' +
    '</div>';

  class SvSelect extends HTMLElement {
    static get formAssociated() { return true; }

    static get observedAttributes() {
      return ['label', 'value', 'disabled', 'open', 'preview-state', 'name'];
    }

    constructor() {
      super();
      this._internals = safeInternals(this);
      this._opts = [];
      this._value = '';
      this._open = false;
      this._activeIndex = -1;
      this._uid = nextId('sv-select');
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = SELECT_TEMPLATE;
      this._trigger = root.querySelector('.trigger');
      this._valueEl = root.querySelector('.value');
      this._label = root.querySelector('.lbl');
      this._menu = root.querySelector('.menu');
      this._slot = root.querySelector('slot.src');
      this._menu.id = this._uid + '-menu';
      this._label.id = this._uid + '-label';
      this._valueEl.id = this._uid + '-value';
      this._trigger.setAttribute('aria-controls', this._menu.id);
      this._trigger.setAttribute('aria-labelledby', this._label.id + ' ' + this._valueEl.id);
      this._onDocPointerDown = this._handleDocPointerDown.bind(this);
      this._trigger.addEventListener('click', this._onTriggerClick.bind(this));
      this._trigger.addEventListener('keydown', this._onKeydown.bind(this));
      this._slot.addEventListener('slotchange', this._readOptions.bind(this));
    }

    connectedCallback() {
      this._readOptions();
      this._syncAll();
      this._open = this.hasAttribute('open') && !this.hasAttribute('disabled');
      this._applyOpen(false);
    }

    disconnectedCallback() {
      this._listenDoc(false);
    }

    attributeChangedCallback(name) {
      if (!this._menu) return;
      this._syncAll();
      if (name === 'open' || name === 'disabled') {
        var next = this.hasAttribute('open') && !this.hasAttribute('disabled');
        if (next !== this._open) {
          this._open = next;
          this._applyOpen(this.isConnected);
        }
      }
    }

    _readOptions() {
      var options = Array.prototype.slice.call(this.querySelectorAll(':scope > option'));
      this._opts = options.map(function (option) {
        var label = (option.textContent || '').trim();
        return {
          value: option.hasAttribute('value') ? option.getAttribute('value') : label,
          label: label,
        };
      });
      var host = this;
      while (this._menu.firstChild) this._menu.removeChild(this._menu.firstChild);
      this._opts.forEach(function (option, index) {
        var item = document.createElement('div');
        item.className = 'option';
        item.setAttribute('role', 'option');
        item.setAttribute('id', host._uid + '-opt-' + index);
        item.setAttribute('aria-selected', option.value === host._value ? 'true' : 'false');
        item.textContent = option.label;
        item.addEventListener('click', function () { host._commit(index); });
        item.addEventListener('pointerenter', function () { host._setActive(index); });
        host._menu.appendChild(item);
      });
      this._syncValueFromAttribute();
      this._renderValue();
      this._syncForm();
    }

    _syncAll() {
      if (!this._trigger) return;
      var labelText = this.getAttribute('label') || '';
      this._label.textContent = labelText;
      this._label.hidden = !labelText;
      var disabled = this.hasAttribute('disabled');
      this._trigger.disabled = disabled;
      this._trigger.setAttribute('aria-disabled', disabled ? 'true' : 'false');
      this._syncValueFromAttribute();
      this._renderValue();
    }

    _syncValueFromAttribute() {
      if (!this._opts.length) return;
      var value = this.getAttribute('value');
      var exists = value !== null && this._opts.some(function (option) { return option.value === value; });
      if (!exists) value = this._opts[0].value;
      if (value !== this._value) {
        this._value = value;
        this._renderSelection();
        this._syncForm();
      }
    }

    _renderValue() {
      if (!this._valueEl) return;
      var selected = null;
      for (var i = 0; i < this._opts.length; i += 1) {
        if (this._opts[i].value === this._value) { selected = this._opts[i]; break; }
      }
      this._valueEl.textContent = selected ? selected.label : '';
    }

    _renderSelection() {
      this._renderValue();
      var value = this._value;
      Array.prototype.forEach.call(this._menu.querySelectorAll('.option'), function (item, index) {
        var option = this._opts[index];
        item.setAttribute('aria-selected', option && option.value === value ? 'true' : 'false');
      }, this);
    }

    _syncForm() { setFormValue(this._internals, this._value); }

    _onTriggerClick() {
      if (this.hasAttribute('disabled')) return;
      this._setOpen(!this._open);
    }

    _setOpen(open) {
      if (open && this.hasAttribute('disabled')) return;
      if (open === this._open) return;
      this._open = open;
      this.toggleAttribute('open', open);
      this._applyOpen(true);
    }

    _applyOpen(emitEvent) {
      if (!this._menu) return;
      this._menu.hidden = !this._open;
      this._trigger.setAttribute('aria-expanded', this._open ? 'true' : 'false');
      if (this._open) {
        this._listenDoc(true);
        var selectedIndex = 0;
        for (var i = 0; i < this._opts.length; i += 1) {
          if (this._opts[i].value === this._value) { selectedIndex = i; break; }
        }
        this._setActive(selectedIndex);
        if (emitEvent) emit(this, 'sv-open', { value: this._value });
      } else {
        this._listenDoc(false);
        this._trigger.removeAttribute('aria-activedescendant');
        this._activeIndex = -1;
        Array.prototype.forEach.call(this._menu.querySelectorAll('.option'), function (item) {
          item.removeAttribute('data-active');
        });
        if (emitEvent) emit(this, 'sv-close', { value: this._value });
      }
    }

    _listenDoc(on) {
      if (on) document.addEventListener('pointerdown', this._onDocPointerDown, true);
      else document.removeEventListener('pointerdown', this._onDocPointerDown, true);
    }

    _handleDocPointerDown(event) {
      if (!this.contains(event.target)) this._setOpen(false);
    }

    _setActive(index) {
      if (!this._opts.length) return;
      var next = Math.max(0, Math.min(this._opts.length - 1, index));
      this._activeIndex = next;
      Array.prototype.forEach.call(this._menu.querySelectorAll('.option'), function (item, i) {
        if (i === next) item.setAttribute('data-active', '');
        else item.removeAttribute('data-active');
      });
      var active = this._menu.children[next];
      if (active) {
        this._trigger.setAttribute('aria-activedescendant', active.id);
        if (typeof active.scrollIntoView === 'function') {
          try { active.scrollIntoView({ block: 'nearest' }); } catch (err) { /* noop */ }
        }
      }
    }

    _move(delta) {
      this._setActive(this._activeIndex + delta);
    }

    _commit(index) {
      var option = this._opts[index];
      if (!option) return;
      this._value = option.value;
      this.setAttribute('value', option.value);
      this._renderSelection();
      this._syncForm();
      this._setOpen(false);
      emit(this, 'sv-change', { value: option.value, label: option.label });
      this._trigger.focus();
    }

    _onKeydown(event) {
      var key = event.key;
      if (key === 'Escape') {
        if (this._open) { event.preventDefault(); this._setOpen(false); }
        return;
      }
      if (key === 'Tab') {
        if (this._open) this._setOpen(false);
        return;
      }
      if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
        event.preventDefault();
        if (this._open) this._commit(this._activeIndex);
        else this._setOpen(true);
        return;
      }
      if (key === 'ArrowDown') {
        event.preventDefault();
        if (!this._open) this._setOpen(true);
        else this._move(1);
        return;
      }
      if (key === 'ArrowUp') {
        event.preventDefault();
        if (!this._open) {
          this._setOpen(true);
          this._setActive(this._opts.length - 1);
        } else {
          this._move(-1);
        }
        return;
      }
      if (this._open && (key === 'Home' || key === 'End')) {
        event.preventDefault();
        this._setActive(key === 'Home' ? 0 : this._opts.length - 1);
        return;
      }
      if (this._open && typeof key === 'string' && key.length === 1 && /\S/.test(key)) {
        var needle = key.toLowerCase();
        for (var offset = 1; offset <= this._opts.length; offset += 1) {
          var index = (this._activeIndex + offset) % this._opts.length;
          var label = this._opts[index].label.toLowerCase();
          if (label.charAt(0) === needle) { this._setActive(index); break; }
        }
      }
    }

    get value() { return this._value; }
    set value(value) {
      if (value == null) this.removeAttribute('value');
      else this.setAttribute('value', String(value));
    }
    get open() { return this._open; }
    set open(value) { this._setOpen(!!value); }
    get selectedIndex() {
      for (var i = 0; i < this._opts.length; i += 1) {
        if (this._opts[i].value === this._value) return i;
      }
      return -1;
    }
    get form() { return this._internals ? this._internals.form : null; }
    focus() { if (this._trigger) this._trigger.focus(); }
  }

  /* ---------------------------------------------------------------
   * sv-tag
   * 元数据标签；宽度随内容，高度 32（可移除按钮保持 32×32 命中区）。
   * variant=neutral(默认)|strong|inverse   mono  removable
   * 事件：sv-remove
   * ------------------------------------------------------------- */
  var TAG_CSS = [
    ':host{display:inline-block;vertical-align:middle;max-width:100%;}',
    '.tag{display:inline-flex;align-items:center;gap:var(--sv-space-4);height:var(--sv-space-32);',
    'max-width:100%;padding:0 var(--sv-space-8);border:var(--sv-border-width-thin) solid var(--sv-border-hairline);',
    'border-radius:var(--sv-radius-sm);background:var(--sv-surface-sunken);color:var(--sv-text-secondary);',
    'font-family:var(--sv-font-zh);font-size:var(--sv-text-caption);line-height:var(--sv-leading-caption);}',
    '.txt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
    ':host([variant="strong"]) .tag{background:transparent;border-color:var(--sv-border-control);',
    'color:var(--sv-text-primary);}',
    ':host([variant="inverse"]) .tag{background:var(--sv-surface-inverse);border-color:var(--sv-border-inverse);',
    'color:var(--sv-text-inverse-primary);}',
    ':host([mono]) .tag{font-family:var(--sv-font-mono);font-size:var(--sv-text-micro);',
    'letter-spacing:var(--sv-tracking-codename);}',
    '.tag:hover,:host([preview-state="hover"]) .tag{border-color:var(--sv-border-strong);',
    'color:var(--sv-text-primary);}',
    ':host([variant="inverse"]) .tag:hover,:host([variant="inverse"][preview-state="hover"]) .tag{',
    'border-color:var(--sv-border-inverse-strong);color:var(--sv-text-inverse-primary);}',
    '.x{display:inline-flex;align-items:center;justify-content:center;width:var(--sv-space-32);',
    'height:var(--sv-space-32);flex:none;margin-right:calc(-1 * var(--sv-space-8));padding:0;border:0;',
    'background:transparent;color:currentColor;cursor:pointer;border-radius:var(--sv-radius-sm);}',
    '.x[hidden]{display:none;}',
    '.x:hover{background:var(--sv-disabled-surface);}',
    '.x:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    '.x svg{width:calc(var(--sv-space-8) + var(--sv-space-4));height:auto;display:block;}',
    ':host([disabled]) .tag{background:var(--sv-disabled-surface);border-color:var(--sv-disabled-border);',
    'color:var(--sv-disabled-text);}',
    ':host([disabled]) .x{cursor:not-allowed;}',
  ].join('');

  var TAG_TEMPLATE =
    '<style>' + COMMON + TAG_CSS + REDUCED + '</style>' +
    '<span class="tag" part="tag">' +
    '<span class="txt" part="label"><slot></slot></span>' +
    '<button class="x" part="remove" type="button" hidden>' + CLOSE_ICON_SVG + '</button>' +
    '</span>';

  class SvTag extends HTMLElement {
    static get observedAttributes() {
      return ['variant', 'mono', 'removable', 'remove-label', 'disabled', 'preview-state'];
    }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = TAG_TEMPLATE;
      this._remove = root.querySelector('.x');
      this._remove.addEventListener('click', this._onRemove.bind(this));
    }

    connectedCallback() { this._update(); }
    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._remove) return;
      var removable = this.hasAttribute('removable');
      this._remove.hidden = !removable;
      this._remove.disabled = this.hasAttribute('disabled');
      var label = this.getAttribute('remove-label') || DEFAULT_LABELS.remove;
      this._remove.setAttribute('aria-label', label);
    }

    _onRemove(event) {
      if (this.hasAttribute('disabled')) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      emit(this, 'sv-remove', null);
    }
  }

  /* ---------------------------------------------------------------
   * sv-status
   * status=running|done|failed|paused|idle|attention
   * 形状 + 文字双编码，颜色仅辅助：
   *   running 180° 弧 / done 实心圆 / failed 实心方块 / paused 双竖条 /
   *   idle 空环 / attention 圆点 + 文字标签
   * 属性：status label size(lg) compact inverse
   * ------------------------------------------------------------- */
  var STATUS_CSS = [
    ':host{display:inline-block;vertical-align:middle;}',
    '.root{display:inline-flex;align-items:center;gap:var(--sv-space-8);',
    'font-family:var(--sv-font-zh);font-size:var(--sv-text-caption);line-height:var(--sv-leading-caption);',
    'color:var(--sv-text-primary);white-space:nowrap;}',
    '.mark{position:relative;display:inline-block;width:var(--sv-space-8);height:var(--sv-space-8);',
    'flex:none;background:var(--sv-status-idle);border-radius:0;}',
    /* running：180° 弧（两块透明边）+ 240ms rotate */
    ':host([status="running"]) .mark{background:transparent;border-radius:var(--sv-radius-full);',
    'border:var(--sv-border-width-mid) solid var(--sv-status-running);',
    'border-right-color:transparent;border-bottom-color:transparent;',
    'animation:sv-status-turn var(--sv-duration-micro) linear infinite;}',
    /* done：实心圆 */
    ':host([status="done"]) .mark{background:var(--sv-status-done);border-radius:var(--sv-radius-full);}',
    /* failed：实心方块，无圆角 */
    ':host([status="failed"]) .mark{background:var(--sv-status-failed);border-radius:0;}',
    /* paused：双竖条 */
    ':host([status="paused"]) .mark{background:transparent;display:inline-flex;',
    'gap:calc(var(--sv-space-4) / 2);}',
    ':host([status="paused"]) .mark::before,:host([status="paused"]) .mark::after{',
    'content:"";flex:1;background:var(--sv-status-paused);}',
    /* idle：空环 */
    ':host([status="idle"]) .mark{background:transparent;border-radius:var(--sv-radius-full);',
    'border:var(--sv-border-width-mid) solid var(--sv-status-idle);}',
    /* attention：圆点 + 文字（paper 底必须成对） */
    ':host([status="attention"]) .mark{background:var(--sv-status-attention);border-radius:var(--sv-radius-full);}',
    /* 尺寸 */
    ':host([size="lg"]) .root{font-size:var(--sv-text-body-m);gap:var(--sv-space-12);}',
    ':host([size="lg"]) .mark{width:var(--sv-space-12);height:var(--sv-space-12);}',
    ':host([size="lg"][status="running"]) .mark{border-width:var(--sv-border-width-strong);',
    'border-right-color:transparent;border-bottom-color:transparent;}',
    ':host([size="lg"][status="idle"]) .mark{border-width:var(--sv-border-width-strong);}',
    /* compact：保留文字，去掉形状（禁止只留形状） */
    ':host([compact]) .mark{display:none;}',
    /* inverse：ink 底使用反色文字令牌；形状色由 status.* 在 ink 底不可见，改用反色令牌 */
    ':host([inverse]) .root{color:var(--sv-text-inverse-primary);}',
    ':host([inverse][status="done"]) .mark{background:var(--sv-text-inverse-primary);}',
    ':host([inverse][status="failed"]) .mark{background:var(--sv-text-inverse-primary);}',
    ':host([inverse][status="running"]) .mark{border-color:var(--sv-text-inverse-secondary);',
    'border-right-color:transparent;border-bottom-color:transparent;}',
    ':host([inverse][status="paused"]) .mark::before,:host([inverse][status="paused"]) .mark::after{',
    'background:var(--sv-text-inverse-secondary);}',
    ':host([inverse][status="idle"]) .mark{border-color:var(--sv-text-inverse-secondary);}',
    ':host([inverse][status="attention"]) .mark{background:var(--sv-status-attention);}',
    '@keyframes sv-status-turn{from{transform:rotate(0deg);}to{transform:rotate(180deg);}}',
  ].join('');

  var STATUS_TEMPLATE =
    '<style>' + COMMON + STATUS_CSS + REDUCED + '</style>' +
    '<span class="root" part="status">' +
    '<span class="mark" part="mark" aria-hidden="true"></span>' +
    '<span class="label" part="label"><slot><span class="fallback"></span></slot></span>' +
    '</span>';

  var STATUS_VALUES = ['running', 'done', 'failed', 'paused', 'idle', 'attention'];

  class SvStatus extends HTMLElement {
    static get observedAttributes() {
      return ['status', 'label', 'size', 'compact', 'inverse'];
    }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = STATUS_TEMPLATE;
      this._fallback = root.querySelector('.fallback');
    }

    connectedCallback() {
      if (!this.hasAttribute('role')) this.setAttribute('role', 'status');
      this._update();
    }

    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._fallback) return;
      var status = this.getAttribute('status') || 'idle';
      if (STATUS_VALUES.indexOf(status) < 0) status = 'idle';
      var label = this.getAttribute('label') || DEFAULT_LABELS.status[status];
      this._fallback.textContent = label;
      this.setAttribute('data-status', status);
    }

    get status() { return this.getAttribute('status') || 'idle'; }
    set status(value) { this.setAttribute('status', value); }
  }

  /* ---------------------------------------------------------------
   * sv-empty
   * 没有数据时给出下一步；Σ 标记（扁平母版）24px + 标题 + 提示 + 单个操作。
   * 属性：title hint action-label filtered
   * 事件：sv-action（内部操作按钮）
   * 插槽：title / hint / action
   * ------------------------------------------------------------- */
  var EMPTY_CSS = [
    ':host{display:block;}',
    '.empty{display:flex;flex-direction:column;align-items:center;text-align:center;',
    'gap:var(--sv-space-12);padding:var(--sv-space-64) var(--sv-space-24);',
    'max-width:calc(var(--sv-space-192) + var(--sv-space-128) + var(--sv-space-32) + var(--sv-space-8));',
    'margin:0 auto;}',
    '.mark{display:block;width:var(--sv-space-24);height:var(--sv-space-24);color:var(--sv-text-primary);}',
    '.mark svg{width:100%;height:100%;display:block;}',
    '.title{margin:0;font-family:var(--sv-font-zh);font-size:var(--sv-text-body-l);',
    'font-weight:var(--sv-weight-medium);line-height:var(--sv-leading-heading);color:var(--sv-text-primary);}',
    '.hint{margin:0;font-size:var(--sv-text-body-m);line-height:var(--sv-leading-body);',
    'color:var(--sv-text-secondary);}',
    '.act{margin-top:var(--sv-space-8);}',
    '.act[hidden]{display:none;}',
    '.btn{display:inline-flex;align-items:center;justify-content:center;',
    'min-height:calc(var(--sv-space-32) + var(--sv-space-8));padding:0 calc(var(--sv-space-16) + var(--sv-space-4));',
    'border:var(--sv-border-width-thin) solid var(--sv-border-control);border-radius:var(--sv-radius-sm);',
    'background:transparent;color:var(--sv-text-primary);font-family:var(--sv-font-zh);',
    'font-size:var(--sv-text-body-m);font-weight:var(--sv-weight-medium);',
    'line-height:var(--sv-leading-heading);cursor:pointer;}',
    '.btn:hover{background:var(--sv-surface-sunken);border-color:var(--sv-border-strong);}',
    '.btn:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
  ].join('');

  var EMPTY_TEMPLATE =
    '<style>' + COMMON + EMPTY_CSS + REDUCED + '</style>' +
    '<section class="empty" part="empty">' +
    '<span class="mark" part="mark" aria-hidden="true">' + BRAND_MARK_SVG + '</span>' +
    '<h3 class="title" part="title"><slot name="title"><span class="title-fallback"></span></slot></h3>' +
    '<p class="hint" part="hint"><slot name="hint"><span class="hint-fallback"></span></slot></p>' +
    '<div class="act" part="action"><slot name="action">' +
    '<button class="btn" part="action-button" type="button"></button></slot></div>' +
    '</section>';

  class SvEmpty extends HTMLElement {
    static get observedAttributes() {
      return ['title', 'hint', 'action-label', 'filtered'];
    }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = EMPTY_TEMPLATE;
      this._titleFallback = root.querySelector('.title-fallback');
      this._hintFallback = root.querySelector('.hint-fallback');
      this._actionWrap = root.querySelector('.act');
      this._actionButton = root.querySelector('.btn');
      this._actionSlot = root.querySelector('slot[name="action"]');
      this._actionButton.addEventListener('click', this._onAction.bind(this));
      this._actionSlot.addEventListener('slotchange', this._updateAction.bind(this));
    }

    connectedCallback() {
      if (!this.hasAttribute('role')) this.setAttribute('role', 'status');
      this._update();
    }

    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._titleFallback) return;
      this._titleFallback.textContent = this.getAttribute('title') || '';
      this._hintFallback.textContent = this.getAttribute('hint') || '';
      this._actionButton.textContent = this.getAttribute('action-label') || '';
      this._updateAction();
    }

    _updateAction() {
      if (!this._actionWrap) return;
      var hasSlot = false;
      if (this._actionSlot && typeof this._actionSlot.assignedNodes === 'function') {
        hasSlot = this._actionSlot.assignedNodes().length > 0;
      }
      var hasButton = !!this.getAttribute('action-label');
      this._actionWrap.hidden = !hasSlot && !hasButton;
    }

    _onAction() {
      if (!this.getAttribute('action-label')) return;
      emit(this, 'sv-action', null);
    }
  }

  /* ---------------------------------------------------------------
   * sv-toast
   * 非阻塞一次性结果；status=success(默认)|error|info
   * 形状：success 实心圆 / error 实心方块 / info 空环；均带文字
   * 属性：status message detail action-label dismiss-label dismissible="false"
   *      persistent duration（默认 6000）size
   * 事件：sv-action（操作按钮）/ sv-dismiss（关闭或超时，detail.reason）
   * 方法：dismiss(reason)
   * ------------------------------------------------------------- */
  var TOAST_CSS = [
    ':host{display:block;}',
    '.card{position:relative;display:flex;align-items:flex-start;gap:var(--sv-space-12);',
    'min-width:calc(var(--sv-space-192) + var(--sv-space-128));',
    'max-width:calc(var(--sv-space-192) + var(--sv-space-128) + var(--sv-space-96) + var(--sv-space-64));',
    'padding:var(--sv-space-16);background:var(--sv-surface-raised);',
    'border:var(--sv-border-width-thin) solid var(--sv-border-strong);border-radius:var(--sv-radius-sm);}',
    '.mark{width:var(--sv-space-8);height:var(--sv-space-8);flex:none;margin-top:calc(var(--sv-space-8) + (var(--sv-space-4) / 2));',
    'border-radius:var(--sv-radius-full);background:var(--sv-status-done);}',
    ':host([status="error"]) .mark{background:var(--sv-status-failed);border-radius:0;}',
    ':host([status="info"]) .mark{background:transparent;border-radius:var(--sv-radius-full);',
    'border:var(--sv-border-width-mid) solid var(--sv-status-idle);}',
    '.body{flex:1;min-width:0;}',
    '.msg{margin:0;font-family:var(--sv-font-zh);font-size:var(--sv-text-body-m);',
    'line-height:var(--sv-leading-body);color:var(--sv-text-primary);}',
    '.detail{margin:calc(var(--sv-space-4) / 2) 0 0;font-size:var(--sv-text-caption);',
    'line-height:var(--sv-leading-caption);color:var(--sv-text-secondary);}',
    '.detail[hidden]{display:none;}',
    '.actions{display:flex;align-items:center;gap:var(--sv-space-16);margin-top:var(--sv-space-8);}',
    '.actions[hidden]{display:none;}',
    '.act{display:inline-flex;align-items:center;min-height:var(--sv-space-32);',
    'padding:0 var(--sv-space-8);border:0;background:none;color:var(--sv-text-primary);',
    'font-family:var(--sv-font-zh);font-size:var(--sv-text-caption);',
    'text-decoration:underline;text-underline-offset:calc(var(--sv-space-4) / 2);cursor:pointer;}',
    '.act:hover{background:var(--sv-surface-sunken);}',
    '.act:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    '.close{display:inline-flex;align-items:center;justify-content:center;',
    'width:var(--sv-space-32);height:var(--sv-space-32);flex:none;',
    'margin:calc(-1 * var(--sv-space-8)) calc(-1 * var(--sv-space-8)) 0 0;',
    'padding:0;border:var(--sv-border-width-thin) solid transparent;border-radius:var(--sv-radius-sm);',
    'background:transparent;color:var(--sv-text-secondary);cursor:pointer;}',
    '.close:hover{background:var(--sv-surface-sunken);color:var(--sv-text-primary);}',
    '.close:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    '.close[hidden]{display:none;}',
    '.close svg{width:calc(var(--sv-space-8) + var(--sv-space-4));height:auto;display:block;}',
    ':host([dismissible="false"]) .close{display:none;}',
  ].join('');

  var TOAST_TEMPLATE =
    '<style>' + COMMON + TOAST_CSS + REDUCED + '</style>' +
    '<div class="card" part="card">' +
    '<span class="mark" part="mark" aria-hidden="true"></span>' +
    '<div class="body" part="body">' +
    '<p class="msg" part="message"><slot name="message"><span class="msg-fallback"></span></slot></p>' +
    '<p class="detail" part="detail"><slot name="detail"><span class="detail-fallback"></span></slot></p>' +
    '<div class="actions" part="actions">' +
    '<button class="act" part="action-button" type="button" hidden></button>' +
    '</div>' +
    '</div>' +
    '<button class="close" part="dismiss" type="button">' + CLOSE_ICON_SVG + '</button>' +
    '</div>';

  var TOAST_DEFAULT_DURATION = 6000;

  class SvToast extends HTMLElement {
    static get observedAttributes() {
      return ['status', 'message', 'detail', 'action-label', 'dismiss-label',
        'dismissible', 'persistent', 'duration'];
    }

    constructor() {
      super();
      this._timer = null;
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = TOAST_TEMPLATE;
      this._msgFallback = root.querySelector('.msg-fallback');
      this._detailFallback = root.querySelector('.detail-fallback');
      this._detail = root.querySelector('.detail');
      this._actions = root.querySelector('.actions');
      this._actionButton = root.querySelector('.act');
      this._close = root.querySelector('.close');
      this._actionButton.addEventListener('click', this._onAction.bind(this));
      this._close.addEventListener('click', this.dismiss.bind(this, 'close'));
      this.addEventListener('pointerenter', this._pause.bind(this));
      this.addEventListener('pointerleave', this._resume.bind(this));
      this.addEventListener('focusin', this._pause.bind(this));
      this.addEventListener('focusout', this._resume.bind(this));
    }

    connectedCallback() {
      this._update();
      this._resume();
    }

    disconnectedCallback() { this._clearTimer(); }

    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._msgFallback) return;
      var status = this.getAttribute('status') || 'success';
      if (['success', 'error', 'info'].indexOf(status) < 0) status = 'success';
      this._msgFallback.textContent = this.getAttribute('message') || '';
      var detailText = this.getAttribute('detail') || '';
      this._detailFallback.textContent = detailText;
      this._detail.hidden = !detailText;
      var actionLabel = this.getAttribute('action-label') || '';
      this._actionButton.textContent = actionLabel;
      this._actionButton.hidden = !actionLabel;
      this._actions.hidden = !actionLabel;
      this._close.setAttribute('aria-label', this.getAttribute('dismiss-label') || DEFAULT_LABELS.dismiss);
      this.setAttribute('role', status === 'error' ? 'alert' : 'status');
      this.setAttribute('aria-live', status === 'error' ? 'assertive' : 'polite');
      this.setAttribute('aria-atomic', 'true');
      this._resume();
    }

    _clearTimer() {
      if (this._timer) {
        clearTimeout(this._timer);
        this._timer = null;
      }
    }

    _pause() { this._clearTimer(); }

    _resume() {
      this._clearTimer();
      if (this.hasAttribute('persistent')) return;
      var duration = parseInt(this.getAttribute('duration'), 10);
      if (!isFinite(duration) || duration <= 0) duration = TOAST_DEFAULT_DURATION;
      var host = this;
      this._timer = setTimeout(function () { host.dismiss('timeout'); }, duration);
    }

    _onAction() {
      emit(this, 'sv-action', { status: this.getAttribute('status') || 'success' });
    }

    dismiss(reason) {
      this._clearTimer();
      emit(this, 'sv-dismiss', { reason: reason || 'close' });
      if (this.parentNode) this.parentNode.removeChild(this);
    }

    get persistent() { return this.hasAttribute('persistent'); }
    set persistent(value) { if (value) this.setAttribute('persistent', ''); else this.removeAttribute('persistent'); }
  }

  /* ---------------------------------------------------------------
   * sv-table
   * 原生 <table> 语义；列 / 行用 JSON 属性或 JS 属性传入。
   * 属性：caption columns rows sort-key sort-dir selected-index
   *      selectable loading error retry-label
   *      empty-title empty-hint empty-action
   * 事件：sv-sort {key,dir} / sv-select {index,row,key} / sv-retry / sv-action
   * ------------------------------------------------------------- */
  var TABLE_CSS = [
    ':host{display:block;--_row-h:calc(var(--sv-space-32) + var(--sv-space-16));}',
    '.wrap{display:flex;flex-direction:column;gap:var(--sv-space-16);min-width:0;}',
    '.banner{display:flex;align-items:center;gap:var(--sv-space-12);padding:var(--sv-space-12) var(--sv-space-16);',
    'border:var(--sv-border-width-thin) solid var(--sv-border-strong);',
    'border-left-width:var(--sv-border-width-strong);background:var(--sv-surface-canvas);',
    'font-size:var(--sv-text-caption);line-height:var(--sv-leading-caption);color:var(--sv-text-primary);}',
    '.banner[hidden]{display:none;}',
    '.err-mark{width:var(--sv-space-8);height:var(--sv-space-8);flex:none;background:var(--sv-status-failed);}',
    '.err-text{flex:1;min-width:0;}',
    '.retry{display:inline-flex;align-items:center;min-height:var(--sv-space-32);padding:0 var(--sv-space-8);',
    'border:0;background:none;color:var(--sv-text-primary);font-family:var(--sv-font-zh);',
    'font-size:var(--sv-text-caption);text-decoration:underline;',
    'text-underline-offset:calc(var(--sv-space-4) / 2);cursor:pointer;flex:none;}',
    '.retry:hover{background:var(--sv-surface-sunken);}',
    '.retry:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    '.scroll{overflow-x:auto;}',
    'table{width:100%;border-collapse:collapse;table-layout:fixed;font-family:var(--sv-font-zh);',
    'font-size:var(--sv-text-caption);line-height:var(--sv-leading-caption);}',
    'th{text-align:left;font-family:var(--sv-font-mono);font-size:var(--sv-text-micro);',
    'font-weight:var(--sv-weight-medium);letter-spacing:var(--sv-tracking-codename);',
    'text-transform:uppercase;color:var(--sv-text-secondary);background:var(--sv-surface-sunken);',
    'border-bottom:var(--sv-border-width-thin) solid var(--sv-border-hairline);',
    'padding:var(--sv-space-12) var(--sv-space-16);white-space:nowrap;}',
    'th[data-align="right"],td[data-align="right"]{text-align:right;}',
    'th:first-child,td:first-child{border-left:var(--sv-border-width-strong) solid transparent;',
    'padding-left:calc(var(--sv-space-16) - var(--sv-border-width-strong));}',
    'td{height:var(--_row-h);padding:0 var(--sv-space-16);',
    'border-bottom:var(--sv-border-width-thin) solid var(--sv-border-hairline);',
    'color:var(--sv-text-primary);vertical-align:middle;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
    'td.placeholder{color:var(--sv-text-secondary);}',
    'tbody tr[data-row-index]:hover td{background:var(--sv-surface-sunken);}',
    'tbody tr[aria-selected="true"] td{background:var(--sv-surface-sunken);}',
    'tbody tr[aria-selected="true"] td:first-child{border-left-color:var(--sv-border-strong);}',
    ':host([selectable]) tbody tr[data-row-index]{cursor:pointer;}',
    'tbody tr[data-row-index]:focus-visible{outline:none;}',
    'tbody tr[data-row-index]:focus-visible td:first-child{outline:var(--_ring);',
    'outline-offset:calc(-1 * var(--sv-border-width-strong));}',
    '.sort{display:inline-flex;align-items:center;gap:var(--sv-space-4);min-height:var(--sv-space-24);',
    'padding:0;border:0;background:none;color:inherit;font:inherit;letter-spacing:inherit;',
    'text-transform:inherit;cursor:pointer;}',
    '.sort:hover{color:var(--sv-text-primary);}',
    '.sort:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    '.sort .ind{font-size:var(--sv-text-micro);color:var(--sv-text-primary);}',
    '.empty-row td{padding:0;height:auto;}',
    'sv-empty::part(empty){padding:var(--sv-space-32) var(--sv-space-16);gap:var(--sv-space-8);}',
    '.loading-row td{height:auto;}',
    '.loading-line{display:inline-flex;align-items:center;gap:var(--sv-space-12);',
    'padding:var(--sv-space-12) 0;color:var(--sv-text-secondary);}',
    '.skel{display:inline-block;height:calc(var(--sv-space-4) + var(--sv-space-4) / 2);',
    'background:var(--sv-border-hairline);}',
    '.skel-1{width:calc(var(--sv-space-64) + var(--sv-space-16));}',
    '.skel-2{width:calc(var(--sv-space-64) + var(--sv-space-32) + var(--sv-space-16));}',
    '.skel-3{width:var(--sv-space-64);}',
    '.loading-text{font-size:var(--sv-text-caption);}',
    '.mono{font-family:var(--sv-font-mono);font-size:var(--sv-text-micro);',
    'letter-spacing:var(--sv-tracking-codename);}',
  ].join('');

  var TABLE_TEMPLATE =
    '<style>' + COMMON + TABLE_CSS + REDUCED + '</style>' +
    '<div class="wrap">' +
    '<div class="banner" part="error" role="alert" hidden>' +
    '<span class="err-mark" part="error-mark" aria-hidden="true"></span>' +
    '<span class="err-text" part="error-text"></span>' +
    '<button class="retry" part="retry" type="button"></button>' +
    '</div>' +
    '<div class="scroll">' +
    '<table part="table">' +
    '<caption class="sr-only" part="caption"></caption>' +
    '<colgroup part="colgroup"></colgroup>' +
    '<thead part="head"><tr part="header-row"></tr></thead>' +
    '<tbody part="body"></tbody>' +
    '</table>' +
    '</div></div>';

  var TABLE_STATUS_KEYS = ['running', 'done', 'failed', 'paused', 'idle', 'attention'];

  class SvTable extends HTMLElement {
    static get observedAttributes() {
      return ['caption', 'columns', 'rows', 'sort-key', 'sort-dir', 'selected-index', 'selectable',
        'loading', 'error', 'retry-label', 'empty-title', 'empty-hint', 'empty-action', 'aria-label'];
    }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = TABLE_TEMPLATE;
      this._table = root.querySelector('table');
      this._caption = root.querySelector('caption');
      this._colgroup = root.querySelector('colgroup');
      this._headerRow = root.querySelector('thead tr');
      this._tbody = root.querySelector('tbody');
      this._banner = root.querySelector('.banner');
      this._errText = root.querySelector('.err-text');
      this._retry = root.querySelector('.retry');
      this._columns = [];
      this._rows = [];
      this._viewRows = [];
      this._selected = -1;
      this._selecting = false;
      this._restoreSortKey = null;
      this._retry.addEventListener('click', this._onRetry.bind(this));
      this._headerRow.addEventListener('click', this._onHeaderClick.bind(this));
      this._tbody.addEventListener('click', this._onBodyClick.bind(this));
      this._tbody.addEventListener('keydown', this._onBodyKeydown.bind(this));
    }

    connectedCallback() { this._sync(); }

    attributeChangedCallback(name) {
      if (name === 'selected-index' && this._selecting) return;
      this._sync();
    }

    _readColumns() {
      var raw;
      try { raw = JSON.parse(this.getAttribute('columns') || '[]'); } catch (err) { raw = []; }
      if (!Array.isArray(raw)) raw = [];
      return raw.filter(function (col) {
        return col && typeof col === 'object' && typeof col.key === 'string' && col.key;
      }).map(function (col) {
        var width = typeof col.width === 'string' && /^\d+(?:\.\d+)?%$/.test(col.width) ? col.width : null;
        return {
          key: col.key,
          label: typeof col.label === 'string' ? col.label : col.key,
          align: col.align === 'right' ? 'right' : 'left',
          sortable: !!col.sortable,
          type: col.type === 'status' ? 'status' : (col.type === 'mono' ? 'mono' : 'text'),
          width: width,
        };
      });
    }

    _readRows() {
      var raw;
      try { raw = JSON.parse(this.getAttribute('rows') || '[]'); } catch (err) { raw = []; }
      if (!Array.isArray(raw)) raw = [];
      return raw.filter(function (row) { return row && typeof row === 'object' && !Array.isArray(row); });
    }

    _sortedRows() {
      var rows = this._rows.slice();
      var key = this.getAttribute('sort-key');
      var dir = this.getAttribute('sort-dir');
      if (!key || (dir !== 'asc' && dir !== 'desc')) return rows;
      var sign = dir === 'desc' ? -1 : 1;
      var host = this;
      rows.sort(function (a, b) {
        var av = host._cellValue(a, key);
        var bv = host._cellValue(b, key);
        if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sign;
        return String(av).localeCompare(String(bv), 'zh-CN') * sign;
      });
      return rows;
    }

    _cellValue(row, key) {
      var value = row[key];
      return value === undefined || value === null ? '' : value;
    }

    _sync() {
      if (!this._table) return;
      this._columns = this._readColumns();
      this._rows = this._readRows();
      var selected = parseInt(this.getAttribute('selected-index'), 10);
      var selectable = this.hasAttribute('selectable');
      this._selected = selectable && isFinite(selected) && selected >= 0 && selected < this._rows.length ? selected : -1;
      this._renderHeader();
      this._renderBody();
    }

    _renderHeader() {
      var row = this._headerRow;
      while (row.firstChild) row.removeChild(row.firstChild);
      while (this._colgroup.firstChild) this._colgroup.removeChild(this._colgroup.firstChild);
      this._columns.forEach(function (col) {
        if (!col.width) return;
        var colEl = document.createElement('col');
        colEl.style.width = col.width;
        this._colgroup.appendChild(colEl);
      }, this);

      var caption = (this.getAttribute('caption') || '').trim();
      this._caption.textContent = caption;
      var ariaLabel = this.getAttribute('aria-label');
      if (caption) this._table.removeAttribute('aria-label');
      else if (ariaLabel) this._table.setAttribute('aria-label', ariaLabel);
      else this._table.removeAttribute('aria-label');
      if (this.hasAttribute('loading')) this._table.setAttribute('aria-busy', 'true');
      else this._table.removeAttribute('aria-busy');

      var sortKey = this.getAttribute('sort-key');
      var sortDir = this.getAttribute('sort-dir');
      this._columns.forEach(function (col) {
        var th = document.createElement('th');
        th.setAttribute('scope', 'col');
        th.setAttribute('part', 'header-cell');
        if (col.align === 'right') th.setAttribute('data-align', 'right');
        if (col.sortable) {
          th.setAttribute('aria-sort', sortKey === col.key
            ? (sortDir === 'desc' ? 'descending' : (sortDir === 'asc' ? 'ascending' : 'none'))
            : 'none');
          var button = document.createElement('button');
          button.type = 'button';
          button.className = 'sort';
          button.setAttribute('part', 'sort-button');
          button.setAttribute('data-key', col.key);
          var label = document.createElement('span');
          label.textContent = col.label;
          var ind = document.createElement('span');
          ind.className = 'ind';
          ind.setAttribute('aria-hidden', 'true');
          ind.textContent = sortKey === col.key ? (sortDir === 'desc' ? '↓' : '↑') : '';
          button.appendChild(label);
          button.appendChild(ind);
          th.appendChild(button);
        } else {
          th.textContent = col.label;
        }
        row.appendChild(th);
      });

      if (this._restoreSortKey) {
        var target = this._headerRow.querySelector('.sort[data-key="' + this._restoreSortKey + '"]');
        this._restoreSortKey = null;
        if (target) target.focus();
      }
    }

    _renderBody() {
      var body = this._tbody;
      while (body.firstChild) body.removeChild(body.firstChild);
      var columns = this._columns.length || 1;

      var error = this.getAttribute('error');
      this._banner.hidden = !error;
      if (error) {
        this._errText.textContent = error;
        this._retry.textContent = this.getAttribute('retry-label') || '重试';
      }

      if (this.hasAttribute('loading')) {
        this._renderLoadingRow(columns);
        return;
      }

      var rows = this._sortedRows();
      this._viewRows = rows;
      if (!rows.length) {
        this._renderEmptyRow(columns);
        return;
      }

      var selectable = this.hasAttribute('selectable');
      rows.forEach(function (rowData, index) {
        var tr = document.createElement('tr');
        tr.setAttribute('part', 'row');
        tr.setAttribute('data-row-index', String(index));
        if (selectable) {
          tr.tabIndex = index === 0 ? 0 : -1;
          tr.setAttribute('aria-selected', index === this._selected ? 'true' : 'false');
        }
        this._columns.forEach(function (col) {
          var td = document.createElement('td');
          td.setAttribute('part', 'cell');
          if (col.align === 'right') td.setAttribute('data-align', 'right');
          this._renderCell(td, col, rowData[col.key]);
          tr.appendChild(td);
        }, this);
        body.appendChild(tr);
      }, this);
    }

    _renderCell(td, col, value) {
      var missing = value === undefined || value === null || value === '';
      if (missing) {
        td.textContent = '—';
        td.classList.add('placeholder');
        td.setAttribute('aria-label', '空值');
        return;
      }
      if (col.type === 'status') {
        var status = String(value);
        if (TABLE_STATUS_KEYS.indexOf(status) > -1) {
          var mark = document.createElement('sv-status');
          mark.setAttribute('status', status);
          td.appendChild(mark);
        } else {
          td.textContent = '—';
          td.classList.add('placeholder');
          td.setAttribute('aria-label', '未知状态');
        }
        return;
      }
      if (col.type === 'mono') {
        var mono = document.createElement('span');
        mono.className = 'mono';
        mono.textContent = String(value);
        td.appendChild(mono);
        return;
      }
      td.textContent = String(value);
    }

    _renderEmptyRow(columns) {
      var tr = document.createElement('tr');
      tr.className = 'empty-row';
      var td = document.createElement('td');
      td.colSpan = columns;
      td.setAttribute('part', 'empty-cell');
      var empty = document.createElement('sv-empty');
      empty.setAttribute('title', this.getAttribute('empty-title') || '还没有任务。');
      var hint = this.getAttribute('empty-hint');
      if (hint) empty.setAttribute('hint', hint);
      var action = this.getAttribute('empty-action');
      if (action) empty.setAttribute('action-label', action);
      var host = this;
      empty.addEventListener('sv-action', function () { emit(host, 'sv-action', null); });
      td.appendChild(empty);
      tr.appendChild(td);
      this._tbody.appendChild(tr);
    }

    _renderLoadingRow(columns) {
      var tr = document.createElement('tr');
      tr.className = 'loading-row';
      var td = document.createElement('td');
      td.colSpan = columns;
      td.setAttribute('part', 'loading-cell');
      var line = document.createElement('span');
      line.className = 'loading-line';
      line.setAttribute('role', 'status');
      [1, 2, 3].forEach(function (n) {
        var bar = document.createElement('span');
        bar.className = 'skel skel-' + n;
        bar.setAttribute('aria-hidden', 'true');
        line.appendChild(bar);
      });
      var text = document.createElement('span');
      text.className = 'loading-text';
      text.textContent = '正在对齐。';
      line.appendChild(text);
      td.appendChild(line);
      tr.appendChild(td);
      this._tbody.appendChild(tr);
    }

    _onRetry() { emit(this, 'sv-retry', null); }

    _onHeaderClick(event) {
      var button = event.target && event.target.closest ? event.target.closest('.sort') : null;
      if (!button) return;
      var key = button.getAttribute('data-key');
      var currentKey = this.getAttribute('sort-key');
      var currentDir = this.getAttribute('sort-dir');
      var dir = currentKey === key && currentDir === 'asc' ? 'desc' : 'asc';
      this._restoreSortKey = key;
      this.setAttribute('sort-key', key);
      this.setAttribute('sort-dir', dir);
      emit(this, 'sv-sort', { key: key, dir: dir });
    }

    _onBodyClick(event) {
      if (!this.hasAttribute('selectable')) return;
      var tr = event.target && event.target.closest ? event.target.closest('tr[data-row-index]') : null;
      if (!tr || !this._tbody.contains(tr)) return;
      this._selectRow(Number(tr.getAttribute('data-row-index')));
    }

    _onBodyKeydown(event) {
      if (!this.hasAttribute('selectable')) return;
      var tr = event.target && event.target.closest ? event.target.closest('tr[data-row-index]') : null;
      if (!tr) return;
      var index = Number(tr.getAttribute('data-row-index'));
      var key = event.key;
      if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Home' || key === 'End') {
        event.preventDefault();
        var next = key === 'ArrowDown' ? index + 1
          : key === 'ArrowUp' ? index - 1
            : key === 'Home' ? 0 : this._viewRows.length - 1;
        this._focusRowAt(next);
      } else if (key === 'Enter' || key === ' ') {
        event.preventDefault();
        this._selectRow(index);
      }
    }

    _focusRowAt(index) {
      var rows = this._tbody.querySelectorAll('tr[data-row-index]');
      if (!rows.length) return;
      var target = Math.max(0, Math.min(rows.length - 1, index));
      Array.prototype.forEach.call(rows, function (row, i) { row.tabIndex = i === target ? 0 : -1; });
      rows[target].focus();
    }

    _selectRow(index) {
      if (index < 0 || index >= this._viewRows.length) return;
      this._selected = index;
      this._selecting = true;
      this.setAttribute('selected-index', String(index));
      this._selecting = false;
      this._applySelection();
      var rowData = this._viewRows[index];
      var key = rowData && rowData.id !== undefined && rowData.id !== null ? rowData.id : index;
      emit(this, 'sv-select', { index: index, row: rowData, key: key });
    }

    _applySelection() {
      var selected = this._selected;
      var selectable = this.hasAttribute('selectable');
      Array.prototype.forEach.call(this._tbody.querySelectorAll('tr[data-row-index]'), function (tr) {
        if (!selectable) { tr.removeAttribute('aria-selected'); return; }
        tr.setAttribute('aria-selected', Number(tr.getAttribute('data-row-index')) === selected ? 'true' : 'false');
      });
    }

    get columns() { return this._columns.slice(); }
    set columns(value) { this.setAttribute('columns', JSON.stringify(value || [])); }
    get rows() { return this._rows.slice(); }
    set rows(value) { this.setAttribute('rows', JSON.stringify(value || [])); }
    get selectedIndex() { return this._selected; }
    set selectedIndex(value) { this.setAttribute('selected-index', String(value)); }
  }

  /* ---------------------------------------------------------------
   * sv-tabs / sv-tab / sv-panel
   * 作者写 <sv-tab label> 元数据 + <sv-panel> 面板内容；
   * tablist 与 tabpanel 都在同一 Shadow DOM 内，ARIA id 引用可解析。
   * 属性：label selected-index
   * 子元素：sv-tab(label selected disabled preview-state) / sv-panel
   * 事件：sv-change {index,id,label}
   * ------------------------------------------------------------- */
  var TABS_CSS = [
    ':host{display:block;}',
    '.tablist{display:flex;align-items:stretch;gap:var(--sv-space-32);',
    'border-bottom:var(--sv-border-width-thin) solid var(--sv-border-hairline);}',
    '.tab{position:relative;display:inline-flex;align-items:center;min-height:calc(var(--sv-space-32) + var(--sv-space-8));',
    'margin:0 0 calc(-1 * var(--sv-border-width-thin));padding:var(--sv-space-8) 0;',
    'border:0;border-bottom:var(--sv-border-width-strong) solid transparent;border-radius:0;background:none;',
    'color:var(--sv-text-secondary);font-family:var(--sv-font-zh);font-size:var(--sv-text-body-m);',
    'line-height:var(--sv-leading-heading);white-space:nowrap;cursor:pointer;',
    '-webkit-appearance:none;appearance:none;}',
    '.tab:hover,.tab.is-preview-hover{color:var(--sv-text-primary);}',
    '.tab[aria-selected="true"]{color:var(--sv-text-primary);font-weight:var(--sv-weight-medium);',
    'border-bottom-color:var(--sv-border-strong);}',
    '.tab:focus-visible,.tab.is-preview-focus{outline:var(--_ring);',
    'outline-offset:calc(-1 * var(--sv-space-4) / 2);}',
    '.tab[aria-disabled="true"]{color:var(--sv-text-muted);cursor:not-allowed;}',
    '.tab[aria-disabled="true"]:hover{color:var(--sv-text-muted);}',
    '.panels{padding-top:var(--sv-space-24);}',
    '.panel{outline:none;}',
    '.panel[hidden]{display:none;}',
    '.panel:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    '.meta-slot{display:none;}',
  ].join('');

  var TABS_TEMPLATE =
    '<style>' + COMMON + TABS_CSS + REDUCED + '</style>' +
    '<div class="wrap">' +
    '<div class="tablist" part="tablist" role="tablist"></div>' +
    '<div class="panels" part="panels"></div>' +
    '<slot class="meta-slot"></slot>' +
    '</div>';

  var SV_PANEL_TEMPLATE = '<style>:host{display:block;}</style><slot></slot>';

  class SvTab extends HTMLElement {}

  class SvPanel extends HTMLElement {
    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = SV_PANEL_TEMPLATE;
    }
  }

  class SvTabs extends HTMLElement {
    static get observedAttributes() { return ['label', 'selected-index']; }

    constructor() {
      super();
      this._uid = nextId('sv-tabs');
      this._tabs = [];
      this._panelEls = [];
      this._buttons = [];
      this._selected = 0;
      this._suppress = false;
      this._rebuildQueued = false;
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = TABS_TEMPLATE;
      this._tablist = root.querySelector('.tablist');
      this._panels = root.querySelector('.panels');
      this._metaSlot = root.querySelector('.meta-slot');
      this._tablist.addEventListener('click', this._onClick.bind(this));
      this._tablist.addEventListener('keydown', this._onKeydown.bind(this));
      this._metaSlot.addEventListener('slotchange', this._queueRebuild.bind(this));
      this._observer = null;
      if (typeof MutationObserver === 'function') {
        var host = this;
        this._observer = new MutationObserver(function (records) {
          for (var i = 0; i < records.length; i += 1) {
            if (records[i].target !== host) { host._queueRebuild(); return; }
          }
        });
      }
    }

    connectedCallback() {
      if (this._observer) {
        this._observer.observe(this, {
          childList: true,
          attributes: true,
          attributeFilter: ['label', 'selected', 'disabled', 'preview-state'],
        });
      }
      this._build();
    }

    disconnectedCallback() {
      if (this._observer) this._observer.disconnect();
    }

    attributeChangedCallback(name) {
      if (name === 'selected-index' && this._suppress) return;
      if (!this.isConnected) return;
      this._queueRebuild();
    }

    _queueRebuild() {
      if (this._rebuildQueued) return;
      this._rebuildQueued = true;
      var host = this;
      var run = function () { host._rebuildQueued = false; if (host.isConnected) host._build(); };
      if (typeof queueMicrotask === 'function') queueMicrotask(run);
      else setTimeout(run, 0);
    }

    _build() {
      if (!this._tablist) return;
      this._tabs = Array.prototype.slice.call(this.querySelectorAll(':scope > sv-tab'));
      this._panelEls = Array.prototype.slice.call(this.querySelectorAll(':scope > sv-panel'));
      while (this._tablist.firstChild) this._tablist.removeChild(this._tablist.firstChild);
      while (this._panels.firstChild) this._panels.removeChild(this._panels.firstChild);
      this._buttons = [];

      var label = this.getAttribute('label');
      if (label) this._tablist.setAttribute('aria-label', label);
      else this._tablist.removeAttribute('aria-label');

      var host = this;
      this._tabs.forEach(function (tab, index) {
        var tabId = host._uid + '-tab-' + index;
        var panelId = host._uid + '-panel-' + index;
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'tab';
        button.id = tabId;
        button.setAttribute('part', 'tab');
        button.setAttribute('role', 'tab');
        button.setAttribute('aria-controls', panelId);
        button.setAttribute('aria-selected', 'false');
        button.tabIndex = -1;
        var buttonLabel = document.createElement('span');
        buttonLabel.textContent = tab.getAttribute('label') || (tab.textContent || '').trim() || 'Tab';
        button.appendChild(buttonLabel);
        if (tab.hasAttribute('disabled')) {
          button.setAttribute('aria-disabled', 'true');
          button.classList.add('is-disabled');
        }
        var preview = tab.getAttribute('preview-state');
        if (preview === 'hover') button.classList.add('is-preview-hover');
        if (preview === 'focus') button.classList.add('is-preview-focus');
        button.addEventListener('click', function () {
          if (host._isDisabled(index)) return;
          host._select(index, true);
        });
        host._tablist.appendChild(button);
        host._buttons.push(button);

        var panel = document.createElement('div');
        panel.className = 'panel';
        panel.setAttribute('part', 'panel');
        panel.setAttribute('role', 'tabpanel');
        panel.id = panelId;
        panel.setAttribute('aria-labelledby', tabId);
        panel.tabIndex = 0;
        panel.hidden = true;
        var slot = document.createElement('slot');
        slot.name = 'sv-panel-' + index;
        panel.appendChild(slot);
        host._panels.appendChild(panel);

        if (host._panelEls[index]) host._panelEls[index].setAttribute('slot', 'sv-panel-' + index);
      });

      this._panelEls.slice(this._tabs.length).forEach(function (panel) {
        panel.removeAttribute('slot');
      });

      var initial = parseInt(this.getAttribute('selected-index'), 10);
      if (!isFinite(initial)) {
        initial = 0;
        for (var i = 0; i < this._tabs.length; i += 1) {
          if (this._tabs[i].hasAttribute('selected')) { initial = i; break; }
        }
      }
      this._select(this._normalize(initial), false, true);
    }

    _isDisabled(index) {
      var tab = this._tabs[index];
      return !!(tab && tab.hasAttribute('disabled'));
    }

    _normalize(index) {
      var count = this._buttons.length;
      if (!count) return 0;
      var target = Math.max(0, Math.min(count - 1, index));
      if (!this._isDisabled(target)) return target;
      for (var offset = 1; offset <= count; offset += 1) {
        var next = (target + offset) % count;
        if (!this._isDisabled(next)) return next;
      }
      return target;
    }

    _select(index, emitEvent, force) {
      var count = this._buttons.length;
      if (!count) return;
      var target = this._normalize(index);
      if (target === this._selected && !force) {
        this._applyPanelState();
        return;
      }
      this._selected = target;
      this._applyPanelState();
      this._suppress = true;
      this.setAttribute('selected-index', String(target));
      this._suppress = false;
      if (emitEvent) {
        var tab = this._tabs[target];
        emit(this, 'sv-change', {
          index: target,
          id: this._buttons[target] ? this._buttons[target].id : null,
          label: tab ? (tab.getAttribute('label') || '') : '',
        });
      }
    }

    _applyPanelState() {
      var selected = this._selected;
      this._buttons.forEach(function (button, index) {
        var isSelected = index === selected;
        button.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        button.tabIndex = isSelected ? 0 : -1;
      });
      Array.prototype.forEach.call(this._panels.children, function (panel, index) {
        panel.hidden = index !== selected;
      });
    }

    _onClick(event) {
      var button = event.target && event.target.closest ? event.target.closest('.tab') : null;
      if (!button) return;
      var index = this._buttons.indexOf(button);
      if (index < 0 || this._isDisabled(index)) return;
      this._select(index, true);
    }

    _onKeydown(event) {
      var key = event.key;
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(key) < 0) return;
      var count = this._buttons.length;
      if (!count) return;
      event.preventDefault();
      var next = this._selected;
      if (key === 'Home') {
        next = 0;
        while (next < count && this._isDisabled(next)) next += 1;
      } else if (key === 'End') {
        next = count - 1;
        while (next >= 0 && this._isDisabled(next)) next -= 1;
      } else {
        var dir = key === 'ArrowRight' ? 1 : -1;
        for (var i = 1; i <= count; i += 1) {
          var candidate = ((this._selected + dir * i) % count + count) % count;
          if (!this._isDisabled(candidate)) { next = candidate; break; }
        }
      }
      if (next < 0 || next >= count) return;
      this._select(next, true);
      if (this._buttons[next]) this._buttons[next].focus();
    }

    get activeIndex() { return this._selected; }
    set activeIndex(value) { this.setAttribute('selected-index', String(value)); }
  }

  /* ---------------------------------------------------------------
   * sv-dialog
   * 原生 <dialog>：show() 走 showModal（焦点陷阱 + Esc 由浏览器提供），
   * 声明式 open 属性 = 非模态内联预览（供文档与截图）。
   * 属性：open title close-label dismissible aria-label
   * 插槽：title / 默认（正文）/ footer（操作）
   * 事件：sv-open {modal} / sv-close {reason}
   * ------------------------------------------------------------- */
  var DIALOG_CSS = [
    ':host{display:block;}',
    'dialog{position:relative;inset:auto;display:none;margin:0 auto;padding:0;width:100%;',
    'max-width:calc(var(--sv-space-192) + var(--sv-space-128) + var(--sv-space-96) + var(--sv-space-64));',
    'border:var(--sv-border-width-thin) solid var(--sv-border-strong);border-radius:var(--sv-radius-sm);',
    'background:var(--sv-surface-raised);color:var(--sv-text-primary);font-family:var(--sv-font-zh);',
    'font-size:var(--sv-text-body-m);line-height:var(--sv-leading-body);}',
    'dialog[open]{display:block;}',
    'dialog.modal{position:fixed;inset:0;margin:auto;}',
    'dialog::backdrop{background:var(--sv-surface-scrim);}',
    '.panel{display:flex;flex-direction:column;min-width:0;}',
    '.head{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--sv-space-16);',
    'padding:var(--sv-space-24) var(--sv-space-24) 0;}',
    '.title{margin:0;font-size:var(--sv-text-heading-m);font-weight:var(--sv-weight-medium);',
    'line-height:var(--sv-leading-heading);color:var(--sv-text-primary);}',
    '.title[hidden]{display:none;}',
    '.close{display:inline-flex;align-items:center;justify-content:center;flex:none;',
    'width:calc(var(--sv-space-32) + var(--sv-space-12));height:calc(var(--sv-space-32) + var(--sv-space-12));',
    'padding:0;border:var(--sv-border-width-thin) solid transparent;border-radius:var(--sv-radius-sm);',
    'background:transparent;color:var(--sv-text-secondary);cursor:pointer;',
    'margin:calc(-1 * var(--sv-space-8)) calc(-1 * var(--sv-space-8)) 0 0;}',
    '.close:hover{background:var(--sv-surface-sunken);color:var(--sv-text-primary);}',
    '.close:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    '.close svg{width:calc(var(--sv-space-8) + var(--sv-space-4));height:auto;display:block;}',
    '.body{padding:var(--sv-space-16) var(--sv-space-24) var(--sv-space-24);',
    'color:var(--sv-text-secondary);font-size:var(--sv-text-body-m);line-height:var(--sv-leading-body);}',
    '.foot{display:flex;align-items:center;justify-content:flex-end;gap:var(--sv-space-12);',
    'padding:var(--sv-space-16) var(--sv-space-24);',
    'border-top:var(--sv-border-width-thin) solid var(--sv-border-hairline);}',
    '.foot[hidden]{display:none;}',
  ].join('');

  var DIALOG_TEMPLATE =
    '<style>' + COMMON + DIALOG_CSS + REDUCED + '</style>' +
    '<dialog class="dlg" part="dialog" role="dialog" aria-modal="false">' +
    '<div class="panel" part="panel">' +
    '<header class="head" part="header">' +
    '<h2 class="title" part="title"><slot name="title"><span class="title-fallback"></span></slot></h2>' +
    '<button class="close" part="close" type="button">' + CLOSE_ICON_SVG + '</button>' +
    '</header>' +
    '<div class="body" part="body"><slot></slot></div>' +
    '<div class="foot" part="footer"><slot name="footer"></slot></div>' +
    '</div></dialog>';

  class SvDialog extends HTMLElement {
    static get observedAttributes() { return ['open', 'title', 'close-label', 'dismissible', 'aria-label']; }

    constructor() {
      super();
      this._uid = nextId('sv-dialog');
      this._booted = false;
      this._suppress = false;
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = DIALOG_TEMPLATE;
      this._dlg = root.querySelector('dialog');
      this._title = root.querySelector('.title');
      this._titleFallback = root.querySelector('.title-fallback');
      this._close = root.querySelector('.close');
      this._body = root.querySelector('.body');
      this._foot = root.querySelector('.foot');
      this._footerSlot = root.querySelector('slot[name="footer"]');
      this._titleId = this._uid + '-title';
      this._bodyId = this._uid + '-body';
      this._title.id = this._titleId;
      this._body.id = this._bodyId;
      this._dlg.setAttribute('aria-labelledby', this._titleId);
      this._dlg.setAttribute('aria-describedby', this._bodyId);
      this._close.addEventListener('click', this._onCloseClick.bind(this));
      this._dlg.addEventListener('cancel', this._onCancel.bind(this));
      this._dlg.addEventListener('click', this._onBackdropClick.bind(this));
      this._footerSlot.addEventListener('slotchange', this._updateFooter.bind(this));
    }

    connectedCallback() {
      this._booted = true;
      this._sync();
      if (this.hasAttribute('open')) this._open(false);
    }

    attributeChangedCallback(name) {
      if (!this._dlg) return;
      this._sync();
      if (name !== 'open' || this._suppress) return;
      if (this.hasAttribute('open')) this._open(false);
      else this._shut('attribute', true);
    }

    _sync() {
      var title = this.getAttribute('title') || '';
      this._titleFallback.textContent = title;
      this._title.hidden = !title;
      var ariaLabel = this.getAttribute('aria-label');
      if (ariaLabel) this._dlg.setAttribute('aria-label', ariaLabel);
      else this._dlg.removeAttribute('aria-label');
      this._close.setAttribute('aria-label', this.getAttribute('close-label') || DEFAULT_LABELS.dismiss);
      this._updateFooter();
    }

    _updateFooter() {
      if (!this._foot || !this._footerSlot) return;
      var hasFooter = typeof this._footerSlot.assignedNodes === 'function'
        ? this._footerSlot.assignedNodes().length > 0
        : true;
      this._foot.hidden = !hasFooter;
    }

    _open(modal) {
      if (!this._dlg) return;
      var isModal = modal !== false;
      if (isModal) {
        var alreadyModal = this._dlg.open && this._dlg.classList.contains('modal');
        if (!alreadyModal) {
          /* 内联 open 状态下不能直接 showModal：先关闭再加模态 */
          if (this._dlg.open) {
            try { this._dlg.close(); } catch (err) { this._dlg.removeAttribute('open'); }
          }
          this._dlg.classList.add('modal');
          this._dlg.setAttribute('aria-modal', 'true');
          try {
            this._dlg.showModal();
          } catch (err) {
            this._dlg.classList.remove('modal');
            this._dlg.setAttribute('aria-modal', 'false');
            try { this._dlg.setAttribute('open', ''); } catch (err2) { /* noop */ }
          }
        }
      } else if (this._dlg.classList.contains('modal')) {
        /* 从模态降级为内联：先关闭再以 open 属性展示 */
        if (this._dlg.open) {
          try { this._dlg.close(); } catch (err) { this._dlg.removeAttribute('open'); }
        }
        this._dlg.classList.remove('modal');
        this._dlg.setAttribute('aria-modal', 'false');
        if (!this._dlg.open) this._dlg.setAttribute('open', '');
      } else {
        /* 内联预览：只设置 open 属性，不运行 dialog focusing steps（避免加载时抢焦点/滚动） */
        if (!this._dlg.open) this._dlg.setAttribute('open', '');
        this._dlg.setAttribute('aria-modal', 'false');
      }
      if (this._booted) emit(this, 'sv-open', { modal: isModal });
    }

    _shut(reason, emitEvent) {
      if (!this._dlg || !this._dlg.open) return;
      try {
        if (typeof this._dlg.close === 'function') this._dlg.close();
        else this._dlg.removeAttribute('open');
      } catch (err) {
        try { this._dlg.removeAttribute('open'); } catch (err2) { /* noop */ }
      }
      this._dlg.classList.remove('modal');
      this._dlg.setAttribute('aria-modal', 'false');
      if (emitEvent !== false && this._booted) emit(this, 'sv-close', { reason: reason || 'close' });
    }

    show() {
      if (this.hasAttribute('open')) { this._open(true); return; }
      this._suppress = true;
      this.setAttribute('open', '');
      this._suppress = false;
      this._open(true);
    }

    close(reason) {
      this._shut(reason || 'api', true);
      if (this.hasAttribute('open')) {
        this._suppress = true;
        this.removeAttribute('open');
        this._suppress = false;
      }
    }

    _onCloseClick() { this.close('close'); }

    _onCancel(event) {
      event.preventDefault();
      if (this.getAttribute('dismissible') === 'false') return;
      this.close('escape');
    }

    _onBackdropClick(event) {
      if (event.target !== this._dlg) return;
      if (!this._dlg.classList.contains('modal')) return;
      if (this.getAttribute('dismissible') === 'false') return;
      this.close('backdrop');
    }

    get open() { return !!(this._dlg && this._dlg.open); }
    set open(value) { if (value) this.show(); else this.close('api'); }
  }

  /* ---------------------------------------------------------------
   * sv-loading
   * 180° 弧 rotate：600ms 旋转 + 240ms 停顿（prefers-reduced-motion 降级为静态弧）。
   * 变体：indeterminate（弧 + 文案）/ determinate（1px 轨道 + 2px 进度 + 数值）/ skeleton（静态骨架条）。
   * 属性：label value cancelable cancel-label size
   * 事件：sv-cancel
   * ------------------------------------------------------------- */
  var LOADING_CSS = [
    ':host{display:block;}',
    '.root{display:inline-flex;align-items:center;gap:var(--sv-space-12);flex-wrap:wrap;',
    'font-family:var(--sv-font-zh);font-size:var(--sv-text-body-m);line-height:var(--sv-leading-body);',
    'color:var(--sv-text-secondary);}',
    '.arc{width:var(--sv-space-24);height:var(--sv-space-24);flex:none;box-sizing:border-box;',
    'border:var(--sv-border-width-mid) solid var(--sv-status-running);border-radius:var(--sv-radius-full);',
    'border-right-color:transparent;border-bottom-color:transparent;',
    'animation:sv-loading-turn calc(var(--sv-duration-transition) + var(--sv-duration-micro)) linear infinite;}',
    '@keyframes sv-loading-turn{0%{transform:rotate(0deg);}71.43%{transform:rotate(180deg);}100%{transform:rotate(180deg);}}',
    ':host([size="lg"]) .arc{width:var(--sv-space-32);height:var(--sv-space-32);',
    'border-width:var(--sv-border-width-strong);border-right-color:transparent;border-bottom-color:transparent;}',
    '.label{min-width:0;}',
    '.track{display:none;position:relative;width:calc(var(--sv-space-192) + var(--sv-space-32));',
    'height:var(--sv-border-width-thin);background:var(--sv-border-hairline);flex:none;}',
    '.bar{position:absolute;inset:calc(-1 * (var(--sv-border-width-strong) - var(--sv-border-width-thin)) / 2) auto auto 0;',
    'height:var(--sv-border-width-strong);background:var(--sv-border-strong);width:0;}',
    '.value{display:none;font-family:var(--sv-font-mono);font-size:var(--sv-text-micro);',
    'letter-spacing:var(--sv-tracking-codename);color:var(--sv-text-primary);}',
    ':host([value]) .arc{display:none;}',
    ':host([value]) .track{display:block;}',
    ':host([value]) .value{display:inline-block;}',
    '.skeleton{display:none;align-items:center;gap:var(--sv-space-12);}',
    '.skel{display:inline-block;height:calc(var(--sv-space-4) + var(--sv-space-4) / 2);',
    'background:var(--sv-border-hairline);}',
    '.skel-1{width:calc(var(--sv-space-64) + var(--sv-space-16));}',
    '.skel-2{width:calc(var(--sv-space-64) + var(--sv-space-32) + var(--sv-space-16));}',
    '.skel-3{width:var(--sv-space-64);}',
    ':host([skeleton]) .arc,:host([skeleton]) .track,:host([skeleton]) .value{display:none;}',
    ':host([skeleton]) .skeleton{display:inline-flex;}',
    '.cancel{display:none;align-items:center;min-height:var(--sv-space-32);padding:0 var(--sv-space-8);',
    'border:0;background:none;color:var(--sv-text-primary);font-family:var(--sv-font-zh);',
    'font-size:var(--sv-text-caption);text-decoration:underline;',
    'text-underline-offset:calc(var(--sv-space-4) / 2);cursor:pointer;}',
    '.cancel:hover{background:var(--sv-surface-sunken);}',
    '.cancel:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    ':host([cancelable]) .cancel{display:inline-flex;}',
  ].join('');

  var LOADING_TEMPLATE =
    '<style>' + COMMON + LOADING_CSS + REDUCED + '</style>' +
    '<span class="root" part="root">' +
    '<span class="arc" part="arc" aria-hidden="true"></span>' +
    '<span class="skeleton" part="skeleton" aria-hidden="true">' +
    '<span class="skel skel-1"></span><span class="skel skel-2"></span><span class="skel skel-3"></span>' +
    '</span>' +
    '<span class="label" part="label"><slot><span class="label-fallback"></span></slot></span>' +
    '<span class="track" part="track" aria-hidden="true"><span class="bar" part="bar"></span></span>' +
    '<span class="value" part="value"></span>' +
    '<button class="cancel" part="cancel" type="button"></button>' +
    '</span>';

  class SvLoading extends HTMLElement {
    static get observedAttributes() {
      return ['label', 'value', 'cancelable', 'cancel-label', 'size', 'skeleton', 'aria-label'];
    }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = LOADING_TEMPLATE;
      this._root = root.querySelector('.root');
      this._labelFallback = root.querySelector('.label-fallback');
      this._bar = root.querySelector('.bar');
      this._value = root.querySelector('.value');
      this._cancel = root.querySelector('.cancel');
      this._cancel.addEventListener('click', this._onCancel.bind(this));
    }

    connectedCallback() { this._update(); }

    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._bar) return;
      var label = this.getAttribute('label') || '正在对齐。';
      this._labelFallback.textContent = label;
      this._cancel.textContent = this.getAttribute('cancel-label') || '取消';
      /* role / aria 放在内部根节点：宿主属性不再被组件自身写入，避免 attributeChanged 递归 */
      this._root.setAttribute('role', 'progressbar');
      this._root.setAttribute('aria-busy', 'true');
      var ariaLabel = this.getAttribute('aria-label') || label;
      this._root.setAttribute('aria-label', ariaLabel);
      var valueText = this.getAttribute('value');
      var numeric = valueText === null ? NaN : Number(valueText);
      if (isFinite(numeric)) {
        var clamped = Math.max(0, Math.min(100, numeric));
        this._bar.style.width = clamped + '%';
        this._value.textContent = Math.round(clamped) + '%';
        this._root.setAttribute('aria-valuemin', '0');
        this._root.setAttribute('aria-valuemax', '100');
        this._root.setAttribute('aria-valuenow', String(Math.round(clamped)));
      } else {
        this._bar.style.width = '';
        this._value.textContent = '';
        this._root.removeAttribute('aria-valuemin');
        this._root.removeAttribute('aria-valuemax');
        this._root.removeAttribute('aria-valuenow');
      }
    }

    _onCancel() { emit(this, 'sv-cancel', null); }

    get value() { return this.getAttribute('value'); }
    set value(v) { if (v == null) this.removeAttribute('value'); else this.setAttribute('value', String(v)); }
  }

  /* ---------------------------------------------------------------
   * sv-error
   * 失败态 = 实心方块 + 文案 + 重试；不引入红色。
   * 属性：message detail action-label variant(banner|field) resolved
   * 事件：sv-retry
   * ------------------------------------------------------------- */
  var ERROR_CSS = [
    ':host{display:block;}',
    '.banner{display:flex;align-items:flex-start;gap:var(--sv-space-12);padding:var(--sv-space-16);',
    'border:var(--sv-border-width-thin) solid var(--sv-border-strong);',
    'border-left-width:var(--sv-border-width-strong);background:var(--sv-surface-canvas);}',
    '.mark{width:var(--sv-space-12);height:var(--sv-space-12);flex:none;background:var(--sv-status-failed);',
    'margin-top:calc(var(--sv-space-4) / 2);}',
    ':host([resolved]) .mark{border-radius:var(--sv-radius-full);background:var(--sv-status-done);}',
    '.body{flex:1;min-width:0;}',
    '.msg{margin:0;font-size:var(--sv-text-body-m);line-height:var(--sv-leading-body);color:var(--sv-text-primary);}',
    '.detail{margin:calc(var(--sv-space-4) / 2) 0 0;font-size:var(--sv-text-caption);',
    'line-height:var(--sv-leading-caption);color:var(--sv-text-secondary);}',
    '.detail[hidden]{display:none;}',
    '.retry{display:inline-flex;align-items:center;flex:none;min-height:var(--sv-space-32);',
    'padding:0 var(--sv-space-8);border:0;background:none;color:var(--sv-text-primary);',
    'font-family:var(--sv-font-zh);font-size:var(--sv-text-caption);text-decoration:underline;',
    'text-underline-offset:calc(var(--sv-space-4) / 2);cursor:pointer;}',
    '.retry:hover{background:var(--sv-surface-sunken);}',
    '.retry:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    ':host([variant="field"]) .banner{border:0;padding:0;background:none;align-items:center;}',
    ':host([variant="field"]) .mark{width:var(--sv-space-8);height:var(--sv-space-8);margin-top:0;}',
    ':host([variant="field"]) .msg{font-size:var(--sv-text-caption);line-height:var(--sv-leading-caption);}',
    ':host([variant="field"]) .detail,:host([variant="field"]) .retry{display:none;}',
  ].join('');

  var ERROR_TEMPLATE =
    '<style>' + COMMON + ERROR_CSS + REDUCED + '</style>' +
    '<div class="banner" part="banner">' +
    '<span class="mark" part="mark" aria-hidden="true"></span>' +
    '<div class="body" part="body">' +
    '<p class="msg" part="message"><slot name="message"><span class="msg-fallback"></span></slot></p>' +
    '<p class="detail" part="detail"><slot name="detail"><span class="detail-fallback"></span></slot></p>' +
    '</div>' +
    '<button class="retry" part="retry" type="button"></button>' +
    '</div>';

  class SvError extends HTMLElement {
    static get observedAttributes() {
      return ['message', 'detail', 'action-label', 'variant', 'resolved'];
    }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = ERROR_TEMPLATE;
      this._msg = root.querySelector('.msg-fallback');
      this._detail = root.querySelector('.detail-fallback');
      this._detailWrap = root.querySelector('.detail');
      this._retry = root.querySelector('.retry');
      this._detailWrap.id = nextId('sv-error-detail');
      this._retry.addEventListener('click', this._onRetry.bind(this));
    }

    connectedCallback() { this._update(); }
    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._msg) return;
      var resolved = this.hasAttribute('resolved');
      var message = this.getAttribute('message') || '';
      var detail = this.getAttribute('detail') || '';
      this._msg.textContent = message;
      this._detail.textContent = detail;
      this._detailWrap.hidden = !detail;
      var action = this.getAttribute('action-label') || (resolved ? '详情' : '重试');
      this._retry.textContent = action;
      this.setAttribute('role', resolved ? 'status' : 'alert');
      this.setAttribute('aria-live', resolved ? 'polite' : 'assertive');
      if (detail) this.setAttribute('aria-describedby', this._detailWrap.id);
      else this.removeAttribute('aria-describedby');
    }

    _onRetry() { emit(this, 'sv-retry', null); }
  }

  /* ---------------------------------------------------------------
   * sv-nav / sv-nav-item / sv-nav-section
   * 侧栏或顶部导航容器；item 优先用原生 <a>（有 href）或 <button>。
   * 属性：label variant(sidebar|topbar)
   * 子元素：sv-nav-section(label) / sv-nav-item(href current disabled)
   * slot：header / 默认（items）/ footer
   * ------------------------------------------------------------- */
  var NAV_CSS = [
    ':host{display:block;height:100%;}',
    '.nav{display:flex;box-sizing:border-box;height:100%;font-family:var(--sv-font-zh);}',
    ':host([variant="topbar"]) .nav{flex-direction:row;align-items:center;gap:var(--sv-space-24);',
    'height:calc(var(--sv-space-32) + var(--sv-space-24));padding:0 var(--sv-space-24);',
    'border-bottom:var(--sv-border-width-thin) solid var(--sv-border-hairline);}',
    '.nav{flex-direction:column;gap:var(--sv-space-4);width:calc(var(--sv-space-192) + var(--sv-space-48));',
    'padding:var(--sv-space-16) var(--sv-space-12);}',
    ':host([variant="topbar"]) .nav{width:auto;height:auto;padding:0 var(--sv-space-24);',
    'flex-direction:row;align-items:center;}',
    '.head,.foot{display:block;min-width:0;}',
    '.head[hidden],.foot[hidden]{display:none;}',
    '.items{display:flex;flex-direction:column;gap:var(--sv-space-4);flex:1;min-height:0;}',
    ':host([variant="topbar"]) .items{flex-direction:row;align-items:center;gap:var(--sv-space-8);flex:none;}',
    '.foot{margin-top:auto;padding-top:var(--sv-space-12);',
    'border-top:var(--sv-border-width-thin) solid var(--sv-border-hairline);}',
    ':host([variant="topbar"]) .foot{display:none;}',
  ].join('');

  var NAV_TEMPLATE =
    '<style>' + COMMON + NAV_CSS + REDUCED + '</style>' +
    '<nav class="nav" part="nav">' +
    '<div class="head" part="header"><slot name="header"></slot></div>' +
    '<div class="items" part="items"><slot></slot></div>' +
    '<div class="foot" part="footer"><slot name="footer"></slot></div>' +
    '</nav>';

  class SvNav extends HTMLElement {
    static get observedAttributes() { return ['label', 'variant']; }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = NAV_TEMPLATE;
      this._nav = root.querySelector('.nav');
      this._head = root.querySelector('.head');
      this._foot = root.querySelector('.foot');
      this._headSlot = root.querySelector('slot[name="header"]');
      this._footSlot = root.querySelector('slot[name="footer"]');
      this._headSlot.addEventListener('slotchange', this._updateSlots.bind(this));
      this._footSlot.addEventListener('slotchange', this._updateSlots.bind(this));
      this._observer = null;
      if (typeof MutationObserver === 'function') {
        var host = this;
        this._observer = new MutationObserver(function () { host._syncDescendants(); });
      }
    }

    connectedCallback() {
      if (this._observer) this._observer.observe(this, { childList: true, subtree: true });
      this._update();
    }

    disconnectedCallback() {
      if (this._observer) this._observer.disconnect();
    }

    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._nav) return;
      var label = this.getAttribute('label') || '';
      if (label) this._nav.setAttribute('aria-label', label);
      else this._nav.removeAttribute('aria-label');
      this._syncDescendants();
      this._updateSlots();
    }

    _syncDescendants() {
      var variant = this.getAttribute('variant') === 'topbar' ? 'topbar' : 'sidebar';
      Array.prototype.forEach.call(this.querySelectorAll('sv-nav-item, sv-nav-section'), function (el) {
        el.setAttribute('nav-variant', variant);
      });
    }

    _updateSlots() {
      if (this._head) this._head.hidden = this._headSlot.assignedNodes().length === 0;
      if (this._foot) this._foot.hidden = this._footSlot.assignedNodes().length === 0;
    }
  }

  var NAV_ITEM_CSS = [
    ':host{display:block;min-width:0;}',
    '.item{display:flex;align-items:center;gap:var(--sv-space-8);box-sizing:border-box;width:100%;',
    'min-height:calc(var(--sv-space-32) + var(--sv-space-4));padding:0 var(--sv-space-8);',
    'border:0;border-left:var(--sv-border-width-strong) solid transparent;border-radius:0;',
    'background:none;color:var(--sv-text-secondary);font-family:var(--sv-font-zh);',
    'font-size:var(--sv-text-caption);line-height:var(--sv-leading-caption);',
    'text-align:left;text-decoration:none;white-space:nowrap;cursor:pointer;',
    '-webkit-appearance:none;appearance:none;}',
    '.item:hover{background:var(--sv-surface-sunken);color:var(--sv-text-primary);}',
    '.item:focus-visible{outline:var(--_ring);outline-offset:var(--_ring-offset);}',
    ':host([current]) .item{color:var(--sv-text-primary);font-weight:var(--sv-weight-medium);',
    'border-left-color:var(--sv-border-strong);}',
    ':host([disabled]) .item{color:var(--sv-text-muted);cursor:not-allowed;}',
    ':host([disabled]) .item:hover{background:none;color:var(--sv-text-muted);}',
    '.dash{display:inline-block;width:var(--sv-space-8);height:var(--sv-border-width-thin);',
    'flex:none;background:var(--sv-border-divider);}',
    ':host([current]) .dash{background:var(--sv-border-strong);}',
    ':host([nav-variant="topbar"]) .item{width:auto;min-height:calc(var(--sv-space-32) + var(--sv-space-24));',
    'border-left:0;border-bottom:var(--sv-border-width-strong) solid transparent;padding:0 var(--sv-space-8);}',
    ':host([nav-variant="topbar"][current]) .item{border-bottom-color:var(--sv-border-strong);}',
    ':host([nav-variant="topbar"]) .dash{display:none;}',
    '.label{min-width:0;overflow:hidden;text-overflow:ellipsis;}',
  ].join('');

  var NAV_ITEM_TEMPLATE =
    '<style>' + COMMON + NAV_ITEM_CSS + REDUCED + '</style>';

  class SvNavItem extends HTMLElement {
    static get observedAttributes() {
      return ['href', 'current', 'disabled', 'nav-variant', 'aria-label'];
    }

    constructor() {
      super();
      this._tag = null;
      this._ctl = null;
      this._root = this.attachShadow({ mode: 'open' });
    }

    connectedCallback() {
      this._buildControl();
      this._update();
    }

    attributeChangedCallback(name) {
      if (!this._root) return;
      if (name === 'href') this._buildControl();
      this._update();
    }

    _buildControl() {
      var href = this.getAttribute('href');
      var tag = href ? 'a' : 'button';
      if (this._tag === tag) return;
      this._tag = tag;
      while (this._root.firstChild) this._root.removeChild(this._root.firstChild);
      var style = document.createElement('style');
      style.textContent = COMMON + NAV_ITEM_CSS + REDUCED;
      var ctl = document.createElement(tag);
      ctl.className = 'item';
      ctl.setAttribute('part', 'item');
      if (tag === 'a') ctl.setAttribute('href', href || '#');
      else ctl.type = 'button';
      ctl.innerHTML = '<slot name="icon"><span class="dash" aria-hidden="true"></span></slot>' +
        '<span class="label" part="label"><slot></slot></span>';
      this._root.appendChild(style);
      this._root.appendChild(ctl);
      this._ctl = ctl;
    }

    _update() {
      if (!this._ctl) return;
      var current = this.hasAttribute('current');
      if (current) this._ctl.setAttribute('aria-current', 'page');
      else this._ctl.removeAttribute('aria-current');
      var disabled = this.hasAttribute('disabled');
      if (disabled) this._ctl.setAttribute('aria-disabled', 'true');
      else this._ctl.removeAttribute('aria-disabled');
      if (this._tag === 'a') {
        this._ctl.setAttribute('href', this.getAttribute('href') || '#');
        if (disabled) this._ctl.tabIndex = -1;
        else this._ctl.removeAttribute('tabindex');
      } else {
        this._ctl.disabled = disabled;
      }
      var ariaLabel = this.getAttribute('aria-label');
      if (ariaLabel) this._ctl.setAttribute('aria-label', ariaLabel);
      else this._ctl.removeAttribute('aria-label');
    }
  }

  var NAV_SECTION_CSS = [
    ':host{display:block;min-width:0;}',
    '.section{display:flex;flex-direction:column;gap:var(--sv-space-4);min-width:0;}',
    '.label{padding:var(--sv-space-8) var(--sv-space-8) var(--sv-space-4);',
    'font-family:var(--sv-font-mono);font-size:var(--sv-text-micro);',
    'letter-spacing:var(--sv-tracking-codename);text-transform:uppercase;',
    'color:var(--sv-text-secondary);}',
    '.label[hidden]{display:none;}',
    ':host([nav-variant="topbar"]) .label{display:none;}',
  ].join('');

  var NAV_SECTION_TEMPLATE =
    '<style>' + COMMON + NAV_SECTION_CSS + REDUCED + '</style>' +
    '<div class="section" part="section">' +
    '<span class="label" part="label"><slot name="label"><span class="label-fallback"></span></slot></span>' +
    '<slot></slot>' +
    '</div>';

  class SvNavSection extends HTMLElement {
    static get observedAttributes() { return ['label']; }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = NAV_SECTION_TEMPLATE;
      this._label = root.querySelector('.label');
      this._labelFallback = root.querySelector('.label-fallback');
    }

    connectedCallback() { this._update(); }
    attributeChangedCallback() { this._update(); }

    _update() {
      if (!this._labelFallback) return;
      var label = this.getAttribute('label') || '';
      this._labelFallback.textContent = label;
      this._label.hidden = !label;
    }
  }

  /* ---------------------------------------------------------------
   * sv-data-card
   * 数据卡：标题 / meta / 字段列表 / 操作插槽 / 说明插槽 / 状态标记。
   * 不内置任何业务数字；value 全部由调用方通过 fields 传入。
   * 属性：title meta variant(default|inverse) fields(JSON) footer-note loading
   * 事件：sv-action（内嵌空态操作）
   * 插槽：title / actions / note / empty
   * ------------------------------------------------------------- */
  var DATA_CARD_CSS = [
    ':host{display:block;}',
    '.card{box-sizing:border-box;padding:calc(var(--sv-space-16) + var(--sv-space-4));',
    'border:var(--sv-border-width-thin) solid var(--sv-border-hairline);',
    'background:var(--sv-surface-raised);color:var(--sv-text-primary);}',
    '.head{display:flex;align-items:baseline;justify-content:space-between;',
    'gap:var(--sv-space-12);margin-bottom:var(--sv-space-8);}',
    '.title{margin:0;font-size:var(--sv-text-heading-m);font-weight:var(--sv-weight-medium);',
    'line-height:var(--sv-leading-heading);color:var(--sv-text-primary);}',
    '.title[hidden]{display:none;}',
    '.meta{font-family:var(--sv-font-mono);font-size:var(--sv-text-micro);',
    'letter-spacing:var(--sv-tracking-codename);text-transform:uppercase;',
    'color:var(--sv-text-secondary);white-space:nowrap;}',
    '.meta[hidden]{display:none;}',
    '.fields{margin:0;display:flex;flex-direction:column;}',
    '.field{display:grid;grid-template-columns:minmax(0, 0.9fr) minmax(0, 1.1fr);',
    'gap:var(--sv-space-12);align-items:baseline;padding:var(--sv-space-8) 0;',
    'border-bottom:var(--sv-border-width-thin) solid var(--sv-border-hairline);}',
    '.field:last-child{border-bottom:0;}',
    '.key{margin:0;font-size:var(--sv-text-caption);line-height:var(--sv-leading-caption);',
    'color:var(--sv-text-secondary);}',
    '.val{margin:0;font-size:var(--sv-text-body-m);line-height:var(--sv-leading-body);',
    'color:var(--sv-text-primary);min-width:0;overflow-wrap:anywhere;}',
    '.val .unit{font-family:var(--sv-font-mono);font-size:var(--sv-text-micro);',
    'letter-spacing:var(--sv-tracking-codename);color:var(--sv-text-secondary);',
    'margin-left:var(--sv-space-4);}',
    '.val .mono{font-family:var(--sv-font-mono);font-size:var(--sv-text-caption);',
    'letter-spacing:var(--sv-tracking-codename);}',
    '.val.is-empty{color:var(--sv-text-secondary);}',
    '.accent{color:var(--sv-text-accent);}',
    '.skel{display:inline-block;height:calc(var(--sv-space-4) + var(--sv-space-4) / 2);',
    'background:var(--sv-border-hairline);width:calc(var(--sv-space-64) + var(--sv-space-16));}',
    '.actions{display:flex;align-items:center;gap:var(--sv-space-8);margin-top:var(--sv-space-12);}',
    '.actions[hidden]{display:none;}',
    '.note{margin:var(--sv-space-12) 0 0;font-size:var(--sv-text-caption);',
    'line-height:var(--sv-leading-caption);color:var(--sv-text-secondary);}',
    '.note[hidden]{display:none;}',
    '.empty-wrap[hidden]{display:none;}',
    'sv-empty::part(empty){padding:var(--sv-space-24) var(--sv-space-8);gap:var(--sv-space-8);}',
    ':host([variant="inverse"]) .card{background:var(--sv-surface-inverse);',
    'border-color:var(--sv-border-inverse);color:var(--sv-text-inverse-primary);}',
    ':host([variant="inverse"]) .title{color:var(--sv-text-inverse-primary);}',
    ':host([variant="inverse"]) .meta{color:var(--sv-text-inverse-secondary);}',
    ':host([variant="inverse"]) .key{color:var(--sv-text-inverse-secondary);}',
    ':host([variant="inverse"]) .val{color:var(--sv-text-inverse-primary);}',
    ':host([variant="inverse"]) .field{border-bottom-color:var(--sv-border-inverse);}',
    ':host([variant="inverse"]) .note{color:var(--sv-text-inverse-secondary);}',
    ':host([variant="inverse"]) .val .unit{color:var(--sv-text-inverse-secondary);}',
    ':host([variant="inverse"]) .val.is-empty{color:var(--sv-text-inverse-muted);}',
    ':host([variant="inverse"]) .accent{color:var(--sv-text-accent);}',
    ':host([variant="inverse"]) sv-empty::part(empty){color:var(--sv-text-inverse-primary);}',
    ':host([loading]) .val{color:var(--sv-text-secondary);}',
  ].join('');

  var DATA_CARD_TEMPLATE =
    '<style>' + COMMON + DATA_CARD_CSS + REDUCED + '</style>' +
    '<section class="card" part="card">' +
    '<div class="head">' +
    '<h2 class="title" part="title"><slot name="title"><span class="title-fallback"></span></slot></h2>' +
    '<span class="meta" part="meta"><slot name="meta"><span class="meta-fallback"></span></slot></span>' +
    '</div>' +
    '<dl class="fields" part="fields"></dl>' +
    '<div class="empty-wrap" part="empty" hidden></div>' +
    '<div class="actions" part="actions"><slot name="actions"></slot></div>' +
    '<p class="note" part="note"><slot name="note"><span class="note-fallback"></span></slot></p>' +
    '</section>';

  class SvDataCard extends HTMLElement {
    static get observedAttributes() {
      return ['title', 'meta', 'variant', 'fields', 'footer-note', 'loading',
        'empty-title', 'empty-hint', 'aria-label'];
    }

    constructor() {
      super();
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = DATA_CARD_TEMPLATE;
      this._card = root.querySelector('.card');
      this._titleFallback = root.querySelector('.title-fallback');
      this._metaFallback = root.querySelector('.meta-fallback');
      this._titleEl = root.querySelector('.title');
      this._metaEl = root.querySelector('.meta');
      this._fields = root.querySelector('.fields');
      this._emptyWrap = root.querySelector('.empty-wrap');
      this._actions = root.querySelector('.actions');
      this._actionsSlot = root.querySelector('slot[name="actions"]');
      this._note = root.querySelector('.note');
      this._noteFallback = root.querySelector('.note-fallback');
      this._fieldsData = [];
      this._titleId = nextId('sv-data-card-title');
      this._noteId = nextId('sv-data-card-note');
      this._titleEl.id = this._titleId;
      this._note.id = this._noteId;
      this._card.setAttribute('aria-labelledby', this._titleId);
      this._actionsSlot.addEventListener('slotchange', this._updateActions.bind(this));
    }

    connectedCallback() { this._update(); }
    attributeChangedCallback() { this._update(); }

    _readFields() {
      var raw;
      try { raw = JSON.parse(this.getAttribute('fields') || '[]'); } catch (err) { raw = []; }
      if (!Array.isArray(raw)) raw = [];
      return raw.filter(function (field) {
        return field && typeof field === 'object' && !Array.isArray(field);
      }).map(function (field) {
        return {
          label: typeof field.label === 'string' ? field.label : '',
          value: field.value === undefined || field.value === null ? '' : String(field.value),
          unit: typeof field.unit === 'string' ? field.unit : '',
          mono: !!field.mono,
          status: typeof field.status === 'string' ? field.status : '',
          unverified: !!field.unverified,
          accent: !!field.accent,
        };
      });
    }

    _update() {
      if (!this._card) return;
      var title = this.getAttribute('title') || '';
      var meta = this.getAttribute('meta') || '';
      this._titleFallback.textContent = title;
      this._titleEl.hidden = !title;
      this._metaFallback.textContent = meta;
      this._metaEl.hidden = !meta;
      var ariaLabel = this.getAttribute('aria-label');
      if (ariaLabel) this._card.setAttribute('aria-label', ariaLabel);
      else this._card.removeAttribute('aria-label');
      var note = this.getAttribute('footer-note') || '';
      this._noteFallback.textContent = note;
      this._note.hidden = !note;
      if (note) this._card.setAttribute('aria-describedby', this._noteId);
      else this._card.removeAttribute('aria-describedby');
      this._card.setAttribute('aria-busy', this.hasAttribute('loading') ? 'true' : 'false');
      this._fieldsData = this._readFields();
      this._renderFields();
      this._updateActions();
      this._renderEmpty();
    }

    _renderFields() {
      var dl = this._fields;
      while (dl.firstChild) dl.removeChild(dl.firstChild);
      var loading = this.hasAttribute('loading');
      var inverse = this.getAttribute('variant') === 'inverse';
      var host = this;
      this._fieldsData.forEach(function (field) {
        var row = document.createElement('div');
        row.className = 'field';
        row.setAttribute('part', 'field');
        var key = document.createElement('dt');
        key.className = 'key';
        key.setAttribute('part', 'key');
        key.textContent = field.label;
        var val = document.createElement('dd');
        val.className = 'val';
        val.setAttribute('part', 'value');
        if (loading) {
          var skel = document.createElement('span');
          skel.className = 'skel';
          skel.setAttribute('aria-hidden', 'true');
          val.appendChild(skel);
        } else if (field.status) {
          var status = document.createElement('sv-status');
          status.setAttribute('status', field.status);
          if (inverse) status.setAttribute('inverse', '');
          val.appendChild(status);
        } else if (field.unverified) {
          val.textContent = '—';
          val.classList.add('is-empty');
          val.setAttribute('aria-label', '待核验');
        } else {
          val.textContent = field.value === '' ? '—' : field.value;
          if (field.value === '') {
            val.classList.add('is-empty');
            val.setAttribute('aria-label', '空值');
          } else if (field.mono) {
            var mono = document.createElement('span');
            mono.className = 'mono';
            mono.textContent = field.value;
            val.textContent = '';
            val.appendChild(mono);
          }
          if (field.unit) {
            var unit = document.createElement('span');
            unit.className = 'unit';
            unit.textContent = field.unit;
            val.appendChild(unit);
          }
          if (field.accent && inverse) val.classList.add('accent');
        }
        row.appendChild(key);
        row.appendChild(val);
        dl.appendChild(row);
      });
      this._card.setAttribute('data-empty', this._fieldsData.length === 0 ? 'true' : 'false');
      void host;
    }

    _renderEmpty() {
      if (!this._emptyWrap) return;
      var isEmpty = this._fieldsData.length === 0 && !this.hasAttribute('loading');
      this._fields.hidden = isEmpty;
      this._emptyWrap.hidden = !isEmpty;
      if (!isEmpty) {
        while (this._emptyWrap.firstChild) this._emptyWrap.removeChild(this._emptyWrap.firstChild);
        return;
      }
      if (this._emptyWrap.firstChild) return;
      var empty = document.createElement('sv-empty');
      empty.setAttribute('title', this.getAttribute('empty-title') || '还没有任务。');
      var hint = this.getAttribute('empty-hint');
      if (hint) empty.setAttribute('hint', hint);
      var host = this;
      empty.addEventListener('sv-action', function () { emit(host, 'sv-action', null); });
      this._emptyWrap.appendChild(empty);
    }

    _updateActions() {
      if (!this._actions || !this._actionsSlot) return;
      this._actions.hidden = this._actionsSlot.assignedNodes().length === 0;
    }

    get fields() { return this._fieldsData.slice(); }
    set fields(value) { this.setAttribute('fields', JSON.stringify(value || [])); }
  }

  /* ---------------------------------------------------------------
   * 轻提示堆叠助手：SVComponents.toast({...})
   * ------------------------------------------------------------- */
  var LAYER_CLASS = 'sv-toast-layer';

  function ensureLayerStyle() {
    if (!document.head || document.getElementById('sv-toast-layer-style')) return;
    var style = document.createElement('style');
    style.id = 'sv-toast-layer-style';
    style.textContent = '.' + LAYER_CLASS + '{position:fixed;inset-inline-end:var(--sv-space-24);' +
      'inset-block-end:var(--sv-space-24);display:flex;flex-direction:column;align-items:flex-end;' +
      'gap:var(--sv-space-8);z-index:40;max-width:calc(100vw - var(--sv-space-48));}';
    document.head.appendChild(style);
  }

  function toastLayer() {
    ensureLayerStyle();
    var layer = document.querySelector('.' + LAYER_CLASS);
    if (!layer) {
      layer = document.createElement('div');
      layer.className = LAYER_CLASS;
      document.body.appendChild(layer);
    }
    return layer;
  }

  function toast(options) {
    options = options || {};
    if (typeof document === 'undefined' || !document.body) return null;
    var el = document.createElement('sv-toast');
    if (options.status) el.setAttribute('status', options.status);
    if (options.message) el.setAttribute('message', options.message);
    if (options.detail) el.setAttribute('detail', options.detail);
    if (options.actionLabel) el.setAttribute('action-label', options.actionLabel);
    if (options.dismissLabel) el.setAttribute('dismiss-label', options.dismissLabel);
    if (options.duration != null) el.setAttribute('duration', String(options.duration));
    if (options.persistent) el.setAttribute('persistent', '');
    if (typeof options.onAction === 'function') el.addEventListener('sv-action', options.onAction);
    if (typeof options.onDismiss === 'function') el.addEventListener('sv-dismiss', options.onDismiss);
    toastLayer().appendChild(el);
    return el;
  }

  /* ---------------------------------------------------------------
   * 注册：DOMContentLoaded 后自动定义；重复引入安全
   * ------------------------------------------------------------- */
  function boot() {
    define('sv-button', SvButton);
    define('sv-input', SvInput);
    define('sv-select', SvSelect);
    define('sv-tag', SvTag);
    define('sv-status', SvStatus);
    define('sv-empty', SvEmpty);
    define('sv-toast', SvToast);
    define('sv-tab', SvTab);
    define('sv-panel', SvPanel);
    define('sv-tabs', SvTabs);
    define('sv-table', SvTable);
    define('sv-dialog', SvDialog);
    define('sv-loading', SvLoading);
    define('sv-error', SvError);
    define('sv-nav-item', SvNavItem);
    define('sv-nav-section', SvNavSection);
    define('sv-nav', SvNav);
    define('sv-data-card', SvDataCard);

    window.SVComponents = {
      version: VERSION,
      components: ['sv-button', 'sv-input', 'sv-select', 'sv-tag', 'sv-status', 'sv-empty', 'sv-toast',
        'sv-table', 'sv-tabs', 'sv-dialog', 'sv-loading', 'sv-error', 'sv-nav', 'sv-nav-item', 'sv-data-card'],
      subComponents: ['sv-tab', 'sv-panel', 'sv-nav-section'],
      toast: toast,
      reducedMotion: function () {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
      },
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
