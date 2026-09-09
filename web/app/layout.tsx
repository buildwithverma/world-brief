import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {title:'World Brief — Your personal news assistant',description:'World news, concise summaries, source evidence, and a private local cache.'};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
