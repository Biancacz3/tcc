/* Browser-independent contract tests: real app.js, simulated DOM and API.
 * Run: node tests/test_web_flow.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname,'../backend/app/web/index.html'),'utf8');
const source = fs.readFileSync(path.join(__dirname,'../backend/app/web/app.js'),'utf8');
class Node {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.hidden = false; this.disabled = false; this.value = ''; this.textContent = ''; this.complete = false; this.naturalWidth = 0; this.checked = false; this.open = false; this.classList = {toggle(){}}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(k,v) { this.attrs[k]=v; }
  removeAttribute(k) { delete this.attrs[k]; if (k==='src') this.src=''; }
  addEventListener(k,fn) { (this.listeners[k] ||= []).push(fn); }
  focus() {}
  reset() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.emit('close'); }
  remove() {}
  async emit(name) { for (const fn of this.listeners[name] || []) await fn({preventDefault(){}}); }
  getContext() { return {drawImage(){},strokeRect(){},fillRect(){},fillText(){},measureText(){return {width:100}}}; }
}
const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Node()]));
assert.equal(Object.keys(ids).length,[...html.matchAll(/id="([^"]+)"/g)].length,'HTML IDs must be unique');
ids['dashboard'].hidden=true; ids['auto-refresh'].checked=true;
const timers = new Map(), intervals = []; let timerId=0, jobStatus='queued', analyses=[], loggedIn=true, historyRequests=[], historyTotal=1;
const calls=[], errors=[];
const capture={id:'capture-1',device_id:'camera-1',captured_at:'2026-10-26T03:34:28Z',received_at:'2026-10-26T03:34:28Z',width:640,height:360,size_bytes:5000,plant_id:'acelga-01',plant_name:'Acelga 01',marker_id:10,analysis_status:'queued',detection_count:null};
const saved={id:'analysis-1',capture_id:'capture-1',created_at:'2026-10-26T03:35:28Z',width:640,height:360,detection_count:1,model_name:'pulgao-v1',model_scope:'pulgao',detections:[{label:'pulgao',confidence:.75,xyxy:[10,10,20,20]}]};
const auth={user:{id:'user-1',email:'bianca@example.com',name:'Bianca',role:'admin'},csrf_token:'csrf-test',expires_at:'2026-11-02T03:00:00Z'};
async function fetchMock(url,opts) {
  calls.push({url,opts}); let data;
  if (url==='/api/v1/auth/me') { if (!loggedIn) return response(401,{}); data=auth; }
  else if(url==='/api/v1/auth/login') { loggedIn=true; data=auth; }
  else if(url==='/api/v1/auth/logout') { assert.equal(opts.headers['X-CSRF-Token'],'csrf-test'); loggedIn=false; return response(204,null); }
  else if(url.startsWith('/api/v1/captures?')) data=[capture];
  else if(url.startsWith('/api/v1/devices?')) data=[{id:'camera-1',name:'Raspberry 01'}];
  else if(url==='/api/v1/captures/capture-1') data=capture;
  else if(url.endsWith('/image')) return {ok:true,status:200,headers:{get(){return 'image/jpeg'}},async blob(){return {image:true}}};
  else if(url.endsWith('/analysis-job/retry')) { assert.equal(opts.method,'POST'); assert.equal(opts.headers['X-CSRF-Token'],'csrf-test'); jobStatus='queued'; data={status:jobStatus,attempts:0}; }
  else if(url.endsWith('/analysis-job')) data={capture_id:'capture-1',status:jobStatus,attempts:0,last_error:jobStatus==='failed'?'Modelo indisponível':null};
  else if(url.endsWith('/analyses')) { assert.equal(opts.method,'GET','normal flow must never manually execute IA'); data=analyses; }
  else if(url.startsWith('/api/v1/plants/positive-history?')) {
    historyRequests.push(url);
    data={items:[{capture_id:'capture-1',analysis_id:'analysis-1',plant_id:null,plant_name:null,marker_id:null,device_id:'camera-1',device_name:'Raspberry 01',captured_at:capture.captured_at,analyzed_at:saved.created_at,detection_count:1}],total:historyTotal,limit:20,offset:0,date_timezone:'America/Sao_Paulo'};
  } else throw new Error(`Unexpected endpoint ${url}`);
  return response(200,data);
}
function response(status,data) { return {ok:status>=200&&status<300,status,headers:{get(){return 'application/json'}},async json(){return data}}; }
const context={document:{getElementById:id=>{assert.ok(ids[id],`Missing HTML id ${id}`);return ids[id]},createElement:tag=>new Node(tag),hidden:false},window:{addEventListener(){}},fetch:fetchMock,Intl,Date,Map,Set,Error,Number,String,Math,Array,Object,URLSearchParams,AbortController,JSON,console,
 URL:{createObjectURL(){return 'blob:photo'},revokeObjectURL(){}},setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id},clearTimeout(id){timers.delete(id)},setInterval(fn,ms){intervals.push({fn,ms});return intervals.length}};
vm.runInNewContext(source,context,{filename:'app.js'});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function settle(){for(let i=0;i<5;i++)await flush()}
function text(node){return [node.textContent,...node.children.map(text)].join(' ')}
(async()=>{
 await settle();
 assert.equal(ids['dashboard'].hidden,false,'existing cookie session should restore dashboard');
 assert.match(text(ids.gallery),/Acelga 01/);
 assert.ok(calls.every(c=>c.opts.credentials==='same-origin'));
 assert.ok(calls.every(c=>!c.opts.headers.Authorization),'no administrative token should be stored or sent by browser');
 await ids.gallery.children[0].emit('click'); await settle();
 assert.match(ids['analysis-message'].textContent,/Aguardando análise automática/);
 assert.equal(ids['analyze-button'].hidden,true,'queued uploads do not require a button');
 analyses=[saved];jobStatus='completed';
 const poll=[...timers.entries()].find(([,x])=>x.ms===3000); assert.ok(poll,'must poll pending analysis');
 timers.delete(poll[0]); await poll[1].fn();await settle();
 assert.match(ids['analysis-message'].textContent,/Praga detectada/);
 assert.match(text(ids['analysis-detections']),/pulgao/,'preserve original trained class for review');
 assert.equal(ids['analyze-button'].hidden,true);
 ids['image-dialog'].close();await settle();
 await ids['nav-positive'].emit('click');await settle();
 assert.equal(ids['positive-view'].hidden,false);
 assert.match(text(ids['positive-body']),/Planta não informada/,'legacy images must not acquire fabricated plant identities');
 assert.match(text(ids['positive-body']),/Praga detectada/);
 assert.match(html,/horário de São Paulo/,'date filter label must match API timezone');
 const previousCount=historyRequests.length; historyTotal=2;
 const refresh=intervals.find(item=>item.ms===30000); assert.ok(refresh);
 await refresh.fn(); await settle();
 assert.equal(historyRequests.length,previousCount+1,'positive history refreshes without a button');
 assert.match(ids['positive-summary'].textContent,/2 registro/);
 ids['filter-plant'].value='acelga-01';ids['filter-from'].value='2026-10-01';ids['filter-to'].value='2026-10-26';
 await ids['positive-filters'].emit('submit');await settle();
 assert.match(historyRequests.at(-1),/plant_id=acelga-01/);assert.match(historyRequests.at(-1),/from_date=2026-10-01/);
 const open=ids['positive-body'].children[0].children[3].children[0];await open.emit('click');await settle();
 assert.equal(ids['image-dialog'].open,true,'history opens original photo and matching analysis');
 assert.match(ids['analysis-message'].textContent,/Praga detectada/);
 ids['image-dialog'].close();analyses=[];jobStatus='failed';
 await ids['nav-captures'].emit('click');await settle();await ids.gallery.children[0].emit('click');await settle();
 assert.equal(ids['analyze-button'].hidden,false,'failed analysis offers explicit retry');
 await ids['analyze-button'].emit('click');await settle();assert.equal(jobStatus,'queued');
 assert.equal(ids['analyze-button'].hidden,true);
 ids['image-dialog'].close(); await ids.logout.emit('click');await settle();
 assert.equal(ids['login-view'].hidden,false); assert.equal(loggedIn,false);
 assert.equal(errors.length,0);
 console.log('WEB FLOW OK: persisted login, CSRF, automatic polling, original model labels, history/filter/photo, explicit retry and logout.');
})().catch(error=>{console.error(error);process.exitCode=1});
