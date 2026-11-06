const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const source=fs.readFileSync('assets/page-state.js','utf8');
function el(text='',attrs={}) {return {innerText:text,placeholder:attrs.placeholder||'',href:attrs.href||'',getAttribute:k=>attrs[k]||null,getClientRects:()=>[{}]};}
function state({url='https://www.zhipin.com/web/geek/jobs',text='岗位搜索页面',controls=[],inputs=[],dialogs=[],menu=[],cards=[]}={}) {
 const document={title:'测试页面',body:{innerText:text},querySelectorAll:s=>s==='a,button,[role="button"],[role="menuitem"]'?controls:s==='a[href]'?controls.filter(e=>e.href):s==='input'?inputs:s==='[aria-label="打开用户菜单"]'?menu:s.startsWith('[role="dialog"]')?dialogs:s.startsWith('.job-card-wrapper')?cards:[]};
 const location=new URL(url);
 const fn=new Function('document','location','getComputedStyle','return ('+source+');')(document,location,()=>({visibility:'visible'}));
 return fn();
}
test('visible candidate account and resume establish signed in without logout label',()=>{
 const s=state({controls:[el('测试账号',{href:'/web/geek/recommend'}),el('简历',{href:'/web/geek/resume'})]});
 assert.equal(s.login,'signed_in');assert.equal(s.status,'ready');assert.equal(s.auth_evidence,'candidate_account_and_resume');
});
test('login link alone never requests relogin',()=>{
 const s=state({controls:[el('登录/注册',{href:'/login'})]});assert.equal(s.login,'unknown');assert.equal(s.status,'needs_inspection');
});
test('actual login dialog requires user login',()=>{
 const s=state({dialogs:[el('验证码登录')],inputs:[el('',{placeholder:'手机号'})]});assert.equal(s.status,'login_required');assert.equal(s.wall,true);
});
test('employer authentication is wrong context, not user logged out',()=>{
 const s=state({url:'https://lpt.liepin.com/auth/flow/process',text:'选择招人合作意向',controls:[el('退出')]});
 assert.equal(s.login,'signed_in');assert.equal(s.status,'wrong_context');
});
test('blank page means browser error not login required',()=>{
 const s=state({url:'about:blank',text:''});assert.equal(s.status,'browser_error');assert.equal(s.login,'unknown');
});
test('maimai account menu is positive auth evidence',()=>{
 const s=state({url:'https://maimai.cn/community/home/recommended',menu:[el('',{'aria-label':'打开用户菜单'})]});assert.equal(s.login,'signed_in');assert.equal(s.appOnly,false);
});
test('generic download app promotion does not prove app-only jobs',()=>{
 const s=state({text:'下载APP查看更多内容'});assert.equal(s.appOnly,false);
});
test('captcha visible requires verification even with logged in account',()=>{
 const s=state({text:'请完成安全验证',controls:[el('退出登录')]});assert.equal(s.status,'blocked');
});
test('public recruitment navigation is readable without claiming signed in',()=>{
 for(const url of ['https://gz.bendibao.com/job/','https://www.iguopin.com/','http://job.mohrss.gov.cn/','https://www.ncss.cn/']){
  const s=state({url,controls:[el('职位查询',{href:'/jobs/'})]});
  assert.equal(s.status,'ready');assert.equal(s.login,'unknown');assert.equal(s.public_access,true);
 }
});
test('public portals still stop at a real login wall or captcha',()=>{
 const controls=[el('职位查询',{href:'/jobs/'})];
 assert.equal(state({url:'https://gz.bendibao.com/job/',controls,dialogs:[el('验证码登录')],inputs:[el('',{placeholder:'手机号'})]}).status,'login_required');
 assert.equal(state({url:'https://www.iguopin.com/',controls,text:'请完成安全验证'}).status,'blocked');
});
test('Bendibao recruiter form is not a candidate search page',()=>{
 const s=state({url:'https://m.hz.bendibao.com/job/recruit',text:'快速免费招聘通道',controls:[el('职位',{href:'/job/'})]});
 assert.equal(s.status,'wrong_context');assert.equal(s.public_access,false);
});
test('lookalike source domains do not get public portal readiness',()=>{
 const s=state({url:'https://bendibao.com.evil.invalid/',controls:[el('职位',{href:'/job/'})]});
 assert.equal(s.status,'needs_inspection');assert.equal(s.public_access,false);
});
