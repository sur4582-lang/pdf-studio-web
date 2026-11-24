(() => {
  const root = document.getElementById('app');
  if (!root) { // header dropdown works with CSS only
    return;
  }
  const $ = id => document.getElementById(id);
  const key = root.dataset.key, ordered = root.dataset.ordered === 'true';
  const accept = root.dataset.accept.split(',').map(s => s.trim().toLowerCase()).filter(s => s.startsWith('.'));
  const maxMB = +root.dataset.maxmb, maxFiles = +root.dataset.maxfiles;
  const drop = $('drop'), picker = $('picker'), list = $('files'), form = $('opts');
  const actions = $('actions'), go = $('go'), prog = $('prog'), fill = $('barFill'), ptxt = $('progText');
  const err = $('err'), result = $('result');
  let files = [], busy = false, jobId = null;

  const fmtSize = n => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(2) + ' MB';
  const showErr = m => { err.textContent = m; err.hidden = !m; };

  function add(newFiles) {
    showErr('');
    for (const f of newFiles) {
      const ext = '.' + f.name.split('.').pop().toLowerCase();
      if (!accept.includes(ext)) { showErr(`"${f.name}" is not a supported file type.`); continue; }
      if (files.length >= maxFiles) { showErr(`You can add up to ${maxFiles} files.`); break; }
      files.push(f);
    }
    const total = files.reduce((a, f) => a + f.size, 0);
    if (total > maxMB * 1048576) showErr(`Total size is over ${maxMB} MB. Remove some files.`);
    render();
  }

  function render() {
    list.innerHTML = '';
    files.forEach((f, i) => {
      const li = document.createElement('li');
      if (ordered) { li.draggable = true; li.dataset.i = i; }
      li.innerHTML = `<span class="n"></span><span class="s">${fmtSize(f.size)}</span>`;
      li.querySelector('.n').textContent = (ordered ? (i + 1) + '. ' : '') + f.name;
      if (ordered) {
        const up = mk('↑', 'Move up', () => move(i, -1)), dn = mk('↓', 'Move down', () => move(i, 1));
        li.append(up, dn);
        li.addEventListener('dragstart', e => { li.classList.add('dragging'); e.dataTransfer.setData('text/plain', i); });
        li.addEventListener('dragend', () => li.classList.remove('dragging'));
        li.addEventListener('dragover', e => e.preventDefault());
        li.addEventListener('drop', e => { e.preventDefault(); const from = +e.dataTransfer.getData('text/plain'); if (!isNaN(from) && from !== i) { files.splice(i, 0, files.splice(from, 1)[0]); render(); } });
      }
      li.append(mk('✕', 'Remove', () => { files.splice(i, 1); render(); }));
      list.append(li);
    });
    const has = files.length > 0;
    form.hidden = actions.hidden = !has;
    if (has) refreshOpts();
  }
  function mk(t, label, fn) { const b = document.createElement('button'); b.type = 'button'; b.textContent = t; b.title = b.ariaLabel = label; b.onclick = fn; return b; }
  function move(i, d) { const j = i + d; if (j < 0 || j >= files.length) return; [files[i], files[j]] = [files[j], files[i]]; render(); }

  drop.onclick = () => picker.click();
  drop.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); picker.click(); } };
  picker.onchange = () => { add([...picker.files]); picker.value = ''; };
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => add([...e.dataTransfer.files]));

  // option visibility / help text come from the original desktop app's rules
  let t = null;
  function refreshOpts() { clearTimeout(t); t = setTimeout(async () => {
    try {
      const r = await fetch('/api/visible/' + key, { method: 'POST', body: new FormData(form) });
      const d = await r.json();
      form.querySelectorAll('.field').forEach(el => {
        const k = el.dataset.key;
        el.hidden = d.visible[k] === false;
        const h = el.querySelector('.help'); if (h) h.textContent = d.help[k] || '';
      });
    } catch (e) { /* options just stay visible */ }
  }, 60); }
  form.addEventListener('input', e => {
    const sv = e.target.closest('.field')?.querySelector('.sv');
    if (sv) sv.textContent = sv.dataset.fmt.replace('{}', e.target.value);
    refreshOpts();
  });
  form.addEventListener('submit', e => e.preventDefault());

  go.onclick = () => {
    if (busy || !files.length) return;
    showErr(''); result.hidden = true;
    const fd = new FormData(form);
    // unchecked checkboxes are absent in FormData: send explicit false
    form.querySelectorAll('input[type=checkbox]').forEach(c => fd.set(c.name, c.checked ? '1' : '0'));
    files.forEach(f => fd.append('files', f, f.name));
    busy = true; go.disabled = true; prog.hidden = false; fill.classList.remove('ind'); fill.style.width = '0'; ptxt.textContent = 'Uploading…';
    const x = new XMLHttpRequest();
    x.open('POST', '/api/run/' + key);
    x.upload.onprogress = e => { if (e.lengthComputable) { fill.style.width = (e.loaded / e.total * 100) + '%'; } };
    x.upload.onload = () => { fill.classList.add('ind'); ptxt.textContent = 'Processing… large files can take a minute.'; };
    x.onload = () => {
      busy = false; go.disabled = false; prog.hidden = true;
      let d = {}; try { d = JSON.parse(x.responseText); } catch (_) {}
      if (x.status !== 200) { showErr(d.error || 'Something went wrong. Please try again.'); return; }
      showResult(d);
    };
    x.onerror = () => { busy = false; go.disabled = false; prog.hidden = true; showErr('Network error. Please check your connection and try again.'); };
    x.send(fd);
  };

  function showResult(d) {
    jobId = d.id;
    $('sum').textContent = d.summary || (d.rows && d.rows.length ? d.rows.join(' · ') : '');
    $('outs').innerHTML = '';
    d.files.slice(0, 12).forEach(f => { const li = document.createElement('li'); li.textContent = f.name + ' (' + f.size + ')'; $('outs').append(li); });
    if (d.files.length > 12) { const li = document.createElement('li'); li.textContent = '… and ' + (d.files.length - 12) + ' more (all in the download)'; $('outs').append(li); }
    $('notes').textContent = (d.messages || []).join(' · ') + (d.failed ? `  (${d.failed} file(s) failed)` : '');
    $('dl').href = d.download; $('dl').textContent = 'Download ' + (d.files.length > 1 ? 'ZIP' : 'file');
    $('ttl').textContent = Math.max(1, Math.round(d.ttl / 60));
    result.hidden = false; result.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  const reset = () => {
    if (jobId) navigator.sendBeacon && navigator.sendBeacon('/api/delete/' + jobId);
    jobId = null; files = []; result.hidden = true; showErr(''); render();
  };
  $('again').onclick = reset; $('clear').onclick = () => { files = []; showErr(''); render(); };
})();
