import {useState, type FormEvent} from 'react';
import {createRoot} from 'react-dom/client';
import Home from '../app/world-brief';
import '../app/globals.css';
import {api,setAccessTokenProvider} from '../lib/brief-api';

// Session stays in memory: reloading the page requires signing in again.
const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
let session: {access_token: string;refresh_token: string;expires_at: number}|null = null;
let refreshing: Promise<string>|null = null;
async function tokenRequest(body: object, grant: string) {
 const response = await fetch(`${url}/auth/v1/token?grant_type=${grant}`, {
  method:'POST',headers:{apikey:key,'Content-Type':'application/json'},body:JSON.stringify(body),
 });
 if(!response.ok) throw new Error('Unable to sign in. Check your email and password.');
 const value=await response.json() as {access_token:string;refresh_token:string;expires_in:number};
 session={...value,expires_at:Date.now()+value.expires_in*1000};
 return value.access_token as string;
}
setAccessTokenProvider(async()=>{
 if(!session) return '';
 if(session.expires_at>Date.now()+60000) return session.access_token;
 if(!refreshing) refreshing=tokenRequest({refresh_token:session.refresh_token},'refresh_token').finally(()=>{refreshing=null});
 return refreshing;
});

function App(){
 const [signedIn,setSignedIn]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function signIn(event:FormEvent<HTMLFormElement>){
  event.preventDefault();setBusy(true);setError('');
  const data=new FormData(event.currentTarget);
  try{await tokenRequest({email:data.get('email'),password:data.get('password')},'password');await api('preferences');setSignedIn(true)}
  catch(e){setError(e instanceof Error?e.message:'Sign-in failed.')}
  finally{setBusy(false)}
 }
 if(!url||!key||!import.meta.env.VITE_API_URL) return <p>Hosting configuration is incomplete. Set the website’s API and Supabase environment variables, then rebuild.</p>;
 if(signedIn) return <><button style={{position:'fixed',bottom:12,right:12,zIndex:100,background:'white',padding:'8px 16px',borderRadius:20}} onClick={()=>{session=null;setSignedIn(false)}}>Sign out</button><Home/></>;
 return <main style={{minHeight:'100vh',display:'grid',placeItems:'center',background:'#f6f5f2'}}><form onSubmit={signIn} style={{width:'min(360px,90vw)',display:'grid',gap:18,padding:30,background:'white',borderRadius:24,boxShadow:'0 10px 40px #0000000a'}}>
  <h1 style={{fontSize:28,fontWeight:600}}>World Brief</h1><p>Your news, in a few clear words. Sign in to continue.</p>
  <label>Email<input name="email" type="email" autoComplete="username" required style={{display:'block',width:'100%',padding:10,border:'1px solid #ddd',borderRadius:8}}/></label>
  <label>Password<input name="password" type="password" autoComplete="current-password" required style={{display:'block',width:'100%',padding:10,border:'1px solid #ddd',borderRadius:8}}/></label>
  {error&&<p role="alert">{error}</p>}<button disabled={busy} style={{padding:12,borderRadius:12,background:'#181818',color:'white'}}>{busy?'Signing in…':'Sign in'}</button>
 </form></main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
