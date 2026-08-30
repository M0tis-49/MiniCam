Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

folder = files.GetParentFolderName(WScript.ScriptFullName)
pythonw = folder & "\.venv\Scripts\pythonw.exe"
script = folder & "\minicam.py"

If files.FileExists(pythonw) Then
    shell.Run """" & pythonw & """ """ & script & """", 0, False
Else
    MsgBox "MiniCam n'est pas encore installe." & vbCrLf & _
        "Lance install.bat une premiere fois, puis relance MiniCam.vbs.", _
        vbExclamation, "MiniCam"
End If
