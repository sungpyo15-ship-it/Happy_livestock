// Firebase 배포판 빌드: app/index.html → dist/ (로그인 껍데기 + 암호화된 앱 코드)
// 사용법: node deploy/build_fb.mjs [관리자아이디]
// - 앱 코드(점검 기준·해설 포함)는 AES-GCM으로 암호화되어, 관리자가 승인한 사람만 복호화 키를 받습니다.
// - 키는 deploy/.content_key 에 보관 (git에 올리지 않음). 관리자는 처음 로그인 때 이 키를 한 번 입력합니다.
import fs from "node:fs";
import path from "node:path";
import { webcrypto as crypto } from "node:crypto";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const CFG = JSON.parse(fs.readFileSync(path.join(ROOT, "deploy/firebase.config.json"), "utf8"));
const ADMIN_ID = (process.argv[2] || CFG.adminId || "admin").toLowerCase();
const DOMAIN = CFG.loginDomain || "farmcheck.team";
const SDK = "10.12.2";

const src = fs.readFileSync(path.join(ROOT, "app/index.html"), "utf8");
const i = src.indexOf("<script>\n/* ====");
const j = src.lastIndexOf("</script>");
if (i < 0 || j < i) throw new Error("앱 스크립트를 찾지 못했습니다");
const appCode = src.slice(i + "<script>".length, j);

// 키 준비
const keyFile = path.join(ROOT, "deploy/.content_key");
let keyB64 = fs.existsSync(keyFile) ? fs.readFileSync(keyFile, "utf8").trim() : "";
if (!keyB64) { keyB64 = Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64"); fs.writeFileSync(keyFile, keyB64 + "\n"); }
const key = await crypto.subtle.importKey("raw", Buffer.from(keyB64, "base64"), "AES-GCM", false, ["encrypt"]);
const iv = crypto.getRandomValues(new Uint8Array(12));
const enc = Buffer.from(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(appCode))).toString("base64");

