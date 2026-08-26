// widevine.js - Pure JS Widevine CDM for Chrome Extension
// Implements: protobuf codec, ASN.1 DER parser, AES-CMAC, PSSH parser, WVD parser, Widevine CDM

// ─── Protobuf Writer ───
function ProtobufWriter(){this.c=[];}
ProtobufWriter.prototype.v=function(n){var b=[];while(n>0x7f){b.push((n&0x7f)|0x80);n>>>=7;}b.push(n&0x7f);this.c.push(new Uint8Array(b));};
ProtobufWriter.prototype.vb=function(n){var b=[];var lo=n>>>0,hi=Math.floor(n/0x100000000)>>>0;while(hi>0||lo>0x7f){b.push((lo&0x7f)|0x80);lo=(lo>>>7)|((hi&0x7f)<<25);hi>>>=7;}b.push(lo&0x7f);this.c.push(new Uint8Array(b));};
ProtobufWriter.prototype.tag=function(f,w){this.v((f<<3)|w);};
ProtobufWriter.prototype.vf=function(f,v){this.tag(f,0);this.vb(v);};
ProtobufWriter.prototype.bf=function(f,v){this.tag(f,2);if(typeof v==='string')v=new TextEncoder().encode(v);this.v(v.length);this.c.push(new Uint8Array(v));};
ProtobufWriter.prototype.mf=function(f,d){this.tag(f,2);this.v(d.length);this.c.push(new Uint8Array(d));};
ProtobufWriter.prototype.out=function(){var t=0;for(var i=0;i<this.c.length;i++)t+=this.c[i].length;var r=new Uint8Array(t);var o=0;for(var i=0;i<this.c.length;i++){r.set(this.c[i],o);o+=this.c[i].length;}return r;};

// ─── Protobuf Reader ───
function ProtobufReader(b){this.b=b;this.o=0;}
ProtobufReader.prototype.rv=function(){var r=0,s=0;while(this.o<this.b.length){var b=this.b[this.o++];r|=(b&0x7f)<<s;if((b&0x80)===0)break;s+=7;if(s>=35){var hi=0;while(this.o<this.b.length){b=this.b[this.o++];hi+=(b&0x7f)*Math.pow(2,s);if((b&0x80)===0)break;s+=7;}return r+hi;}}return r;};
ProtobufReader.prototype.rb=function(){var l=this.rv();var v=this.b.subarray(this.o,this.o+l);this.o+=l;return v;};
ProtobufReader.prototype.nf=function(){if(this.o>=this.b.length)return null;var t=this.rv();var fn=t>>3,wt=t&7,v;switch(wt){case 0:v=this.rv();break;case 1:v=this.b.subarray(this.o,this.o+8);this.o+=8;break;case 2:v=this.rb();break;case 5:v=this.b.subarray(this.o,this.o+4);this.o+=4;break;default:throw new Error('wire type '+wt);}return{f:fn,w:wt,v:v};};

