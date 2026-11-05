// Compatibility adapter: retain the diary UI and replace cloud calls with this host.
(() => {
  const listeners = [];
  async function request(path, value) {
    try {
      const response = await fetch(path, value === undefined ? {credentials:'same-origin'} : {
        method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(value)
      });
      return await response.json();
    } catch (_) { return {error:{message:'無法連到主機，請確認主機已開啟且遠端連線正常'}}; }
  }
  function emit(event, session) { listeners.forEach(fn => queueMicrotask(() => fn(event, session))); }
  window.supabase = {createClient() { return {
    auth: {
      getSession: () => request('/api/session'),
      onAuthStateChange(fn) {listeners.push(fn); return {data:{subscription:{unsubscribe(){}}}};},
      async signInWithPassword(value) {
        const r = await request('/api/login', value);
        if (!r.error) emit('SIGNED_IN', r.data.session);
        return r;
      },
      async signOut() {const r=await request('/api/logout', {}); if(!r.error)emit('SIGNED_OUT', null); return r;},
      async signUp() {return {error:{message:'本機版帳號請在主機啟動視窗設定'}};}
    },
    from(table) {
      const query = {table, op:'select', filters:{}};
      let pending;
      const builder = {
        select() {return builder;}, order() {return builder;},
        eq(key,value) {query.filters[key]=value;return builder;},
        insert(values) {query.op='insert';query.values=values;return builder;},
        update(values) {query.op='update';query.values=values;return builder;},
        delete() {query.op='delete';return builder;},
        single() {query.single=true;return builder;},
        then(resolve,reject) {pending ||= request('/api/query',query);return pending.then(resolve,reject);}
      };
      return builder;
    }
  };}};
})();
