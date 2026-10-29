"""Developer QA: execute a read-only probe or exported help macros in an existing CATIA."""

import argparse
import json
from pathlib import Path

import win32com.client

from catia_manual.integrations.help_macro import export_help_macros

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--executable", type=Path)
parser.add_argument("--macro", default="PadHelp.catvbs")
parser.add_argument("--inspect-context", action="store_true")
args = parser.parse_args()
folder = Path(".cache/catia-macro-probe").resolve()
folder.mkdir(parents=True, exist_ok=True)
application = win32com.client.GetActiveObject("CATIA.Application")
parameters = ()
if args.executable:
    export_help_macros(args.executable, folder)
    if args.inspect_context:
        script = (folder / "ManualViewer.catvbs").read_text("ascii")
        script = script.replace("Sub CATMain()", "Function Probe()")
        script = script.replace("Exit Sub", "Exit Function").replace("End Sub", "End Function")
        script = script.replace(
            'CreateObject("WScript.Shell").Run command, 1, False', "Probe = command"
        )
        (folder / "ContextProbe.catvbs").write_text(script, "ascii")
        result = application.SystemService.ExecuteScript(
            str(folder), 1, "ContextProbe.catvbs", "Probe", parameters
        )
    else:
        result = application.SystemService.ExecuteScript(
            str(folder), 1, args.macro, "CATMain", parameters
        )
else:
    (folder / "Probe.catvbs").write_text(
        'Language="VBSCRIPT"\nFunction Probe()\n'
        '  Probe = "CMV:" & CATIA.GetWorkbenchId()\nEnd Function\n',
        encoding="utf-8",
    )
    result = application.SystemService.ExecuteScript(
        str(folder), 1, "Probe.catvbs", "Probe", parameters
    )
print(json.dumps({"result": result, "workbench": application.GetWorkbenchId()}, ensure_ascii=False))
