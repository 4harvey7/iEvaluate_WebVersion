"""Server-side folder checks: website folders must be full paths (no shortcuts),
and the server copy of the rules matches the watcher's copy."""
import pytest

from app import folder_safety as fs
from app.folder_safety import MESSAGES, check_folder_for_server
from tests.test_watcher_client import fw


@pytest.mark.parametrize("raw,code", [
    ("", "empty"),
    ("Scans", "relative"),
    (r"%USERPROFILE%\Scans", "variables"),
    (r"~\Scans", "variables"),
    ("~/Scans", "variables"),
    (r"%OneDrive%\Scans", "variables"),
    (r"%FOO%\Scans", "variables"),
    ("C:\\", "drive_root"),
    (r"C:\Windows", "system"),
    (r"C:\Users\bob\Desktop", "too_big"),
])
def test_refused_folders(raw, code):
    _, problem = check_folder_for_server(raw)
    assert problem == MESSAGES[code]


@pytest.mark.parametrize("raw,expected", [
    (r"C:\Scans", "C:/Scans"),
    (' "c:' + chr(92) + 'scan' + chr(92) + '" ', "C:/scan"),
    (r"C:\Users\bob\Desktop\Scans", "C:/Users/bob/Desktop/Scans"),
])
def test_accepted_folders(raw, expected):
    assert check_folder_for_server(raw) == (expected, None)


PATHS = ["", "Scans", r"%USERPROFILE%\Scans", "~/Scans", r"C:\Scans", "C:/", r"C:\Windows\Temp",
         r"C:\Users\bob", r"C:\Users\bob\Documents", r"C:\Users\bob\Documents\Scans", r"\server\share",
         r"\server\share\scans", "D:Scans"]


@pytest.mark.parametrize("p", PATHS)
def test_server_and_watcher_rules_match(p):
    assert fs.folder_problem(p) == fw.folder_problem(p)
    assert fs.uses_shortcut(p) == fw.uses_shortcut(p)