const fbCfg = { apiKey: CFG.apiKey, authDomain: CFG.authDomain, projectId: CFG.projectId, storageBucket: CFG.storageBucket, messagingSenderId: CFG.messagingSenderId, appId: CFG.appId };
const shell = `
<script src="https://www.gstatic.com/firebasejs/${SDK}/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/${SDK}/firebase-auth-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/${SDK}/firebase-firestore-compat.js"></script>
<script>
(function(){
const CFG=${JSON.stringify(fbCfg)}, ADMIN_ID=${JSON.stringify(ADMIN_ID)}, DOMAIN=${JSON.stringify(DOMAIN)};
const ENC="${enc}", IV="${Buffer.from(iv).toString("base64")}";
const $=s=>document.querySelector(s), main=$("#main");
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
document.body.classList.add("gated"); $("#hdrScore").textContent="";
const box=(t,b,x="")=>{main.innerHTML='<div class="gate"><div class="gate-icon">'+t[0]+'</div><h2>'+t+'</h2><p>'+b+'</p>'+x+'</div>'};
const card=h=>'<div class="card stack" style="text-align:left;max-width:420px;margin:0 auto">'+h+'</div>';
const field=(id,l,type,ph)=>'<label class="f">'+l+'<input id="'+id+'" type="'+(type||"text")+'" autocomplete="'+(type==="password"?"current-password":type==="email"?"email":"username")+'" autocapitalize="off" placeholder="'+(ph||"")+'"></label>';
const msgEl='<div class="small" id="smsg" style="color:var(--bad)"></div>';
const setMsg=(t,ok)=>{const m=$("#smsg");if(m){m.textContent=t;m.style.color=ok?"var(--good)":"var(--bad)"}};
if(!window.firebase){box("연결할 수 없습니다","인터넷 연결을 확인하고 새로고침해 주세요.");return}
firebase.initializeApp(CFG);
const auth=firebase.auth(), db=firebase.firestore();
try{auth.setPersistence(firebase.auth.Auth.Persistence.LOCAL)}catch(e){}
try{auth.languageCode="ko"}catch(e){}
const fake=id=>id+"@"+DOMAIN, isFake=e=>String(e||"").endsWith("@"+DOMAIN), idOk=id=>/^[a-z0-9_]{3,20}$/.test(id), mailOk=m=>/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(m);
const mask=m=>{const [a,d]=String(m).split("@");return (a.length<=2?a[0]+"*":a.slice(0,2)+"*".repeat(Math.min(6,a.length-2)))+"@"+d};
let view="login", creating=false, unsub=null;
const ERR={"auth/invalid-credential":"아이디 또는 비밀번호가 맞지 않습니다","auth/wrong-password":"아이디 또는 비밀번호가 맞지 않습니다","auth/user-not-found":"아이디 또는 비밀번호가 맞지 않습니다","auth/invalid-login-credentials":"아이디 또는 비밀번호가 맞지 않습니다",
 "auth/email-already-in-use":"이미 가입된 이메일입니다","auth/invalid-email":"이메일 형식을 확인해 주세요","auth/weak-password":"비밀번호는 6자 이상이어야 합니다","auth/too-many-requests":"시도가 많아 잠시 막혔습니다. 잠시 후 다시 해 주세요","auth/network-request-failed":"인터넷 연결을 확인해 주세요"};
async function idDoc(id){try{const d=await db.doc("ids/"+id).get();return d.exists?d.data():null}catch(e){return null}}
function loginView(){box("축우 현장점검","관리자가 승인한 팀원만 사용할 수 있습니다.",card(field("s_id","아이디","text","영문 소문자·숫자")+field("s_pw","비밀번호","password")+'<button class="primary" data-s="login">로그인</button>'+msgEl+'<button class="ghost" data-s="toForgot">비밀번호를 잊으셨나요?</button><button class="ghost" data-s="toSignup">처음이신가요? 가입 신청</button>'))}
function signupView(){box("가입 신청","가입 후 관리자가 승인하면 사용할 수 있습니다.",card(field("s_id","아이디","text","영문 소문자·숫자 3~20자")+field("s_pw","비밀번호","password","6자 이상")+field("s_pw2","비밀번호 확인","password")+field("s_name","이름","text","예: 홍길동")+field("s_mail","이메일 (비밀번호 찾기용)","email","예: name@gmail.com")+field("s_note","소속 · 연락처","text","예: 전북 지역 / 010-0000-0000")+'<button class="primary" data-s="signup">가입 신청</button>'+msgEl+'<button class="ghost" data-s="toLogin">이미 아이디가 있어요</button>'))}
function forgotView(){box("비밀번호 찾기","아이디를 넣으면 가입할 때 등록한 이메일로 비밀번호 재설정 링크를 보냅니다.",card(field("s_id","아이디","text","영문 소문자·숫자")+'<button class="primary" data-s="forgot">재설정 메일 보내기</button>'+msgEl+'<button class="ghost" data-s="toLogin">로그인으로 돌아가기</button>'))}
const logoutBtn='<div class="row" style="justify-content:center"><button data-s="logout">로그아웃</button></div>';
main.addEventListener("click",async e=>{const b=e.target.closest("[data-s]");if(!b)return;const a=b.dataset.s;
  if(a==="toSignup"){view="signup";return signupView()} if(a==="toLogin"){view="login";return loginView()} if(a==="toForgot"){view="forgot";return forgotView()}
  if(a==="logout"){if(unsub)unsub();view="login";return auth.signOut()}
  if(a==="login"){let id=($("#s_id").value||"").trim().toLowerCase();const pw=$("#s_pw").value||"";
    let mail=id.includes("@")?id:null; if(!mail&&!idOk(id))return setMsg("아이디를 확인해 주세요");b.disabled=true;
    try{if(mail)await auth.signInWithEmailAndPassword(mail,pw);
      else{const x=await idDoc(id);const tries=[...new Set([x&&x.email,x&&x.pending,fake(id)].filter(Boolean))];let last=null;
        for(const t of tries){try{await auth.signInWithEmailAndPassword(t,pw);last=null;break}catch(er){last=er;if(er.code==="auth/too-many-requests")break}} if(last)throw last}
    }catch(err){setMsg(ERR[err.code]||"로그인하지 못했습니다 ("+(err.code||"오류")+")");b.disabled=false}return}
  if(a==="forgot"){const id=($("#s_id").value||"").trim().toLowerCase();if(!idOk(id))return setMsg("아이디를 확인해 주세요");b.disabled=true;
    const x=await idDoc(id);
    const to=x&&(x.email||x.pending);
    if(!to){b.disabled=false;return setMsg("이 아이디에는 비밀번호 찾기용 이메일이 등록되어 있지 않습니다. 관리자에게 문의해 주세요")}
    try{await auth.sendPasswordResetEmail(to);setMsg(mask(to)+" 로 재설정 메일을 보냈습니다. 메일이 안 보이면 스팸함도 확인해 주세요",true)}catch(err){setMsg(ERR[err.code]||"메일을 보내지 못했습니다 ("+(err.code||"오류")+")")}b.disabled=false;return}
  if(a==="signup"){const id=($("#s_id").value||"").trim().toLowerCase(),pw=$("#s_pw").value||"",pw2=$("#s_pw2").value||"",name=($("#s_name").value||"").trim(),mail=($("#s_mail").value||"").trim().toLowerCase(),note=($("#s_note").value||"").trim();
    if(!idOk(id))return setMsg("아이디는 영문 소문자·숫자·밑줄 3~20자입니다"); if(id===ADMIN_ID)return setMsg("사용할 수 없는 아이디입니다");
    if(pw.length<6)return setMsg("비밀번호는 6자 이상이어야 합니다"); if(pw!==pw2)return setMsg("비밀번호 확인이 맞지 않습니다"); if(!name)return setMsg("이름을 넣어 주세요"); if(!mailOk(mail))return setMsg("비밀번호 찾기에 쓸 이메일을 넣어 주세요");
    b.disabled=true;
    if(await idDoc(id)){b.disabled=false;return setMsg("이미 사용 중인 아이디입니다")}
    creating=true;let c=null;
    try{c=await auth.createUserWithEmailAndPassword(mail,pw);
      try{await db.doc("ids/"+id).set({uid:c.user.uid,email:mail})}catch(err){try{await c.user.delete()}catch(_){}throw {code:"id-taken"}}
      await db.doc("users/"+c.user.uid).set({id,name:name.slice(0,40),note:note.slice(0,120),status:"pending",createdAt:Date.now()}); creating=false; route(c.user)}
    catch(err){creating=false;setMsg(err.code==="id-taken"?"이미 사용 중인 아이디입니다":(ERR[err.code]||"가입하지 못했습니다 ("+(err.code||"오류")+")"));b.disabled=false}return}
  if(a==="key"){const k=cleanKey($("#s_key").value);if(k.length!==44){setMsg("키는 44자입니다. 지금 "+k.length+"자가 들어왔습니다. 파일 안의 한 줄을 끝의 = 까지 빠짐없이 복사해 주세요");return}b.disabled=true;const code=await decrypt(k).catch(()=>null);if(!code){setMsg("키가 맞지 않습니다");b.disabled=false;return}
    try{await db.doc("secrets/content").set({k,at:Date.now()})}catch(err){setMsg("키를 저장하지 못했습니다. 보안 규칙을 확인해 주세요 ("+(err.code||"")+")");b.disabled=false;return}
    run(code,pendingInfo)}
});
main.addEventListener("input",e=>{if(e.target.id==="s_key"){const k=$("#klen");if(k)k.textContent=cleanKey(e.target.value).length+"자 / 44자"}});
main.addEventListener("keydown",e=>{if(e.key==="Enter"&&e.target.tagName==="INPUT"){const b=main.querySelector(".primary[data-s]");if(b)b.click()}});
let pendingInfo=null;
async function syncId(id,u){/* 아이디 → 이메일 연결 정보 유지 (예전 계정 포함). 실패하면 오류 코드를 돌려줌 */
  try{const x=await idDoc(id), real=isFake(u.email)?null:u.email;
    if(!x)await db.doc("ids/"+id).set({uid:u.uid,email:real});
    else if(x.uid===u.uid&&(x.email||null)!==real)await db.doc("ids/"+id).set({uid:u.uid,email:real});
    return null}catch(e){return e&&e.code||"error"}
}
async function route(u){
  if(!u){view==="signup"?signupView():view==="forgot"?forgotView():loginView();return}
  box("권한 확인 중","잠시만 기다려 주세요.");
  let adm=await idDoc(ADMIN_ID);
  if(!adm&&u.email===fake(ADMIN_ID)){const er=await syncId(ADMIN_ID,u);adm=await idDoc(ADMIN_ID);
    if(!adm)return box("관리자 연결을 만들지 못했습니다","Firebase 콘솔 → Firestore Database → 규칙 탭에 <b>match /ids/{id}</b> 부분이 있는 최신 규칙이 게시되어 있는지 확인해 주세요. 게시 후 1~2분 뒤 새로고침하면 됩니다. (오류: "+esc(er||"없음")+")",logoutBtn)}
  if(adm&&adm.uid!==u.uid&&u.email===fake(ADMIN_ID))return box("관리자 연결이 다른 계정에 있습니다","관리자 계정을 지웠다가 다시 만든 경우입니다. Firebase 콘솔 → Firestore Database → 데이터에서 <b>ids</b> 안의 <b>admin</b> 문서를 삭제한 뒤 새로고침해 주세요.",logoutBtn);
  if(adm&&adm.uid===u.uid){syncId(ADMIN_ID,u);return loadKey({uid:u.uid,id:ADMIN_ID,name:"관리자",isAdmin:true,email:isFake(u.email)?null:u.email})}
  if(unsub)unsub();
  unsub=db.doc("users/"+u.uid).onSnapshot(d=>{
    if(!d.exists){if(creating)return;return box("가입 정보가 없습니다","가입 신청을 다시 해 주세요. 계속 안 되면 관리자에게 문의하세요.",logoutBtn)}
    const x=d.data(), id=x.id||(u.email||"").split("@")[0]; syncId(id,u);
    if(x.status==="approved"){unsub();unsub=null;return loadKey({uid:u.uid,id,name:x.name||id,isAdmin:false,email:isFake(u.email)?null:u.email})}
    if(x.status==="blocked")return box("사용이 중지되었습니다","관리자가 이 아이디의 사용을 중지했습니다. 관리자에게 문의하세요.",logoutBtn);
    if(x.status==="rejected")return box("가입이 승인되지 않았습니다","관리자에게 문의하세요.",logoutBtn);
    box("승인 대기 중",esc(x.name||id)+"님("+esc(id)+")의 가입 신청을 보냈습니다. 관리자가 승인하면 이 화면이 자동으로 열립니다.",logoutBtn);
  },err=>box("권한을 확인하지 못했습니다","잠시 후 새로고침해 주세요. ("+esc(err.code||"")+")",logoutBtn));
}
auth.onAuthStateChanged(u=>{if(creating)return;route(u)});
const cleanKey=v=>String(v||"").replace(/[\\s"'\\x60“”‘’]/g,"").replace(/-/g,"+").replace(/_/g,"/");
const b64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function decrypt(k){const key=await crypto.subtle.importKey("raw",b64(k),"AES-GCM",false,["decrypt"]);const pt=await crypto.subtle.decrypt({name:"AES-GCM",iv:b64(IV)},key,b64(ENC));return new TextDecoder().decode(pt)}
async function loadKey(info){
  box("앱을 여는 중","잠시만 기다려 주세요.");pendingInfo=info;
  let k=null;try{const d=await db.doc("secrets/content").get();if(d.exists)k=d.data().k}catch(e){}
  if(k){const code=await decrypt(k).catch(()=>null);if(code)return run(code,info)}
  if(info.isAdmin)return box("앱 키 등록","처음 한 번만 관리자가 앱 키를 등록합니다. 받은 앱 키 파일(farmcheck_app_key.txt) 안의 한 줄(44자)을 붙여넣으세요.",card('<label class="f">앱 키<textarea id="s_key" rows="2" autocomplete="off" autocapitalize="off" spellcheck="false" style="font-family:var(--mono);min-height:60px" placeholder="예: Ab3+xY...="></textarea></label><div class="small muted" id="klen">0자 / 44자</div>'+'<button class="primary" data-s="key">등록하고 시작</button>'+msgEl)+logoutBtn);
  box("앱 준비 중","관리자가 아직 앱 설정을 마치지 않았습니다. 잠시 후 다시 열어 주세요.",logoutBtn);
}
/* 앱(복호화된 코드)에 넘기는 계정 기능: 비밀번호 변경, 비밀번호 찾기용 이메일 등록 */
const account={
  email:()=>{const u=auth.currentUser;return u&&!isFake(u.email)?u.email:null},
  async reauth(pw){const u=auth.currentUser;await u.reauthenticateWithCredential(firebase.auth.EmailAuthProvider.credential(u.email,pw))},
  async changePassword(cur,np){await account.reauth(cur);await auth.currentUser.updatePassword(np)},
  async setEmail(cur,mail,id){await account.reauth(cur);await auth.currentUser.verifyBeforeUpdateEmail(mail);
    const u=auth.currentUser;try{await db.doc("ids/"+id).set({uid:u.uid,email:isFake(u.email)?null:u.email,pending:mail})}catch(e){}}
};
function run(code,info){
  window.FB={uid:info.uid,id:info.id,name:info.name,isAdmin:info.isAdmin,db,account,logout:()=>auth.signOut().then(()=>location.reload())};
  document.body.classList.remove("gated");main.innerHTML="";
  const s=document.createElement("script");s.textContent=code;document.body.appendChild(s);
}
})();
</script>`;

