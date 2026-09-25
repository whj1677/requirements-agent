import React from 'react';
const paths:Record<string,React.ReactNode>={
 document:<><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h4M9 12h6M9 16h6"/></>,
 target:<><circle cx="11" cy="13" r="8"/><circle cx="11" cy="13" r="4"/><path d="m11 13 9-10m-1 0v4h4"/></>,
 shield:<><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/></>,
 question:<><circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5m0 3h.01"/></>,
 check:<path d="m5 12 4 4L19 6"/>,
 lock:<><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/></>,
 edit:<><path d="m4 16 12-12 4 4L8 20H4zm10-10 4 4"/></>,
 upload:<><path d="m7 8 5-5 5 5m-5-5v13M4 15v6h16v-6"/></>,
 light:<><path d="M8 17c0-4-3-4-3-8a7 7 0 0 1 14 0c0 4-3 4-3 8m-8 0h8m-7 4h6"/></>,
 list:<><path d="M9 6h12M9 12h12M9 18h12M3 6h1M3 12h1M3 18h1"/></>,
};
export function Icon({name}:{name:string}){return <svg className="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.document}</svg>;}
