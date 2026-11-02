# PyInstaller spec. Build from the repo root with:  pyinstaller packaging/inboxpilot.spec --noconfirm --clean
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (
    collect_submodules("inboxpilot")
    + collect_submodules("googleapiclient")
    + collect_submodules("google_auth_oauthlib")
    + collect_submodules("anthropic")
    + ["keyring.backends.Windows", "keyring.backends.fail"]
)
datas = collect_data_files("googleapiclient") + collect_data_files("certifi")

a = Analysis(
    ["../run_inboxpilot.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="InboxPilot", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="InboxPilot")
