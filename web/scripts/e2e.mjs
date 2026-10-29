import {spawnSync} from 'node:child_process';
import {existsSync,mkdirSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const output=path.join(root,'evidence','frontend-e2e',new Date().toISOString().replace(/[:.]/g,'-'));
mkdirSync(output,{recursive:true});
const local=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const python=process.env.RA_TEST_PYTHON||(existsSync(local)?local:'python');
function run(command,args,cwd){const r=spawnSync(command,args,{cwd,stdio:'inherit',env:{...process.env,PYTHONUTF8:'1',PYTHONPATH:root}});if(r.status!==0)process.exit(r.status??1);}
run(process.execPath,[path.join(root,'web/node_modules/typescript/bin/tsc'),'--noEmit'],path.join(root,'web'));
await build({root:path.join(root,'web'),build:{outDir:path.join(output,'dist')}});
run(python,['-X','utf8',path.join(root,'tests/check_frontend_experience_browser.py'),'--static-build',path.join(output,'dist'),'--output',path.join(output,'checks')],root);
console.log('Isolated browser report: '+path.join(output,'checks/result.json'));
