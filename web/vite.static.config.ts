import {defineConfig, loadEnv} from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/postcss';
import {fileURLToPath} from 'node:url';

export default defineConfig(({mode})=>{
 const directory=fileURLToPath(new URL('.', import.meta.url));
 const env={...loadEnv(mode,directory),...process.env};
 if(!env.VITE_API_URL?.startsWith('https://')||!env.VITE_SUPABASE_URL?.startsWith('https://')||!env.VITE_SUPABASE_PUBLISHABLE_KEY)
  throw new Error('Set VITE_API_URL, VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY before building the hosted website.');
 return {
 root: fileURLToPath(new URL('./static', import.meta.url)),
 envDir: fileURLToPath(new URL('.', import.meta.url)),
 resolve: {alias: {'@': fileURLToPath(new URL('.', import.meta.url))}},
 css: {postcss: {plugins: [tailwindcss()]}},
 plugins: [react()],
 build: {outDir: '../dist-static', emptyOutDir: true},
};});
