/* FinTrack — small progressive enhancements. Every page works without this file;
   it adds dialogs, the delete confirmation, live category hints, ledger filters,
   toasts, drag-and-drop upload and the fetch-based sign-in forms. */
(function () {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /* ---- greeting by the viewer's local time ---- */
  $$('[data-greeting]').forEach((el) => {
    const h = new Date().getHours();
    el.textContent = h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  });

  /* ---- dialogs ---- */
  $$('[data-open]').forEach((btn) => btn.addEventListener('click', () => {
    const dlg = document.getElementById(btn.dataset.open);
    if (!dlg) return;
    dlg.showModal();
    const first = $('input[name="amount"], input:not([type=hidden])', dlg);
    if (first) setTimeout(() => first.focus(), 30);
  }));
  $$('dialog').forEach((dlg) => {
    $$('[data-close]', dlg).forEach((b) => b.addEventListener('click', () => dlg.close()));
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); }); // backdrop
  });

  /* ---- account menu: close on outside click / Escape ---- */
  const menu = $('.user-menu');
  if (menu) {
    document.addEventListener('click', (e) => { if (!menu.contains(e.target)) menu.open = false; });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') menu.open = false; });
  }

  /* ---- confirm before destructive forms ---- */
  const confirmDlg = $('#confirm-dialog');
  let pendingForm = null;
  $$('form[data-confirm]').forEach((form) => form.addEventListener('submit', (e) => {
    if (!confirmDlg || form.dataset.confirmed) return;
    e.preventDefault();
    pendingForm = form;
    $('[data-confirm-text]', confirmDlg).textContent = form.dataset.confirm;
    confirmDlg.showModal();
  }));
  if (confirmDlg) {
    $('[data-confirm-ok]', confirmDlg).addEventListener('click', () => {
      if (!pendingForm) return;
      pendingForm.dataset.confirmed = '1';
      confirmDlg.close();
      pendingForm.submit();
    });
  }

  /* ---- live category hint in the add form ---- */
  const addForm = $('.add-form');
  if (addForm) {
    const desc = $('[name="description"]', addForm);
    const hint = $('[data-category-hint]', addForm);
    const url = addForm.dataset.categoryUrl;
    let timer = null;
    let seq = 0;
    const update = () => {
      const q = desc.value.trim();
      const kind = ($('[name="kind"]:checked', addForm) || {}).value;
      if (!q) { hint.textContent = ''; return; }
      const mine = ++seq;
      fetch(`${url}?q=${encodeURIComponent(q)}&kind=${kind}`, { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (mine !== seq || !data || !data.label) return;
          hint.innerHTML = '';
          hint.append('Files under ');
          const chip = document.createElement('span');
          chip.className = 'chip chip-accent';
          chip.textContent = data.label;
          hint.append(chip);
        })
        .catch(() => {});
    };
    desc.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(update, 280); });
    $$('[name="kind"]', addForm).forEach((r) => r.addEventListener('change', update));
    addForm.addEventListener('submit', () => {
      const btn = $('button[type=submit]', addForm);
      if (addForm.checkValidity()) btn.disabled = true;
    });
  }

  /* ---- toasts ---- */
  $$('.toast').forEach((toast, i) => {
    const dismiss = () => {
      toast.classList.add('is-leaving');
      setTimeout(() => toast.remove(), 260);
    };
    $('[data-dismiss]', toast).addEventListener('click', dismiss);
    setTimeout(dismiss, 4800 + i * 600);
  });

  /* ---- ledger filters (transactions page) ---- */
  const filters = $('[data-filters]');
  if (filters) {
    const q = $('[data-filter-q]', filters);
    const cat = $('[data-filter-category]', filters);
    const kinds = $$('[data-filter-kind]', filters);
    const count = $('[data-filter-count]', filters);
    const empty = $('[data-filter-empty]');
    const months = $$('[data-month]');
    let kind = 'all';

    const apply = () => {
      const needle = q.value.trim().toLowerCase();
      let shown = 0;
      months.forEach((m) => {
        let inMonth = 0;
        $$('.txn', m).forEach((row) => {
          const ok = (kind === 'all' || row.dataset.kind === kind)
            && (!cat.value || row.dataset.category === cat.value)
            && (!needle || row.dataset.search.includes(needle));
          row.hidden = !ok;
          row.classList.toggle('no-rule', ok && inMonth === 0); // first visible row: no divider above
          if (ok) inMonth += 1;
        });
        m.hidden = inMonth === 0;
        shown += inMonth;
      });
      count.textContent = `${shown} shown`;
      if (empty) empty.hidden = shown !== 0;
    };

    q.addEventListener('input', apply);
    cat.addEventListener('change', apply);
    kinds.forEach((b) => b.addEventListener('click', () => {
      kind = b.dataset.filterKind;
      kinds.forEach((k) => k.setAttribute('aria-pressed', String(k === b)));
      apply();
    }));
    const reset = $('[data-filter-reset]');
    if (reset) reset.addEventListener('click', () => {
      q.value = ''; cat.value = ''; kind = 'all';
      kinds.forEach((k) => k.setAttribute('aria-pressed', String(k.dataset.filterKind === 'all')));
      apply();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === '/' && document.activeElement.tagName !== 'INPUT') { e.preventDefault(); q.focus(); }
    });
  }

  /* ---- receipt upload: drag and drop + chosen file name + busy state ---- */
  const drop = $('[data-dropzone]');
  if (drop) {
    const input = $('[data-file]', drop);
    const pill = $('[data-file-pill]');
    const show = () => {
      const f = input.files && input.files[0];
      pill.classList.toggle('is-on', !!f);
      if (f) $('[data-file-name]', pill).textContent = `${f.name} · ${(f.size / 1024).toFixed(0)} KB`;
    };
    input.addEventListener('change', show);
    ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('is-over'); }));
    ['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, () => drop.classList.remove('is-over')));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
    const form = $('[data-upload]');
    form.addEventListener('submit', () => {
      const btn = $('button[type=submit]', form);
      btn.disabled = true;
      $('span', btn).textContent = btn.dataset.busyText;
    });
  }

  /* ---- password visibility ---- */
  $$('[data-toggle-password]').forEach((btn) => btn.addEventListener('click', () => {
    const input = btn.parentElement.querySelector('input');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    $('use', btn).setAttribute('href', show ? '#i-eye-off' : '#i-eye');
  }));

  /* ---- sign-in / sign-up: post with fetch, show errors inline ---- */
  $$('[data-auth-form]').forEach((form) => {
    const box = $('[data-form-error]', form);
    const btn = $('button[type=submit]', form);
    const fail = (msg) => {
      $('span', box).textContent = msg;
      box.classList.add('is-on');
      btn.disabled = false;
      btn.classList.remove('is-loading');
    };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      box.classList.remove('is-on');
      const missing = $$('input[required]', form).find((i) => !i.value.trim());
      if (missing) { missing.focus(); return fail('Fill in every field to continue.'); }
      btn.disabled = true;
      btn.classList.add('is-loading');
      try {
        const res = await fetch(form.action, {
          method: 'POST', body: new FormData(form), credentials: 'same-origin',
          headers: { 'X-Requested-With': 'fetch' },
        });
        const data = await res.json();
        if (data.success) { window.location.assign(data.redirect || '/'); return; }
        fail(data.message || 'Something went wrong. Please try again.');
      } catch (err) {
        fail("We couldn't reach FinTrack. Check your connection and try again.");
      }
    });
  });
})();
