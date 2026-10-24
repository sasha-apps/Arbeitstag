const V='arbeitstag-7';
const CORE=['./','index.html','manifest.json','icon-180.png','icon-192.png','icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(V).then(c=>Promise.all(CORE.map(u=>fetch(new Request(u,{cache:'reload'})).then(r=>{if(r.ok)return c.put(u,r)}).catch(()=>{})))).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==V).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{
  const r=e.request;if(r.method!=='GET')return;const u=new URL(r.url);if(u.origin!==location.origin)return;
  const fresh=r.mode==='navigate'||/\/$|index\.html$|news\.json$|blitzer\.json$/.test(u.pathname);
  if(fresh){ // App-Seite, Nachrichten und Blitzer: zuerst aus dem Netz, offline aus dem Speicher
    const key=/news\.json$/.test(u.pathname)?'news.json':/blitzer\.json$/.test(u.pathname)?'blitzer.json':'./';
    e.respondWith(fetch(r,{cache:'no-store'}).then(res=>{if(res.ok){const cp=res.clone();caches.open(V).then(c=>c.put(key,cp))}return res})
      .catch(()=>caches.match(key).then(h=>h||caches.match('index.html'))));return}
  e.respondWith(caches.open(V).then(async c=>{const hit=await c.match(r);
    const net=fetch(r).then(res=>{if(res&&res.ok)c.put(r,res.clone());return res}).catch(()=>hit);return hit||net}));
});
/* Benachrichtigungen: Inhalt mit dem Abgleich-Schlüssel öffnen, Verkehr anhängen */
const b64d=s=>Uint8Array.from(atob(String(s)),c=>c.charCodeAt(0));
function kvGet(k){return new Promise(res=>{try{const r=indexedDB.open('arbeitstag-push',1);r.onupgradeneeded=()=>r.result.createObjectStore('kv');
  r.onsuccess=()=>{try{const q=r.result.transaction('kv').objectStore('kv').get(k);q.onsuccess=()=>res(q.result||null);q.onerror=()=>res(null)}catch(e){res(null)}};r.onerror=()=>res(null)}catch(e){res(null)}})}
async function openBox(b){const k=await kvGet('key');if(!k||!b)return null;const [iv,ct]=String(b).split('.');
  const key=await crypto.subtle.importKey('raw',b64d(k),{name:'AES-GCM'},false,['decrypt']);
  return JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:b64d(iv)},key,b64d(ct))))}
async function onPush(d){let m=null;try{m=await openBox(d.b)}catch(e){}
  const lines=m&&Array.isArray(m.l)?m.l.slice():[];if(d.tr)lines.push(d.tr);if(!m&&!d.tr)lines.push('Du hast eine Erinnerung. Öffne ArbeitsTag.');
  return self.registration.showNotification(m&&m.t||'ArbeitsTag',{body:lines.join('\n'),tag:d.id||'arbeitstag',icon:'icon-192.png',badge:'icon-192.png',data:{url:'./'}})}
self.handlePush=onPush;
self.addEventListener('push',e=>{let d={};try{d=e.data?e.data.json():{}}catch(x){}e.waitUntil(onPush(d))});
self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{for(const c of cs){if('focus' in c)return c.focus()}return self.clients.openWindow('./')}))});
