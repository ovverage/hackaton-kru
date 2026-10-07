"""Create local release artifacts from an explicit source allowlist, never .local."""
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
import zipfile

from shared.version import RULE_VERSION

ROOT=Path(__file__).resolve().parents[1]
VERSION=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
DOC_SUFFIXES = {'.md', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.json'}


def source_files(root):
    """Only committed/staged public source; never collect ignored training runs."""
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').split('\0')
    directories = {'agent','backend','shared','scripts','tests','extension','deploy','training','packaging','.github','web','docs','deliverables'}
    top_level = {'README.md','THIRD_PARTY_NOTICES.md','.gitignore','.gitattributes','Start-Student.cmd','pyproject.toml','requirements-core.lock','requirements-student.lock','requirements-windows.lock','model-manifest.json','package.json'}
    paths = []
    for name in tracked:
        relative = Path(name)
        if not name or (relative.parts[0] not in directories and name not in top_level):
            continue
        if any(part in {'.local', 'node_modules', '__pycache__', 'runs', 'data', 'models', 'dist'} for part in relative.parts):
            continue
        path = root / relative
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    return paths


def main():
    dist=ROOT/'dist'
    source=dist/f'Qorgau-Source-{VERSION}.zip'
    paths=source_files(ROOT)
    public_paths=set(paths)
    with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            archive.write(path,path.relative_to(ROOT).as_posix())
    staff=dist/f'Qorgau-Admin-{VERSION}.zip'
    with zipfile.ZipFile(staff,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in (dist/'Qorgau-SecurityBridge.exe',ROOT/'deploy/Prepare-Qorgau-Exam.ps1',source,ROOT/'README.md',ROOT/'THIRD_PARTY_NOTICES.md',ROOT/'model-manifest.json'):
            archive.write(file,file.name)
        for folder,name in ((ROOT/'extension','extension'),(ROOT/'docs','docs'),(dist/'third-party','third-party')):
            for file in folder.rglob('*'):
                if file.is_file() and not file.is_symlink() and (name=='third-party' or file in public_paths) and (name!='docs' or file.suffix.lower() in DOC_SUFFIXES):
                    archive.write(file,name+'/'+file.relative_to(folder).as_posix())
        for file in (ROOT/'deliverables').iterdir():
            if file not in public_paths or file.suffix.lower() not in {'.pptx', '.docx'}:
                continue
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
    model_version=json.loads((ROOT/'model-manifest.json').read_text(encoding='utf-8'))['version']
    manifest={'version':VERSION,'model_version':model_version,'rule_version':RULE_VERSION,'windows':'x64, Python 3.12 build',
              'mode':'OBSERVE / GUARDED; STRICT unavailable',
              'source':source_state,'files':files}
    (dist/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