let out = src.slice(0, i) + shell.trim() + src.slice(j + "</script>".length);
out = out.replace(/<title>[^<]*<\/title>/, "<title>축우 현장점검</title>").replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "");
out = out.replace("<head>", '<head>\n<meta name="robots" content="noindex,nofollow">');
if (/교재|파워북|CCCS 착유우|WHY=/.test(out.replace(/<style>[\s\S]*?<\/style>/, ""))) throw new Error("평문 콘텐츠가 남아 있습니다");

const dist = path.join(ROOT, "dist");
fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(dist);
fs.writeFileSync(path.join(dist, "index.html"), out);
for (const f of ["manifest.webmanifest", "icon.svg", "robots.txt"]) fs.copyFileSync(path.join(ROOT, "app", f), path.join(dist, f));
fs.writeFileSync(path.join(dist, "sw.js"), fs.readFileSync(path.join(ROOT, "app/sw.js"), "utf8").replace(', "./manual.pdf"', "").replace(/hl-cccs-v\d+/, "hl-fb-" + Date.now().toString(36)));
fs.writeFileSync(path.join(dist, ".nojekyll"), "");
console.log("dist 완료:", (out.length / 1024).toFixed(0) + "KB", "· 관리자 아이디:", ADMIN_ID, "· 키 파일:", keyFile);
