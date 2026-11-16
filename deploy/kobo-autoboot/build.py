"""Build the USB package; never mount, install on, or reboot a device."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def build(output: Path) -> None:
    source = Path(__file__).resolve().parent
    prefix = '.adds/kobosync-autoboot/'
    files = {
        prefix+'manage.sh': (source / 'manage.sh').read_bytes(),
        prefix+'autostart.sh': (source / 'autostart.sh').read_bytes(),
        '.adds/nm/kobosync-autoboot': (source / 'nickelmenu').read_bytes(),
    }
    manifest = {'version': '0.1.0', 'files': {
        name: hashlib.sha256(contents).hexdigest() for name, contents in files.items()
    }}
    files[prefix+'manifest.json'] = (json.dumps(manifest, indent=2)+'\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 16, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parents[2] / 'dist/kobosync-autoboot.zip')
    arguments = parser.parse_args()
    build(arguments.output)
    print(arguments.output)
