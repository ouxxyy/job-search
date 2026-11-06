// Read rendered UI only. Login links alone are not evidence of a logged-out session.
() => {
 const visible=e=>!!(e.getClientRects().length && getComputedStyle(e).visibility!=='hidden');
 const elements=selector=>[...document.querySelectorAll(selector)].filter(visible);
 const text=(document.body?.innerText||'').slice(0,20000);
 const controls=elements('a,button,[role="button"],[role="menuitem"]');
 const label=e=>(e.innerText||e.getAttribute('aria-label')||'').trim();
 const links=elements('a[href]');
 const href=e=>e.getAttribute('href')||'';
 const dialogs=elements('[role="dialog"], [class*="login-dialog"], [class*="login-modal"]');
 const dialogText=dialogs.map(e=>e.innerText||'').join(' ');
 const phone=elements('input').some(e=>/手机号|验证码|手机号码/.test(e.placeholder||''));
 const loginEntry=controls.find(e=>/^(登录|登录\/注册|登录注册|立即登录|注册\/登录)$/.test(label(e)));
 const logout=controls.some(e=>/^(退出|退出登录|安全退出|退出账号)$/.test(label(e)));
 const bossAccount=location.hostname.endsWith('zhipin.com') && links.some(e=>href(e).includes('/web/geek/recommend') && label(e) && !/登录|注册/.test(label(e))) && links.some(e=>href(e).includes('/web/geek/resume'));
 const userMenu=elements('[aria-label="打开用户菜单"]').length>0;
 const wall= /\/(?:login|passport\/login)(?:\/|$)/.test(location.pathname) || (phone && /登录|注册/.test(dialogText||text)) || /扫码登录|密码登录|验证码登录|登录后继续|请先登录/.test(dialogText);
 const blocked=/安全验证|访问过于频繁|操作过于频繁|完成验证后|滑动滑块|访问受限|异常访问/.test(text);
 const recruiter=location.hostname==='lpt.liepin.com' || location.hostname==='u.bendibao.com' || /\/job\/recruit\/?$/.test(location.pathname) || /\/web\/boss\//.test(location.pathname) || /^\/ent\//.test(location.pathname) || /选择招人合作意向/.test(text);
 const appOnly=/仅支持.*APP|请在APP内查看岗位|打开APP查看职位/.test(text);
 const cards=elements('.job-card-wrapper,.job-card-box,.joblist-box__item,.job-list-item,.joblist-item,.job-card').length;
 const blank=location.href==='about:blank'||location.hostname==='web.tabbit.ai'||!text.trim();
 const login=wall?'signed_out':(logout||bossAccount||userMenu)?'signed_in':'unknown';
 const publicSource=['bendibao.com','iguopin.com','job.mohrss.gov.cn','ncss.cn'].some(host=>location.hostname===host||location.hostname.endsWith('.'+host));
 const publicAccess=publicSource&&!wall&&!blocked&&!recruiter&&!appOnly&&links.some(e=>/招聘|职位|城市选择/.test(label(e)));
 const status=blank?'browser_error':blocked?'blocked':recruiter?'wrong_context':wall?'login_required':appOnly?'app_required':login==='signed_in'||cards>0||publicAccess?'ready':'needs_inspection';
 return {url:location.href,title:document.title,login,status,blocked,wall,cards,
  context:recruiter?'recruiter':'candidate_or_unknown',
  auth_evidence:wall?'visible_login_wall':bossAccount?'candidate_account_and_resume':logout?'logout_control':userMenu?'account_menu':'none',
  loginEntryVisible:!!loginEntry,loginLink:loginEntry?.href||'',
  empty:/暂无相关职位|没有找到相关职位|暂无搜索结果/.test(text),appOnly,public_access:publicAccess};
}
