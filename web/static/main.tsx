import {createRoot} from 'react-dom/client';
import Home from '../app/world-brief';
import '../app/globals.css';

function App(){
 if(!import.meta.env.VITE_API_URL) return <p>Hosting configuration is incomplete. Set the website’s API URL, then rebuild.</p>;
 return <Home/>;
}
createRoot(document.getElementById('root')!).render(<App/>);
