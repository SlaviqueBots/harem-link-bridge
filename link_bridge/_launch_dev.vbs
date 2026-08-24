Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\\Users\\Professional\\Projects\\slavique-harem-bot"
' Kill prior copies, then start one DEV GUI (no console).
sh.Run """C:\\Users\\Professional\\Projects\\slavique-harem-bot\\venv\\Scripts\\pythonw.exe"" scripts\\relaunch_bridge_dev.py", 0, False
