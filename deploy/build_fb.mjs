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
const field=(id,l,type,ph)=>'<label class="f">'+l+'<input id="'+id+'" type="'+(type||"text")+'" autocomplete="'+(type==="password"?"current-password":"username")+'" placeholder="'+(ph||"")+'"></label>';
const msgEl='<div class="small" id="smsg" style="color:var(--bad)"></div>';
const setMsg=t=>{const m=$("#smsg");if(m)m.textContent=t};
if(!window.firebase){box("연결할 수 없습니다","인터넷 연결을 확인하고 새로고침해 주세요.");return}
firebase.initializeApp(CFG);
const auth=firebase.auth(), db=firebase.firestore();
try{auth.setPersistence(firebase.auth.Auth.Persistence.LOCAL)}catch(e){}
const em=id=>id+"@"+DOMAIN, idOk=id=>/^[a-z0-9_]{3,20}$/.test(id);
let view="login", creating=false, unsub=null;
const ERR={"auth/invalid-credential":"아이디 또는 비밀번호가 맞지 않습니다","auth/wrong-password":"아이디 또는 비밀번호가 맞지 않습니다","auth/user-not-found":"아이디 또는 비밀번호가 맞지 않습니다","auth/invalid-login-credentials":"아이디 또는 비밀번호가 맞지 않습니다",
 "auth/email-already-in-use":"이미 사용 중인 아이디입니다","auth/weak-password":"비밀번호는 6자 이상이어야 합니다","auth/too-many-requests":"시도가 많아 잠시 막혔습니다. 잠시 후 다시 해 주세요","auth/network-request-failed":"인터넷 연결을 확인해 주세요"};
