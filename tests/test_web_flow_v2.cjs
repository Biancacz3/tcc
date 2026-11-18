/* Node contract tests execute the actual UI without a browser or GPIO.
 * Run: node tests/test_web_flow_v2.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {webcrypto} = require('node:crypto');
const html = fs.readFileSync(path.join(__dirname,'../backend/app/web/index.html'),'utf8');
const source = fs.readFileSync(path.join(__dirname,'../backend/app/web/app.js'),'utf8');
const idsInHtml = [...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
assert.equal(new Set(idsInHtml).size,idsInHtml.length,'unique HTML IDs');
assert.doesNotMatch(source,/localStorage|sessionStorage|innerHTML|\.randomUUID\(/,'no persistent tokens, HTML interpolation, or secure-context-only UUID');
const labels = [...html.matchAll(/for="([^"]+)"/g)].map(m=>m[1]);
for (const id of labels) assert.ok(idsInHtml.includes(id),`label target ${id}`);
class Node {
  constructor(tag='div') {
    this.tag=tag;this.children=[];this.listeners={};this.attrs={};this.hidden=false;this.disabled=false;
    this.value='';this.textContent='';this.complete=false;this.naturalWidth=0;this.naturalHeight=0;
    this.checked=false;this.open=false;this.files=[];this.draws=[];this.classes=new Set();
    this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};
  }
  append(...children){this.children.push(...children)}
  replaceChildren(...children){this.children=[...children]}
  setAttribute(k,v){this.attrs[k]=v}
  removeAttribute(k){delete this.attrs[k];if(k==='src')this.src=''}
  addEventListener(name,fn){(this.listeners[name]||=[]).push(fn)}
  focus(){this.focused=true}
  reset(){if(this.resetHook)this.resetHook()}
  showModal(){this.open=true}
  close(){this.open=false;void this.emit('close')}
  remove(){}
  async emit(name,extra={}){const event={defaultPrevented:false,preventDefault(){this.defaultPrevented=true},...extra};for(const fn of this.listeners[name]||[])await fn(event);return event}
  getContext(){const draws=this.draws;return {drawImage(){},strokeRect(...args){draws.push({kind:'box',color:this.strokeStyle,args})},fillRect(){},fillText(label){draws.push({kind:'label',label})},measureText(){return{width:110}}}}
}
const settle=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve))};
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const baseCapture={id:'capture-1',device_id:'camera-1',captured_at:'2026-10-26T03:34:28Z',received_at:'2026-10-26T03:34:28Z',width:640,height:360,size_bytes:5000,plant_id:'acelga-01',plant_name:'Acelga 01',marker_id:10,source:'device',analysis_status:'queued',detection_count:null,analysis_state:null};
const positive={id:'analysis-2',capture_id:'capture-1',created_at:'2026-10-26T03:35:28Z',width:640,height:360,detection_count:1,model_name:'pulgao-v1',model_scope:'pulgao',analysis_state:'positive',detections:[{label:'pulgao',confidence:.75,xyxy:[10,10,20,20]}],unidentified_count:1,candidates:[{id:1,xyxy:[25,25,35,35],status:'unidentified'}],warnings:[]};
const review={...positive,id:'analysis-1',analysis_state:'review',detection_count:0,detections:[],unidentified_count:1};
function setup(role='admin'){
  const nodes=Object.fromEntries(idsInHtml.map(id=>[id,new Node()]));nodes.dashboard.hidden=true;nodes['auto-refresh'].checked=true;
  nodes['upload-form'].resetHook=()=>{nodes['upload-file'].files=[];nodes['upload-file'].value='';nodes['upload-plant'].value='';nodes['upload-name'].value=''};
  const timers=new Map(),intervals=[],windows={},calls=[],state={role,job:'queued',analyses:[],captures:[{...baseCapture}],loggedIn:true,summaryFailure:false,manualFailure:false,uploadRecords:[],users:[{id:'user-1',name:'Bianca',email:'bianca@example.com',role,is_active:true,created_at:'2026-01-01T00:00:00Z'},{id:'user-2',name:'Consulta <img>',email:'consulta@example.com',role:'viewer',is_active:true,created_at:'2026-01-01T00:00:00Z'}]};
  let timerId=0;
  const auth=()=>({user:{id:'user-1',email:'bianca@example.com',name:'Bianca',role:state.role},csrf_token:'csrf-test',expires_at:'2026-11-02T03:00:00Z'});
  const response=(status,data)=>({ok:status>=200&&status<300,status,headers:{get(){return'application/json'}},async json(){return data}});
  async function fetchMock(url,opts){
    calls.push({url,opts});let data;
    if(opts.method!=='GET'&&url!=='/api/v1/auth/login')assert.equal(opts.headers['X-CSRF-Token'],'csrf-test',`CSRF for ${url}`);
    if(url==='/api/v1/auth/me'){if(!state.loggedIn)return response(401,{});data=auth()}
    else if(url==='/api/v1/auth/login'){state.loggedIn=true;data=auth()}
    else if(url==='/api/v1/auth/logout'){state.loggedIn=false;return response(204,null)}
    else if(url==='/api/v1/dashboard/summary'){if(state.summaryFailure)throw Error('offline');data={captures:42,positive_captures:5,review_captures:4,queued_captures:2,failed_captures:1,devices:3,last_received_at:baseCapture.received_at}}
    else if(url.startsWith('/api/v1/captures?')){if(state.holdCaptures){const hold=state.holdCaptures;state.holdCaptures=null;await hold}data=state.captures}
    else if(url.startsWith('/api/v1/devices?'))data=[{id:'camera-1',name:'Raspberry 01',is_active:true,created_at:'2026-01-01T00:00:00Z'}];
    else if(url==='/api/v1/captures/manual'){
      assert.equal(opts.headers['Content-Type'],'image/jpeg');assert.equal(opts.body.name,'planta.jpg');
      state.uploadRecords.push({headers:opts.headers,body:opts.body});if(state.manualFailure){state.manualFailure=false;throw Error('lost response')}
      state.job='queued';state.analyses=[];data={...baseCapture,id:opts.headers['X-Capture-Id'],source:'manual'};state.captures=[data,...state.captures];
    }
    else if(url.endsWith('/image'))return{ok:true,status:200,headers:{get(){return'image/jpeg'}},async blob(){return{jpeg:true}}};
    else if(url.endsWith('/analysis-job/retry')||url.endsWith('/reanalyze')){state.job='queued';data={status:'queued',attempts:0}}
    else if(url.endsWith('/analysis-job'))data={capture_id:'capture-1',status:state.job,attempts:0,last_error:state.job==='failed'?'internal technical error':null};
    else if(url.endsWith('/analyses')){assert.equal(opts.method,'GET','normal UI never calls synchronous IA');data=state.analyses}
    else if(/^\/api\/v1\/captures\/[^/]+$/.test(url))data=baseCapture;
    else if(url.startsWith('/api/v1/plants/positive-history?'))data={items:[{capture_id:'capture-1',analysis_id:'analysis-2',plant_id:null,plant_name:null,device_id:'camera-1',device_name:'Raspberry 01',captured_at:baseCapture.captured_at,analyzed_at:positive.created_at,detection_count:1}],total:1,limit:20,offset:0};
    else if(url.startsWith('/api/v1/users?'))data={items:state.users,total:state.users.length,limit:50,offset:0};
    else if(url==='/api/v1/users'&&opts.method==='POST'){const input=JSON.parse(opts.body);assert.equal(input.role,'viewer');data={...input,id:'user-3',is_active:true};state.users.push(data)}
    else if(url==='/api/v1/users/me/password'){state.loggedIn=false;data={message:'ok',reauthenticate:true}}
    else if(url.endsWith('/reset-password')){if(state.holdReset)await state.holdReset;data={message:'ok',reauthenticate:false}}
    else if(/^\/api\/v1\/users\/[^/]+$/.test(url)&&opts.method==='PATCH'){const input=JSON.parse(opts.body);data={...state.users.find(u=>url.endsWith(u.id)),...input};state.users=state.users.map(u=>u.id===data.id?data:u)}
    else throw Error(`Unexpected endpoint ${url}`);
    return response(200,data);
  }
  const context={document:{getElementById:id=>{assert.ok(nodes[id],`missing HTML id ${id}`);return nodes[id]},createElement:tag=>new Node(tag),hidden:false},window:{addEventListener:(name,fn)=>{(windows[name]||=[]).push(fn)}},fetch:fetchMock,Intl,Date,Map,Set,Error,Number,String,Math,Array,Object,URLSearchParams,AbortController,JSON,console,Uint8Array,
    crypto:{getRandomValues:bytes=>webcrypto.getRandomValues(bytes)}, // deliberately no randomUUID, like HTTP LAN
    URL:{createObjectURL(){return'blob:photo'},revokeObjectURL(){}},setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id},clearTimeout:id=>timers.delete(id),setInterval(fn,ms){intervals.push({fn,ms});return intervals.length}};
  vm.runInNewContext(source,context,{filename:'app.js'});
  async function poll(){const item=[...timers.entries()].find(([,t])=>t.ms===3000);assert.ok(item,'detail polling timer');timers.delete(item[0]);await item[1].fn();await settle()}
  return{nodes,state,calls,timers,intervals,windows,poll,context};
}
(async()=>{
 const app=setup(),{nodes:n,state:s,calls}=app;await settle();
 assert.equal(n.dashboard.hidden,false);assert.equal(n['stat-count'].textContent,'42','global count, not one page');
 assert.equal(n['stat-review'].textContent,'4');assert.equal(n['stat-devices'].textContent,'3');assert.match(n['stat-failed'].textContent,/1 foto/);
 assert.ok(calls.every(c=>c.opts.credentials==='same-origin'&&!c.opts.headers.Authorization));
 n['capture-state'].value='review';n['capture-plant'].value='acelga-01';await n['capture-filters'].emit('submit');await settle();
 assert.ok(calls.some(c=>c.url.includes('analysis_state=review')&&c.url.includes('plant_id=acelga-01')));
 // A filter changed while images/metadata are still loading must be applied next.
 let release;s.holdCaptures=new Promise(resolve=>{release=resolve});const refresh=n.refresh.emit('click');await settle();
 n['capture-state'].value='positive';await n['capture-filters'].emit('submit');release();await refresh;await settle();
 assert.match(calls.filter(c=>c.url.startsWith('/api/v1/captures?')).at(-1).url,/analysis_state=positive/);
 await n.gallery.children[0].emit('click');await settle();
 assert.match(n['analysis-message'].textContent,/Aguardando análise automática/);assert.equal(n['analyze-button'].hidden,true);
 s.analyses=[review];s.job='completed';await app.poll();
 assert.match(n['analysis-message'].textContent,/não identificada/);assert.doesNotMatch(n['analysis-message'].textContent,/Praga detectada/);
 const image=n['detail-image'];image.complete=true;image.naturalWidth=640;image.naturalHeight=360;await image.emit('load');
 assert.ok(n['analysis-canvas'].draws.some(d=>d.kind==='box'&&d.color==='#ffc452'));
 assert.equal(n['reanalyze-button'].hidden,false);await n['reanalyze-button'].emit('click');await settle();
 assert.ok(calls.some(c=>c.url.endsWith('/reanalyze')&&c.opts.method==='POST'));assert.match(n['analysis-progress'].textContent,/nova análise/);
 s.analyses=[positive,review];s.job='completed';await app.poll();
 assert.match(n['analysis-message'].textContent,/Praga detectada/,'new result selected automatically');
 assert.ok(n['analysis-canvas'].draws.some(d=>d.kind==='box'&&d.color==='#b8ee69'));
 n['analysis-history'].value='1';await n['analysis-history'].emit('change');await app.poll();
 assert.match(n['analysis-message'].textContent,/não identificada/,'explicit historical choice survives polling');
 n['show-boxes'].checked=false;await n['show-boxes'].emit('change');assert.equal(n['detail-image'].hidden,false,'original photo remains available');
 n['image-dialog'].close();await n['nav-positive'].emit('click');await settle();
 assert.match(text(n['positive-body']),/Planta não informada/);assert.match(text(n['positive-body']),/Praga detectada/);
 n['filter-from'].value='2026-10-01';await n['positive-filters'].emit('submit');await settle();
 assert.ok(calls.some(c=>c.url.includes('/plants/positive-history?')&&c.url.includes('from_date=2026-10-01')));
 await n['positive-body'].children[0].children[3].children[0].emit('click');await settle();assert.match(n['analysis-message'].textContent,/Praga detectada/);
 n['image-dialog'].close();s.analyses=[{...positive,analysis_state:'legacy',unidentified_count:0,candidates:[]}];
 await n['nav-captures'].emit('click');await settle();await n.gallery.children[0].emit('click');await settle();
 assert.match(n['analysis-message'].textContent,/Praga detectada/);assert.match(n['analysis-meta'].textContent,/Versão anterior/);assert.match(text(n['analysis-detections']),/pulgao/);
 n['image-dialog'].close();s.analyses=[];s.job='failed';await n.gallery.children[0].emit('click');await settle();
 assert.equal(n['analyze-button'].hidden,false);assert.doesNotMatch(n['analysis-error'].textContent,/internal technical/);
 await n['analyze-button'].emit('click');await settle();assert.equal(s.job,'queued');n['image-dialog'].close();
 // Raw upload on plain LAN, UTF-8 headers and safe retry after ambiguous failure.
 await n['open-upload'].emit('click');n['upload-file'].files=[{name:'planta.jpg',type:'image/jpeg',size:5000}];n['upload-plant'].value='acelga-01';n['upload-name'].value='Acelga · São José';s.manualFailure=true;
 await n['upload-form'].emit('submit');await settle();assert.match(n['upload-error'].textContent,/tentar o mesmo envio/);
 await n['upload-form'].emit('submit');await settle();assert.equal(s.uploadRecords.length,2);
 const first=s.uploadRecords[0].headers,second=s.uploadRecords[1].headers;
 assert.match(first['X-Capture-Id'],/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
 assert.equal(first['X-Capture-Id'],second['X-Capture-Id'],'retry must be idempotent');assert.equal(first['X-Captured-At'],second['X-Captured-At']);
 assert.equal(decodeURIComponent(first['X-Plant-Name']),'Acelga · São José');assert.equal(first['X-Plant-Name-Encoding'],'percent-utf8');
 assert.equal(n['upload-dialog'].open,false);assert.equal(n['image-dialog'].open,true);assert.match(n['analysis-message'].textContent,/Aguardando análise automática/);
 assert.ok(!calls.some(c=>c.url.endsWith('/analyses')&&c.opts.method==='POST'));
 n['image-dialog'].close();await n['nav-devices'].emit('click');await settle();assert.match(text(n['devices-list']),/Raspberry 01/);assert.match(text(n['devices-list']),/Envio autorizado/);
 await n['nav-users'].emit('click');await settle();assert.match(text(n['users-body']),/Consulta <img>/,'untrusted text stays text');
 await n['create-user'].emit('click');n['user-name'].value='Nova conta';n['user-email'].value='nova@example.com';n['user-role'].value='viewer';n['user-password'].value='senha-longa-123';
 await n['user-form'].emit('submit');await settle();assert.equal(n['user-dialog'].open,false);assert.match(text(n['users-body']),/Nova conta/);assert.equal(n['user-password'].value,'');
 await n['users-body'].children[1].children[3].children[0].emit('click');n['user-active'].checked=false;await n['user-form'].emit('submit');await settle();
 assert.equal(s.users[1].is_active,false);assert.ok(calls.some(c=>c.opts.method==='PATCH'));
 await n['users-body'].children[1].children[3].children[1].emit('click');n['reset-password'].value='nova-senha-123';
 let releaseReset;s.holdReset=new Promise(resolve=>{releaseReset=resolve});const reset=n['reset-form'].emit('submit');await settle();
 const cancel=await n['reset-dialog'].emit('cancel');assert.equal(cancel.defaultPrevented,true,'cannot replace dialog during mutation');
 releaseReset();await reset;await settle();assert.equal(n['reset-password'].value,'');assert.match(n['users-notice'].textContent,/Senha redefinida/);
 // Session revalidation restores a safe view if permissions changed meanwhile.
 s.role='viewer';await app.windows.pageshow[0]({persisted:true});await settle();
 assert.equal(n['nav-users'].hidden,true);assert.equal(n['users-view'].hidden,true);assert.equal(n['users-body'].children.length,0);assert.equal(n['captures-view'].hidden,false);
 await n['nav-account'].emit('click');n['current-password'].value='senha-anterior-123';n['new-password'].value='nova-senha-123';n['confirm-password'].value='outra-senha-123';await n['password-form'].emit('submit');assert.match(n['password-error'].textContent,/não são iguais/);
 n['confirm-password'].value='nova-senha-123';await n['password-form'].emit('submit');await settle();assert.equal(n['login-view'].hidden,false);assert.match(n['login-error'].textContent,/Senha alterada/);assert.equal(n['current-password'].value,'');
 const viewer=setup('viewer');await settle();assert.equal(viewer.nodes['open-upload'].hidden,true);assert.equal(viewer.nodes['nav-users'].hidden,true);
 await viewer.nodes['nav-users'].emit('click');assert.ok(!viewer.calls.some(c=>c.url.startsWith('/api/v1/users?')));
 viewer.state.summaryFailure=true;await viewer.nodes.refresh.emit('click');await settle();assert.match(viewer.nodes['page-error'].textContent,/resumo não foi atualizado/);
 viewer.state.summaryFailure=false;await viewer.windows.online[0]();await settle();assert.equal(viewer.nodes['connection-status'].textContent,'Conectado');
 viewer.nodes['upload-file'].files=[{name:'private.jpg'}];await viewer.nodes.logout.emit('click');await settle();assert.equal(viewer.nodes['upload-file'].files.length,0,'logout clears selected local file');
 console.log('WEB V2 OK: global counts, filters/races, auto polling, review/positive/legacy overlays, historical choices, raw idempotent LAN upload, reanalysis/retry, users/passwords, role revalidation, reconnection and session cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1});
