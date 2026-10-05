"""Create local release artifacts from an explicit source allowlist, never .local."""
import hashlib
import json
import subprocess
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DOC_SUFFIXES = {'.md', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.json'}


def main():
    dist=ROOT/'dist'
    source=dist/'Qorgau-Source-0.3.0.zip'
    paths=[]
    for name in ('agent','backend','shared','scripts','tests','extension','deploy','web/src','web/public'):
        folder=ROOT/name
        if folder.exists():
            paths.extend(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    paths.extend(p for p in (ROOT/'docs').rglob('*') if p.is_file() and p.suffix.lower() in DOC_SUFFIXES)
    paths.extend(ROOT/name for name in ('README.md','THIRD_PARTY_NOTICES.md','.gitignore','.gitattributes','Start-Student.cmd','pyproject.toml','requirements-core.lock','requirements-student.lock','model-manifest.json','package.json','web/package.json','web/package-lock.json','web/index.html','web/vite.config.ts','web/tsconfig.json','web/tsconfig.app.json','web/tsconfig.node.json') if (ROOT/name).is_file())
    paths.extend((ROOT/'deliverables').glob('*.pptx'))
    with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            archive.write(path,path.relative_to(ROOT).as_posix())
    staff=dist/'Qorgau-Admin-0.3.0.zip'
    with zipfile.ZipFile(staff,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in (dist/'Qorgau-SecurityBridge.exe',ROOT/'deploy/Prepare-Qorgau-Exam.ps1',source,ROOT/'README.md',ROOT/'THIRD_PARTY_NOTICES.md',ROOT/'model-manifest.json'):
            archive.write(file,file.name)
        for folder,name in ((ROOT/'extension','extension'),(ROOT/'docs','docs'),(dist/'third-party','third-party')):
            for file in folder.rglob('*'):
                if file.is_file() and (name!='docs' or file.suffix.lower() in DOC_SUFFIXES):
                    archive.write(file,name+'/'+file.relative_to(folder).as_posix())
        for file in (ROOT/'deliverables').glob('*.pptx'):
            archive.write(file,'deliverables/'+file.name)
    files=[]
    for path in (dist/'Qorgau-Student.exe',dist/'Qorgau-NativeHost.exe',dist/'Qorgau-SecurityBridge.exe',source,staff):
        with path.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        files.append({'file':path.name,'bytes':path.stat().st_size,'sha256':digest})
    try:
        revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
        source_state={'commit':revision,'working_tree_dirty':dirty}
    except (OSError, subprocess.CalledProcessError):
        source_state={'commit':None,'working_tree_dirty':None}
    manifest={'version':'0.3.0','model_version':'2026.10.06.1','rule_version':'3.0','windows':'x64, Python 3.12 build',
              'mode':'OBSERVE; experimental SEB adapter is not accepted STRICT',
              'source':source_state,'files':files}
    (dist/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
