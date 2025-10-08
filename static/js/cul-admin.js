(function(){
  function esc(s){ return String(s||''); }

  function prefillAdminForm(data){
    if(!data) return;
    const get = id => document.getElementById(id);
    get('adm_id')?.setAttribute('value', data.id ?? '');
    const setVal = (id, v) => { const el = get(id); if(el) el.value = v ?? ''; };
    setVal('adm_title', data.title);
    setVal('adm_category', data.category);
    setVal('adm_difficulty', data.difficulty);
    setVal('adm_duration', data.duration);
    setVal('adm_length', data.length_km);
    setVal('adm_rating', data.rating);
    setVal('adm_desc', data.short_description);
  }

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
    if(typeof fetchAndRender === 'function') return fetchAndRender({}, false);
    // fallback: перезагрузить страницу
    location.reload();
  }

  function openPanel(){ document.getElementById('adm_panel')?.classList.add('active'); }
  function closePanel(){ document.getElementById('adm_panel')?.classList.remove('active'); }

  // 1) Делегируем клики по кнопкам на карточках
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('button.adm-btn');
    if(!btn) return;
    e.preventDefault(); e.stopPropagation();
    const id = btn.dataset.id;
    const act = btn.dataset.adm;

    try{
      if(act === 'edit'){
        openPanel();
        document.getElementById('adm_id')?.setAttribute('value', id || '');
        const j = await getJSON('/cul/admin/get/'+id);
        prefillAdminForm(j.data);
      }else if(act === 'publish'){
        await postForm('/cul/admin/toggle_publish/'+id);
        refreshGrid();
      }else if(act === 'delete'){
        if(!confirm('Удалить маршрут #'+id+'?')) return;
        await postForm('/cul/admin/delete/'+id);
        refreshGrid();
      }
    }catch(err){
      alert('Ошибка: ' + (err?.message || err));
    }
  }, {capture:true});

  // 2) Сабмит формы админ-панели
  const admForm = document.getElementById('adm_form');
  if(admForm){
    admForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      try{
        const fd = new FormData(admForm);
        const id = (fd.get('id') || '').toString().trim();
        const url = id ? ('/cul/admin/update/' + id) : '/cul/admin/create';
        await postForm(url, fd);
        closePanel();
        refreshGrid();
      }catch(err){
        alert('Ошибка: ' + (err?.message || err));
      }
    });
  }

  // 3) Кнопки открыть/закрыть/новый
  document.getElementById('adm_open')?.addEventListener('click', openPanel);
  document.getElementById('adm_close')?.addEventListener('click', closePanel);
  document.getElementById('adm_new')?.addEventListener('click', function(){
    admForm?.reset();
    const id = document.getElementById('adm_id');
    if(id) id.value = '';
  });

})();