"""Package distributable launcher and source without local accounts/config/game assets."""
from pathlib import Path
import hashlib
import json
import zipfile
import sys

root=Path(__file__).parent
release=root/'release'/(sys.argv[1] if len(sys.argv)>1 else '')
exe=release/'IOPLauncher_Server.exe'
excluded={'.git','build','release','__pycache__','server_data','diagnostics'}
with zipfile.ZipFile(release/'IOPLauncher_Server_Source.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if not path.is_file() or any(part in excluded for part in relative.parts): continue
        if path.suffix.lower() not in ('.py','.md','.bat','.txt') and path.name!='.gitignore': continue
        archive.write(path,'iop_launcher_server/'+relative.as_posix())
with zipfile.ZipFile(release/'IOPLauncher_Server_Package.zip','w',zipfile.ZIP_DEFLATED) as archive:
    archive.write(exe,exe.name)
    archive.write(root/'PRIVATE_SERVER.md','사용방법.md')
    archive.write(root/'HOTKEY_ANALYSIS.md','HOTKEY_ANALYSIS.md')
    archive.write(root/'update-manifest.json','update-manifest.json')
for name in ('IOPLauncher_Server_Source.zip','IOPLauncher_Server_Package.zip'):
    with zipfile.ZipFile(release/name) as archive:
        assert archive.testzip() is None
        assert not any('server_data' in p or p.endswith('iop_launcher_config.json') for p in archive.namelist())
manifest={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
          for p in (exe,release/'IOPLauncher_Server_Source.zip',release/'IOPLauncher_Server_Package.zip')}
(release/'SHA256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps(manifest,indent=2))
