(function(){
  function get(id){ return document.getElementById(id); }
  function openPanel(){ get('adm_panel')?.classList.add('active'); }
  function closePanel(){ get('adm_panel')?.classList.remove('active'); }

  async function getJSON(url){
    const res = await fetch(url, {credentials:'include'});
    const data = await res.json().catch(()=>({}));
    if(!res.ok || data?.success === false) throw new Error(data?.error || 'Ошибка');
    return data;
  }
  async function postForm(url, fd=new FormData()){
    const res = await fetch(url, {method:'POST', body:fd, credentials:'include'});
    const data = await res.json().catch(()=>({}));
    if(!res.ok || data?.success === false) throw new Error(data?.error || 'Ошибка');
    return data;
  }
  function refreshGrid(){
    // Если на странице есть JSON-подгрузка — перерисуем
    if(typeof fetchAndRender === 'function') return fetchAndRender();
    // Иначе просто перезагрузим
    location.reload();
  }
  function prefill(data){
    if(!data) return;
    const setVal = (id, v) => { const el=get(id); if(el) el.value = (v??''); };
    setVal('adm_id', data.id);
    setVal('adm_title', data.title);
    setVal('adm_desc', data.short_description);
    setVal('adm_descr_full', data.description);
    setVal('adm_duration', data.duration);
    const diff = get('adm_difficulty'); if(diff) diff.value = data.difficulty || '';
    const chk = get('adm_published'); if(chk) chk.checked = !!data.is_published;
  }

  // Делегирование кликов по карточкам (edit / publish / delete)
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('button.adm-btn, label.adm-btn, input[data-adm="publish"]');
    if(!btn) return;

    // Если это переключатель публикации внутри карточки
    const toggleInput = btn.matches('input[data-adm="publish"]') ? btn : btn.querySelector('input[data-adm="publish"]');
    if(toggleInput){
      e.preventDefault(); e.stopPropagation();
      const id = toggleInput.dataset.id;
      try{
        await postForm(`/cul/admin/toggle_publish/${id}`);
      }catch(err){
        alert('Ошибка: ' + (err?.message || err));
        // откат чекбокса
        toggleInput.checked = !toggleInput.checked;
        return;
      }
      // мягкий рефреш
      if(typeof fetchAndRender === 'function') fetchAndRender();
      return;
    }

    // Кнопки edit/delete
    const id  = btn.dataset.id;
    const act = btn.dataset.adm;
    if(!id || !act) return;

    if(act === 'edit'){
      e.preventDefault(); e.stopPropagation();
      openPanel();
      try{
        const j = await getJSON(`/cul/admin/get/${id}`);
        prefill(j.data);
      }catch(err){
        alert('Ошибка: ' + (err?.message || err));
      }
      return;
    }

    if(act === 'delete'){
      e.preventDefault(); e.stopPropagation();
      if(!confirm(`Удалить маршрут #${id}?`)) return;
      try{
        await postForm(`/cul/admin/delete/${id}`);
        refreshGrid();
      }catch(err){
        alert('Ошибка: ' + (err?.message || err));
      }
      return;
    }
  }, {capture:true});

  // Сабмит формы (создать / обновить)
  const form = get('adm_form');
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try{
      const fd = new FormData(form);
      const id = (fd.get('id') || '').toString().trim();
      const url = id ? `/cul/admin/update/${id}` : '/cul/admin/create';
      await postForm(url, fd);
      closePanel();
      refreshGrid();
    }catch(err){
      alert('Ошибка: ' + (err?.message || err));
    }
  });

  // Переключатель публикации внутри админ‑панели — шлет toggle сразу
  const panelToggle = get('adm_published');
  panelToggle?.addEventListener('change', async (e)=>{
    const id = get('adm_id')?.value;
    if(!id) return;
    try{
      await postForm(`/cul/admin/toggle_publish/${id}`);
      // можно ничего не обновлять; состояние уже в чекбоксе
    }catch(err){
      alert('Ошибка: ' + (err?.message || err));
      e.target.checked = !e.target.checked;
    }
  });

  // Кнопки открыть/закрыть/новый
  get('adm_open')?.addEventListener('click', openPanel);
  get('adm_close')?.addEventListener('click', closePanel);
  get('adm_new')?.addEventListener('click', function(){
    form?.reset();
    const id = get('adm_id'); if(id) id.value='';
    const chk = get('adm_published'); if(chk) chk.checked = true;
  });
})();