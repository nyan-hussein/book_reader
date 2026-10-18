"""Build the Bookshelf installer for the system this runs on (no Python needed to run it).

    python installers/build_app.py                   Windows -> installers/windows/Bookshelf-Setup.exe
                                                     Mac     -> installers/mac/Bookshelf.dmg
    python installers/build_app.py --test-installer  (Windows) also install, check, uninstall

A Mac installer can only be built on a Mac (or by the GitHub "Build Mac app" action), and
a Windows one only on Windows. Temporary files are made in build/ and deleted at the end.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VERSION = "1.1.0"  # shown in Windows' installed-apps list; raise it for each new version

INSTALLERS = Path(__file__).resolve().parent    # this folder: settings + finished installers
ROOT = INSTALLERS.parent                        # the project folder
SRC = ROOT / "src"                              # the app's code
BUILD = ROOT / "build"                          # temporary, deleted after a successful build
APP_OUT = BUILD / "app"                         # the built app, before it's packed
WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
OUT = INSTALLERS / ("windows" if WINDOWS else "mac")  # where the finished installer goes
ICON = BUILD / ("bookshelf.ico" if WINDOWS else "bookshelf.icns")
INNO = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6/ISCC.exe"]

# Qt parts the app never uses; leaving them out makes the download much smaller
EXCLUDE = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNetwork", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSql", "PySide6.QtSvg",
    "PySide6.QtSvgWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects",
    "PySide6.QtSensors", "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtTest",
    "tkinter", "unittest", "pydoc",
    # big libraries that happen to be installed but the app doesn't use
    "numpy", "scipy", "PIL", "cryptography", "pydantic", "pydantic_core", "pandas",
    "matplotlib", "IPython", "jedi", "lxml", "fontTools", "sympy",
]
if WINDOWS:
    EXCLUDE += ["qtawesome", "qtpy"]  # Windows uses its own icon font; the Mac needs these

# Windows: Qt files pulled in by plugins the app doesn't need (software OpenGL, QML/Quick,
# virtual keyboard, networking, SVG icons, PDF module, translations)
PRUNE = [
    "PySide6/opengl32sw.dll", "PySide6/Qt6Quick*.dll", "PySide6/Qt6Qml*.dll",
    "PySide6/Qt6Pdf*.dll", "PySide6/Qt6Network.dll", "PySide6/Qt6VirtualKeyboard*.dll",
    "PySide6/Qt6Svg*.dll", "PySide6/Qt6OpenGL*.dll", "PySide6/translations",
    "PySide6/plugins/platforminputcontexts", "PySide6/plugins/qmltooling",
    "PySide6/plugins/networkinformation", "PySide6/plugins/tls", "PySide6/plugins/iconengines",
    "PySide6/plugins/imageformats/qsvg.dll", "PySide6/plugins/imageformats/qpdf.dll",
]

def icon_pngs(sizes) -> dict[int, bytes]:
    """The app icon (drawn by the app itself) as PNG images."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtWidgets import QApplication

    sys.path.insert(0, str(SRC))
    from bookshelf.theme import app_icon_pixmap

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 (needed to draw)
    out = {}
    for size in sizes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        app_icon_pixmap(size).save(buf, "PNG")
        out[size] = bytes(data)
    return out


def make_icon() -> None:
    BUILD.mkdir(exist_ok=True)
    if WINDOWS:
        images = icon_pngs((16, 24, 32, 48, 64, 128, 256))
        # .ico = header + one directory entry per size + the PNG images
        header = struct.pack("<HHH", 0, 1, len(images))
        offset = 6 + 16 * len(images)
        entries, blobs = b"", b""
        for size, png in images.items():
            dim = 0 if size >= 256 else size
            entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
            blobs += png
            offset += len(png)
        ICON.write_bytes(header + entries + blobs)
    elif MAC:
        # .icns comes from an .iconset folder of PNGs, made with Apple's iconutil
        images = icon_pngs((16, 32, 64, 128, 256, 512, 1024))
        iconset = BUILD / "bookshelf.iconset"
        shutil.rmtree(iconset, ignore_errors=True)
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            (iconset / f"icon_{size}x{size}.png").write_bytes(images[size])
            (iconset / f"icon_{size}x{size}@2x.png").write_bytes(images[size * 2])
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICON)], check=True)


