"""Small pinned native-package fixture; no native executable invocation."""
import base64
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location('cboard_windows_native', Path(__file__).parents[1]/'scripts/windows/install_native.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def package(name):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        data = b'native fixture'
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    data = buffer.getvalue()
    entry = {'url':'https://registry.npmjs.org/@openai/codex/-/fixture.tgz',
             'integrity':'sha512-'+base64.b64encode(hashlib.sha512(data).digest()).decode()}
    return data,entry


def test_current_native_bin_layout_and_checksum(tmp_path):
    data,entry = package('package/vendor/x86_64-pc-windows-msvc/bin/codex.exe')
    with patch.object(native.urllib.request,'urlopen',return_value=io.BytesIO(data)):
        executable = native.install(tmp_path/'native',entry)
    assert executable.read_bytes()==b'native fixture'
    with pytest.raises(ValueError,match='already exists'):
        native.install(tmp_path/'native',entry)


def test_checksum_failure_never_installs(tmp_path):
    data,entry = package('package/vendor/x86_64-pc-windows-msvc/bin/codex.exe')
    with patch.object(native.urllib.request,'urlopen',return_value=io.BytesIO(data+b'corrupt')):
        with pytest.raises(ValueError,match='checksum'):
            native.install(tmp_path/'native',entry)
    assert not (tmp_path/'native').exists()


def test_native_archive_rejects_traversal(tmp_path):
    data,entry = package('../outside')
    with patch.object(native.urllib.request,'urlopen',return_value=io.BytesIO(data)):
        with pytest.raises(ValueError,match='unsafe'):
            native.install(tmp_path/'native',entry)
    assert not (tmp_path/'outside').exists()
