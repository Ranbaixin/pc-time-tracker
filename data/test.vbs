Set ws = CreateObject("Wscript.Shell")
ws.CurrentDirectory = "C:\Users\Ran-xin\pc-time-tracker"
ws.Run "cmd /c cd /d C:\Users\Ran-xin\pc-time-tracker && F:\PY\pythonw.exe -m src.main run", 0
