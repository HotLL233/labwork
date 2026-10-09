const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
async function check(ok){
  let handler,request,redirect;
  const elements={'login-form':{addEventListener:(_,fn)=>handler=fn},'login-submit':{disabled:false},'login-error':{},password:{value:'test-password'}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/login.js','utf8'),{
    document:{getElementById:id=>elements[id]},
    fetch:async(path,options)=>{request={path,...options};return{ok,json:async()=>({error:'密码错误'})};},
    location:{replace:path=>redirect=path}
  });
  await handler({preventDefault(){}});
  assert.equal(request.path,'/api/login');
  assert.equal(JSON.parse(request.body).password,'test-password');
  if(ok){assert.equal(redirect,'/');assert.equal(elements['login-submit'].disabled,true);}
  else{assert.equal(redirect,undefined);assert.equal(elements['login-error'].textContent,'密码错误');assert.equal(elements['login-submit'].disabled,false);}
}
(async()=>{await check(true);await check(false);console.log('login success/error passed');})().catch(error=>{console.error(error);process.exitCode=1;});