// ─── ASN.1 DER → JWK ───
function al(b,o){var n=b[o++];if(n<0x80)return{l:n,o:o};var nb=n&0x7f,n2=0;for(var i=0;i<nb;i++)n2=(n2<<8)|b[o++];return{l:n2,o:o};}
function ai(b,o){if(b[o++]!==2)throw new Error('int');var r=al(b,o);o=r.o;var v=b.subarray(o,o+r.l);return{v:v,o:o+r.l};}
function b64u(b){var s=0;while(s<b.length-1&&b[s]===0)s++;b=b.subarray(s);var str='';for(var i=0;i<b.length;i++)str+=String.fromCharCode(b[i]);return btoa(str).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
function rsaDerToJwk(d){var o=0;if(d[o++]!==0x30)throw new Error('seq');o=al(d,o).o;var r=ai(d,o);o=r.o;var c=[];for(var i=0;i<7;i++){r=ai(d,o);o=r.o;c.push(r.v);}return{kty:'RSA',n:b64u(c[0]),e:b64u(c[1]),d:b64u(c[2]),p:b64u(c[3]),q:b64u(c[4]),dp:b64u(c[5]),dq:b64u(c[6]),qi:b64u(c[7])};}

// ─── AES-CMAC (via Web Crypto AES-CBC single-block) ───
async function aesBlock(ck,blk){var iv=new Uint8Array(16);var e=await crypto.subtle.encrypt({name:'AES-CBC',iv:iv},ck,blk);return new Uint8Array(e,0,16);}
function lshX(b,rb){var r=new Uint8Array(16),c=0;for(var i=15;i>=0;i--){var v=(b[i]<<1)|c;r[i]=v&0xff;c=v>>8;}if(rb&&c)r[15]^=0x87;return r;}
function xorB(a,b){var r=new Uint8Array(16);for(var i=0;i<16;i++)r[i]=a[i]^b[i];return r;}
async function cmac(key,msg){var ck=await crypto.subtle.importKey('raw',key,'AES-CBC',false,['encrypt']);var L=await aesBlock(ck,new Uint8Array(16));var K1=lshX(L,(L[0]&0x80)!==0);var K2=lshX(K1,(K1[0]&0x80)!==0);var n=Math.ceil(msg.length/16)||1;var bl=[];for(var i=0;i<n-1;i++)bl.push(msg.subarray(i*16,(i+1)*16));var lb=msg.subarray((n-1)*16);if(lb.length===16)bl.push(xorB(new Uint8Array(lb),K1));else{var p=new Uint8Array(16);for(var i=0;i<lb.length;i++)p[i]=lb[i];p[lb.length]=0x80;bl.push(xorB(p,K2));}var C=new Uint8Array(16);for(var i=0;i<n;i++)C=await aesBlock(ck,xorB(C,bl[i]));return C;}

// ─── PSSH Box Parser ───
var WV_SID=new Uint8Array([0xed,0xef,0x8b,0xa9,0x79,0xd6,0x4a,0xce,0xa3,0xc8,0x27,0xdc,0xd5,0x1d,0x21,0xed]);
function findPssh(data){var res=[];var s=0;while(true){var idx=-1;for(var i=s;i<=data.length-4;i++){if(data[i]===0x70&&data[i+1]===0x73&&data[i+2]===0x73&&data[i+3]===0x68){idx=i;break;}}if(idx===-1)break;if(idx>=4){var sz=(data[idx-4]<<24)|(data[idx-3]<<16)|(data[idx-2]<<8)|data[idx-1];if(sz>=32&&sz<=512&&idx-4+sz<=data.length){var box=data.subarray(idx-4,idx-4+sz);var wv=true;for(var j=0;j<16;j++){if(box[12+j]!==WV_SID[j]){wv=false;break;}}if(wv)res.push(new Uint8Array(box));}}s=idx+4;}return res;}
function parsePssh(box){var v=box[8];if(v===0){var dl=(box[28]<<24)|(box[29]<<16)|(box[30]<<8)|box[31];return{version:v,initData:box.subarray(32,32+dl)};}if(v===1){var kc=(box[28]<<24)|(box[29]<<16)|(box[30]<<8)|box[31];var o=32+kc*16;var dl=(box[o]<<24)|(box[o+1]<<16)|(box[o+2]<<8)|box[o+3];return{version:v,initData:box.subarray(o+4,o+4+dl)};}return null;}

// ─── WVD File Parser ───
function parseWvd(d){var mg=String.fromCharCode(d[0],d[1],d[2]);if(mg!=='WVD')throw new Error('Not WVD');var v=d[3],tp=d[4],sl=d[5];var pkl=(d[7]<<8)|d[8];var pk=d.subarray(9,9+pkl);var co=9+pkl;var cil=(d[co]<<8)|d[co+1];var ci=d.subarray(co+2,co+2+cil);return{version:v,type:tp,securityLevel:sl,privateKeyDer:new Uint8Array(pk),clientIdBytes:new Uint8Array(ci)};}

// ─── Widevine CDM ───
function WidevineCDM(wvdData){var p=parseWvd(wvdData);this.type=p.type;this.securityLevel=p.securityLevel;this.privateKeyDer=p.privateKeyDer;this.clientIdBytes=p.clientIdBytes;this.signKey=null;this.decryptKey=null;this.context=null;}

WidevineCDM.prototype.init=async function(){if(this.signKey)return;var jwk=rsaDerToJwk(this.privateKeyDer);this.signKey=await crypto.subtle.importKey('jwk',jwk,{name:'RSA-PSS',hash:'SHA-1'},false,['sign']);this.decryptKey=await crypto.subtle.importKey('jwk',jwk,{name:'RSA-OAEP'},false,['decrypt']);};

WidevineCDM.prototype.generateChallenge=async function(psshInitData){await this.init();var rid=crypto.getRandomValues(new Uint8Array(16));var n=crypto.getRandomValues(new Uint32Array(1))[0];if(n===0)n=1;
// WidevinePsshData
var wp=new ProtobufWriter();wp.bf(1,psshInitData);wp.vf(2,1);wp.bf(3,rid);var wpb=wp.out();
// ContentIdentification
var ci=new ProtobufWriter();ci.mf(1,wpb);var cib=ci.out();
// LicenseRequest
var lr=new ProtobufWriter();lr.mf(1,this.clientIdBytes);lr.mf(2,cib);lr.vf(3,1);lr.vf(4,Math.floor(Date.now()/1000));lr.vf(6,21);lr.vf(7,n);var lrb=lr.out();
// Sign
var sig=await crypto.subtle.sign({name:'RSA-PSS',saltLength:20},this.signKey,lrb);
// SignedMessage
var sm=new ProtobufWriter();sm.vf(1,1);sm.bf(2,lrb);sm.bf(3,new Uint8Array(sig));var ch=sm.out();
// Context for key derivation
var enc=new TextEncoder().encode('ENCRYPTION');var mac=new TextEncoder().encode('AUTHENTICATION');var ec=new Uint8Array(enc.length+1+lrb.length+4);ec.set(enc,0);ec[enc.length]=0;ec.set(lrb,enc.length+1);var p=lrb.length+enc.length+1;ec[p]=0;ec[p+1]=0;ec[p+2]=0;ec[p+3]=0x80;
var mc=new Uint8Array(mac.length+1+lrb.length+4);mc.set(mac,0);mc[mac.length]=0;mc.set(lrb,mac.length+1);p=lrb.length+mac.length+1;mc[p]=0;mc[p+1]=0;mc[p+2]=0x02;mc[p+3]=0;
this.context={enc:ec,mac:mc};return ch;};

WidevineCDM.prototype.parseLicense=async function(resp){var r=new ProtobufReader(resp);var t,m,s,sk,oc;var f;while((f=r.nf())!==null){switch(f.f){case 1:t=f.v;break;case 2:m=f.v;break;case 3:s=f.v;break;case 4:sk=f.v;break;case 9:oc=f.v;break;}}
if(t!==2)throw new Error('Expected LICENSE(2), got '+t);
// Decrypt session key
var dsk=await crypto.subtle.decrypt({name:'RSA-OAEP'},this.decryptKey,sk);var ssk=new Uint8Array(dsk);
// Derive keys
var ei=new Uint8Array(1+this.context.enc.length);ei[0]=1;ei.set(this.context.enc,1);this.ek=await cmac(ssk,ei);
var mi1=new Uint8Array(1+this.context.mac.length);mi1[0]=1;mi1.set(this.context.mac,1);var mk1=await cmac(ssk,mi1);
var mi2=new Uint8Array(1+this.context.mac.length);mi2[0]=2;mi2.set(this.context.mac,1);var mk2=await cmac(ssk,mi2);
this.mks=new Uint8Array(mk1.length+mk2.length);this.mks.set(mk1,0);this.mks.set(mk2,mk1.length);
// Parse License
var lr=new ProtobufReader(m);var keys=[];var lf;while((lf=lr.nf())!==null){if(lf.f===3){var kr=new ProtobufReader(lf.v);var kid=null,iv=null,ek=null,kt=null;var kf;while((kf=kr.nf())!==null){switch(kf.f){case 1:kid=kf.v;break;case 2:iv=kf.v;break;case 3:ek=kf.v;break;case 4:kt=kf.v;break;}}if(ek&&iv)keys.push({kid:kid,iv:iv,ek:ek,kt:kt});}}
// Decrypt content keys
var res=[];for(var i=0;i<keys.length;i++){var k=keys[i];if(k.kt!==2)continue;var ak=await crypto.subtle.importKey('raw',this.ek,'AES-CBC',false,['decrypt']);var d=await crypto.subtle.decrypt({name:'AES-CBC',iv:k.iv},ak,k.ek);var pad=new Uint8Array(d);var pl=pad[pad.length-1];var ck=pad.subarray(0,pad.length-pl);var kh='';for(var j=0;j<k.kid.length;j++)kh+=k.kid[j].toString(16).padStart(2,'0');var kh2='';for(var j=0;j<ck.length;j++)kh2+=ck[j].toString(16).padStart(2,'0');res.push({kid:kh,key:kh2,type:'CONTENT'});}
return res;};