def build() -> None:
    args = [
        sys.executable, "-m", "PyInstaller", str(SRC / "main.py"),
        "--name", "Bookshelf",
        "--windowed",            # no console window; on Mac this makes Bookshelf.app
        "--onedir",              # starts fast
        "--noconfirm", "--clean",
        "--icon", str(ICON),
        "--distpath", str(APP_OUT),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
    ]
    if MAC:
        args += ["--osx-bundle-identifier", "com.bookshelf.reader"]
    for module in EXCLUDE:
        args += ["--exclude-module", module]
    subprocess.run(args, check=True)
    if WINDOWS:
        internal = APP_OUT / "Bookshelf" / "_internal"
        for pattern in PRUNE:
            for path in internal.glob(pattern):
                shutil.rmtree(path) if path.is_dir() else path.unlink()


def app_exe() -> Path:
    if MAC:
        return APP_OUT / "Bookshelf.app" / "Contents" / "MacOS" / "Bookshelf"
    return APP_OUT / "Bookshelf" / ("Bookshelf.exe" if WINDOWS else "Bookshelf")


def self_test(exe: Path | None = None) -> None:
    """Start the built app, open a sample book, and check it rendered."""
    import pymupdf
    exe = exe or app_exe()
    tmp = Path(tempfile.mkdtemp(prefix="bookshelf-test-"))
    sample = tmp / "sample.pdf"
    doc = pymupdf.open()
    for n in range(3):
        doc.new_page().insert_text((72, 100), f"Self-test page {n + 1}", fontsize=28)
    doc.save(sample)
    shot = tmp / "shot.png"
    env = dict(os.environ, BOOKSHELF_DATA_DIR=str(tmp / "data"),
               BOOKSHELF_SELFTEST=f"{sample}|{shot}")
    subprocess.run([str(exe)], env=env, timeout=180, check=True)
    if not shot.exists() or shot.stat().st_size < 10_000:
        raise SystemExit("Self-test FAILED: the built app did not show the book")
    print(f"  self-test passed: {exe}")


def package_dmg() -> Path:
    """A disk image with the app, a shortcut to Applications and the how-to note."""
    dmg = OUT / "Bookshelf.dmg"
    stage = BUILD / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    # ditto keeps the app's signature and links intact
    subprocess.run(["ditto", str(APP_OUT / "Bookshelf.app"), str(stage / "Bookshelf.app")], check=True)
    (stage / "Applications").symlink_to("/Applications")
    shutil.copy(OUT / "How to open.txt", stage)
    dmg.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-volname", "Bookshelf", "-srcfolder", str(stage),
                    "-ov", "-format", "UDZO", str(dmg)], check=True)
    return dmg


def make_installer() -> Path | None:
    iscc = next((p for p in INNO if p.exists()), None)
    if iscc is None:
        print("  Inno Setup 6 is needed to make the installer: https://jrsoftware.org/isinfo.php")
        return None
    subprocess.run([str(iscc), "/Q", f"/DAppVersion={VERSION}",
                    f"/DSourceDir={APP_OUT / 'Bookshelf'}", f"/DIconFile={ICON}",
                    f"/DOutputDir={OUT}", str(OUT / "installer.iss")], check=True)
    return OUT / "Bookshelf-Setup.exe"


def test_installer(setup: Path) -> None:
    """Install silently into a temporary folder, run the installed app, then uninstall."""
    target = Path(tempfile.mkdtemp(prefix="bookshelf-install-")) / "Bookshelf"
    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
                    "/TASKS=", f"/DIR={target}"], check=True, timeout=300)
    installed = target / "Bookshelf.exe"
    if not installed.exists():
        raise SystemExit("Installer test FAILED: Bookshelf.exe was not installed")
    self_test(installed)
    subprocess.run([str(target / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                   check=True, timeout=300)
    for _ in range(40):  # the uninstaller finishes in the background
        if not installed.exists():
            break
        time.sleep(0.5)
    if installed.exists():
        raise SystemExit("Installer test FAILED: uninstall left the app behind")
    print("  installer test passed: installed, opened a book, uninstalled cleanly")


if __name__ == "__main__":
    if not (WINDOWS or MAC):
        raise SystemExit("Installers can be built on Windows or a Mac.")
    print("1/3  drawing the icon")
    make_icon()
    print("2/3  building the app (takes a few minutes)")
    build()
    self_test()
    print("3/3  making the installer")
    installer = package_dmg() if MAC else make_installer()
    if installer and WINDOWS and "--test-installer" in sys.argv:
        test_installer(installer)
    shutil.rmtree(BUILD, ignore_errors=True)  # temporary files are no longer needed
    if installer:
        print(f"\nDone: {installer}  ({installer.stat().st_size / 1e6:.0f} MB)")
