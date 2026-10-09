const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const elements=new Map(),requests=[];
function element(id){if(!elements.has(id))elements.set(id,{value:'',files:[],hidden:false,disabled:false,innerHTML:'',textContent:'',classList:{toggle(){},remove(){}},listeners:{},addEventListener(type,handler){this.listeners[type]=handler;},querySelectorAll(){return[];},querySelector(){return null;},reset(){}});return elements.get(id);}
const project={code:'LAB',name:'自定义项目',sheets:[{key:'s0',name:'原始表'},{key:'s1',name:'水分',method_key:'water'}],methods:[{key:'water',name:'水分',fields:[],params:{},columns:{}}],rules_confirmed:false};
let active='2026-10',writable=true,missingOnce=false;
const context=vm.createContext({
  console,Date,sessionStorage:{getItem(){return '2026-10';},setItem(){},removeItem(){}},
  document:{getElementById:element,querySelector(){return{content:'test-token'};},querySelectorAll(){return[];}},
  location:{assign(){throw Error('unexpected redirect');}},
  fetch:async(path,options)=>{requests.push({path,...options});let data;
    if(path==='/api/catalog'&&missingOnce){missingOnce=false;return{ok:false,status:400,json:async()=>({error:'月份不存在，请刷新目录'})};}
    if(path==='/api/catalog')data={projects:[project],active_month:active,months:['2026-09','2026-10'],current_month:'2026-10',writable,monthly_rules:{},version:'test',port:18807};
    else if(path==='/api/month'){active=JSON.parse(options.body).month;writable=active>='2026-10';data={month:active};}
    else data={view:{name:'原始表',columns:[],rows:[],merges:[],styles:[],sheet_names:[],last_row:3},next_row:4};
    return{ok:true,status:200,json:async()=>data};
  }
});
vm.runInContext(fs.readFileSync(__dirname+'/app.js','utf8'),context);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  assert(requests.some(request=>request.path==='/api/book-sheet/LAB/s0?start=1'));
  assert.equal(element('browse-note').hidden,false);
  assert(!element('methods').innerHTML.includes('待接入'));
  element('workbook-file').files=[{name:'LAB_2026.xlsx',size:100}];element('workbook-file').listeners.change();
  assert.equal(element('project-code').value,'LAB_2026');assert.equal(element('project-name').value,'LAB_2026');
  element('project-code').value='';element('project-name').value='';element('workbook-file').files=[{name:'理化原始表.xlsx',size:100}];element('workbook-file').listeners.change();
  assert.equal(element('project-code').value,'');assert.equal(element('project-name').value,'理化原始表');
  element('project-name').value='自定义名称';element('workbook-file').listeners.change();assert.equal(element('project-name').value,'自定义名称');
  element('project-code').value='NEW';element('project-name').value='新项目';element('workbook-file').files=[{name:'new.xlsx',size:100}];
  vm.runInContext('workbookStatus()',context);
  assert.equal(element('import-workbook').disabled,false);
  element('project-code').value='LAB';vm.runInContext('workbookStatus()',context);
  assert.equal(element('import-workbook').disabled,true);
  await vm.runInContext("selectSheet('s1')",context);
  assert(requests.some(request=>request.path==='/api/sheet/LAB/water'));
  assert.equal(element('browse-note').hidden,true);
  await vm.runInContext("changeMonth('2026-09')",context);
  const catalogRequest=requests.filter(request=>request.path==='/api/catalog').at(-1);
  assert.equal(catalogRequest.headers['X-Labwork-Month'],'2026-09');
  assert.equal(element('browse-note').hidden,false);
  assert.equal(element('import-workbook').disabled,true);
  assert.equal(element('download-workbook').href,'/api/workbook/LAB/download?month=2026-09');
  assert.equal(element('download-workbook').hidden,false);
  assert.equal(requests.at(-1).path,'/api/book-sheet/LAB/s1?start=1');
  assert.equal(requests.at(-1).headers['X-Labwork-Month'],'2026-09');
  await vm.runInContext("changeMonth('2099-11')",context);
  assert.equal(vm.runInContext('rows[0].date',context),'2099-11-01');
  assert.equal(vm.runInContext('previewId',context),null);
  assert.equal(element('browse-note').hidden,true);
  missingOnce=true;await vm.runInContext('loadCatalog()',context);
  assert.equal(requests.filter(request=>request.path==='/api/catalog').at(-1).headers['X-Labwork-Month'],undefined);
  await vm.runInContext('selectProject(undefined)',context);
  assert.equal(element('import-panel').open,true);
  console.log('dynamic sheets, calculation gating, per-browser month and readonly passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