function loginView(){box("축우 현장점검","관리자가 승인한 팀원만 사용할 수 있습니다.",card(field("s_id","아이디","text","영문 소문자·숫자")+field("s_pw","비밀번호","password")+'<button class="primary" data-s="login">로그인</button>'+msgEl+'<button class="ghost" data-s="toSignup">처음이신가요? 가입 신청</button>'))}
function signupView(){box("가입 신청","가입 후 관리자가 승인하면 사용할 수 있습니다.",card(field("s_id","아이디","text","영문 소문자·숫자 3~20자")+field("s_pw","비밀번호","password","6자 이상")+field("s_pw2","비밀번호 확인","password")+field("s_name","이름","text","예: 홍길동")+field("s_note","소속 · 연락처","text","예: 전북 지역 / 010-0000-0000")+'<button class="primary" data-s="signup">가입 신청</button>'+msgEl+'<button class="ghost" data-s="toLogin">이미 아이디가 있어요</button>'))}
const logoutBtn='<div class="row" style="justify-content:center"><button data-s="logout">로그아웃</button></div>';
main.addEventListener("click",async e=>{const b=e.target.closest("[data-s]");if(!b)return;const a=b.dataset.s;
  if(a==="toSignup"){view="signup";return signupView()} if(a==="toLogin"){view="login";return loginView()}
  if(a==="logout"){if(unsub)unsub();view="login";return auth.signOut()}
  if(a==="login"){const id=($("#s_id").value||"").trim().toLowerCase(),pw=$("#s_pw").value||"";if(!idOk(id))return setMsg("아이디를 확인해 주세요");b.disabled=true;
    try{await auth.signInWithEmailAndPassword(em(id),pw)}catch(err){setMsg(ERR[err.code]||"로그인하지 못했습니다 ("+(err.code||"오류")+")");b.disabled=false}return}
  if(a==="signup"){const id=($("#s_id").value||"").trim().toLowerCase(),pw=$("#s_pw").value||"",pw2=$("#s_pw2").value||"",name=($("#s_name").value||"").trim(),note=($("#s_note").value||"").trim();
    if(!idOk(id))return setMsg("아이디는 영문 소문자·숫자·밑줄 3~20자입니다"); if(pw.length<6)return setMsg("비밀번호는 6자 이상이어야 합니다"); if(pw!==pw2)return setMsg("비밀번호 확인이 맞지 않습니다"); if(!name)return setMsg("이름을 넣어 주세요");
    b.disabled=true;creating=true;
    try{const c=await auth.createUserWithEmailAndPassword(em(id),pw); if(id!==ADMIN_ID)await db.doc("users/"+c.user.uid).set({id,name:name.slice(0,40),note:note.slice(0,120),status:"pending",createdAt:Date.now()}); creating=false; route(c.user)}
    catch(err){creating=false;setMsg(ERR[err.code]||"가입하지 못했습니다 ("+(err.code||"오류")+")");b.disabled=false}return}
  if(a==="key"){const k=($("#s_key").value||"").trim();b.disabled=true;const code=await decrypt(k).catch(()=>null);if(!code){setMsg("키가 맞지 않습니다");b.disabled=false;return}
    try{await db.doc("secrets/content").set({k,at:Date.now()})}catch(err){setMsg("키를 저장하지 못했습니다. 보안 규칙을 확인해 주세요 ("+(err.code||"")+")");b.disabled=false;return}
    run(code,pendingInfo)}
});
main.addEventListener("keydown",e=>{if(e.key==="Enter"&&e.target.tagName==="INPUT"){const b=main.querySelector(".primary[data-s]");if(b)b.click()}});
let pendingInfo=null;
function route(u){
  if(!u){view==="signup"?signupView():loginView();return}
  const id=(u.email||"").split("@")[0], isAdmin=id===ADMIN_ID;
  if(isAdmin)return loadKey({uid:u.uid,id,name:"관리자",isAdmin:true});
  box("권한 확인 중","잠시만 기다려 주세요.");
  if(unsub)unsub();
  unsub=db.doc("users/"+u.uid).onSnapshot(d=>{
    if(!d.exists){if(creating)return;return box("가입 정보가 없습니다","가입 신청을 다시 해 주세요. 계속 안 되면 관리자에게 문의하세요.",logoutBtn)}
    const x=d.data();
    if(x.status==="approved"){unsub();unsub=null;return loadKey({uid:u.uid,id,name:x.name||id,isAdmin:false})}
    if(x.status==="blocked")return box("사용이 중지되었습니다","관리자가 이 아이디의 사용을 중지했습니다. 관리자에게 문의하세요.",logoutBtn);
    if(x.status==="rejected")return box("가입이 승인되지 않았습니다","관리자에게 문의하세요.",logoutBtn);
    box("승인 대기 중",esc(x.name||id)+"님("+esc(id)+")의 가입 신청을 보냈습니다. 관리자가 승인하면 이 화면이 자동으로 열립니다.",logoutBtn);
  },err=>box("권한을 확인하지 못했습니다","잠시 후 새로고침해 주세요. ("+esc(err.code||"")+")",logoutBtn));
}
auth.onAuthStateChanged(u=>{if(creating)return;route(u)});
const b64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function decrypt(k){const key=await crypto.subtle.importKey("raw",b64(k),"AES-GCM",false,["decrypt"]);const pt=await crypto.subtle.decrypt({name:"AES-GCM",iv:b64(IV)},key,b64(ENC));return new TextDecoder().decode(pt)}
async function loadKey(info){
  box("앱을 여는 중","잠시만 기다려 주세요.");pendingInfo=info;
  let k=null;try{const d=await db.doc("secrets/content").get();if(d.exists)k=d.data().k}catch(e){}
  if(k){const code=await decrypt(k).catch(()=>null);if(code)return run(code,info)}
  if(info.isAdmin)return box("앱 키 등록","처음 한 번만 관리자가 앱 키를 등록합니다. 받은 앱 키를 붙여넣으세요.",card(field("s_key","앱 키","password")+'<button class="primary" data-s="key">등록하고 시작</button>'+msgEl)+logoutBtn);
  box("앱 준비 중","관리자가 아직 앱 설정을 마치지 않았습니다. 잠시 후 다시 열어 주세요.",logoutBtn);
}
function run(code,info){
  window.FB={uid:info.uid,id:info.id,name:info.name,isAdmin:info.isAdmin,db,logout:()=>auth.signOut().then(()=>location.reload())};
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
