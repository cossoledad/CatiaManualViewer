"""Export ordinary CATIA VBScript macros; no registry or CATIA installation changes."""

from pathlib import Path

FEATURE_TOPICS = {
    "pad": "online/prtug_C2/prtugbt0501.htm",
    "pocket": "online/prtug_C2/prtugbt0502.htm",
}
WORKBENCH_MANUALS = {
    "prtcfg": "prtug_C2",
    "partdesign": "prtug_C2",
    "part design": "prtug_C2",
    "assembly": "asmug_C2",
    "assemblydesign": "asmug_C2",
    "sketcher": "skter_C2",
    "generativeshapedesign": "cfyug_C2",
}


def vb_string(value: str) -> str:
    """ASCII VBScript expression; CATIA B33 does not reliably load UTF-16 catvbs files."""
    parts, current = [], ""
    for index in range(0, len(encoded := value.encode("utf-16-le")), 2):
        code = int.from_bytes(encoded[index : index + 2], "little")
        if 32 <= code <= 126:
            current += chr(code).replace('"', '""')
        else:
            if current:
                parts.append('"' + current + '"')
                current = ""
            parts.append(f"ChrW({code if code < 32768 else code - 65536})")
    if current:
        parts.append('"' + current + '"')
    lines = [" & ".join(parts[start : start + 8]) for start in range(0, len(parts), 8)]
    return " & _\n      ".join(lines) or '""'


def export_help_macros(executable: Path, destination: Path) -> list[Path]:
    executable = executable.resolve()
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("请选择便携版 CatiaManualViewer.exe")
    if any(c in str(executable) for c in ('"', "\r", "\n")):
        raise ValueError("程序路径包含不可用于宏的字符")
    destination.mkdir(parents=True, exist_ok=True)
    prefix = (
        'Language="VBSCRIPT"\n\n'
        "Function Quoted(value)\n"
        '  Quoted = Chr(34) & Replace(Replace(CStr(value), Chr(34), ""), vbCrLf, " ") & Chr(34)\n'
        "End Function\n\n"
        "Sub CATMain()\n"
        f"  Dim viewer\n  viewer = {vb_string(str(executable))}\n"
        '  If Not CreateObject("Scripting.FileSystemObject").FileExists(viewer) Then\n'
        '    MsgBox "Viewer was moved. Export the help macros again from Catia Manual Viewer."\n'
        "    Exit Sub\n  End If\n"
    )
    general = prefix + (
        '  Dim wb, feature, doc, selection, command\n  wb = ""\n  feature = ""\n'
        "  On Error Resume Next\n  wb = CATIA.GetWorkbenchId()\n"
        "  Set doc = CATIA.ActiveDocument\n  Set selection = doc.Selection\n"
        "  If selection.Count2 > 0 Then feature = TypeName(selection.Item2(1).Value)\n"
        "  Err.Clear\n  On Error GoTo 0\n"
        '  command = Quoted(viewer) & " --help-workbench " & Quoted(wb)'
        ' & " --help-feature " & Quoted(feature)\n'
        '  CreateObject("WScript.Shell").Run command, 1, False\nEnd Sub\n'
    )
    files = {"ManualViewer.catvbs": general}
    for feature, target in FEATURE_TOPICS.items():
        files[feature.title() + "Help.catvbs"] = prefix + (
            f'  CreateObject("WScript.Shell").Run Quoted(viewer) & " --open {target}", 1, False\n'
            "End Sub\n"
        )
    result = []
    for name, value in files.items():
        path = destination / name
        # Encode non-ASCII paths via ChrW, so both the CATIA loader and Windows retain them.
        path.write_text(value, encoding="ascii")
        result.append(path)
    return result


def resolve_help_context(root, catalog, workbench="", feature=""):
    """A selected feature is context, not CATIA's currently active command."""
    target = FEATURE_TOPICS.get(feature.casefold())
    if target and (root / target).is_file():
        return {"open": target}
    manual = WORKBENCH_MANUALS.get(workbench.casefold())
    if manual and manual in catalog.manuals:
        return {"manual": manual, "search": feature}
    return {"search": feature} if feature else {}
