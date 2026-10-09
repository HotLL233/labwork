'use strict';
document.getElementById('login-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const button=document.getElementById('login-submit'),error=document.getElementById('login-error');
  if(button.disabled)return;
  button.disabled=true;button.textContent='正在登录…';error.hidden=true;
  try{
    const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('password').value})});
    if(!response.ok){const data=await response.json();throw Error(data.error||'登录失败，请检查密码。');}
    location.replace('/');
  }catch(e){error.textContent=e.message||'连接失败，请稍后重试。';error.hidden=false;button.disabled=false;button.textContent='登录平台';}
});
