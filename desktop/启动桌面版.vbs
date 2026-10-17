Option Explicit
' Start the PDF translation desktop app (native WebView2 window, no console).
Dim fso, shell, here, pythonw, pythonExe, app
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
app = here & "\desktop_app.py"
pythonw = here & "\..\.venv\Scripts\pythonw.exe"
pythonExe = here & "\..\.venv\Scripts\python.exe"
If Not fso.FileExists(pythonw) Then
  If fso.FileExists(pythonExe) Then
    pythonw = pythonExe
  Else
    MsgBox "Virtual environment not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Please follow the install steps in the usage guide first.", 16, "PDF Translation"
    WScript.Quit 1
  End If
End If
shell.CurrentDirectory = here
shell.Run """" & pythonw & """ """ & app & """", 0, False
