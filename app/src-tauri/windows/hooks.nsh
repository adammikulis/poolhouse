; What the Windows installer does beyond copying files (tauri.conf.json, bundle.windows.nsis.installerHooks).
;
; Install over an older copy: the node supervisor and the daemon run from files in $INSTDIR that cannot be replaced while
; they run, so the node is asked to stop and what still runs from the install is ended before anything is written.
;
; The firewall: Windows blocks inbound connections until a rule allows them, and only an administrator adds one. The rules
; are exactly poolhouse.lan.RULES, for private networks only. Windows asks once; declined, the install goes on and this
; computer still reaches its pool by connecting out. A silent install adds them only where it is already an administrator's.

!macro NSIS_HOOK_PREINSTALL
  IfFileExists "$INSTDIR\poolhouse-headless.exe" 0 +2
    nsExec::Exec '"$INSTDIR\poolhouse-headless.exe" -m poolhouse.node_launch stop'
  nsExec::Exec 'taskkill /F /T /IM poolhouse-headless.exe'
!macroend

!macro PoolhouseRules COMMANDS
  FileOpen $0 "$PLUGINSDIR\poolhouse-firewall.cmd" w
  FileWrite $0 "${COMMANDS}"
  FileClose $0
  UserInfo::GetAccountType
  Pop $1
  ${If} $1 == "Admin"
    nsExec::Exec '"$SYSDIR\cmd.exe" /c "$PLUGINSDIR\poolhouse-firewall.cmd"'
    Pop $1
  ${ElseIfNot} ${Silent}
    ExecShellWait "runas" "$SYSDIR\cmd.exe" '/c "$PLUGINSDIR\poolhouse-firewall.cmd"' SW_HIDE
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  InitPluginsDir
  !insertmacro PoolhouseRules 'netsh advfirewall firewall delete rule name="Poolhouse"$\r$\n\
netsh advfirewall firewall add rule name="Poolhouse" dir=in action=allow profile=private protocol=TCP localport=8770$\r$\n\
netsh advfirewall firewall delete rule name="Poolhouse node"$\r$\n\
netsh advfirewall firewall add rule name="Poolhouse node" dir=in action=allow profile=private protocol=TCP localport=7447$\r$\n\
netsh advfirewall firewall delete rule name="Poolhouse node discovery"$\r$\n\
netsh advfirewall firewall add rule name="Poolhouse node discovery" dir=in action=allow profile=private protocol=UDP localport=7448$\r$\n\
netsh advfirewall firewall delete rule name="Poolhouse phones"$\r$\n\
netsh advfirewall firewall add rule name="Poolhouse phones" dir=in action=allow profile=private protocol=TCP localport=7450$\r$\n'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  IfFileExists "$INSTDIR\poolhouse-headless.exe" 0 +2
    nsExec::Exec '"$INSTDIR\poolhouse-headless.exe" -m poolhouse.node_launch stop'
  nsExec::Exec 'taskkill /F /T /IM poolhouse-headless.exe'
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    InitPluginsDir
    !insertmacro PoolhouseRules 'netsh advfirewall firewall delete rule name="Poolhouse"$\r$\n\
netsh advfirewall firewall delete rule name="Poolhouse node"$\r$\n\
netsh advfirewall firewall delete rule name="Poolhouse node discovery"$\r$\n\
netsh advfirewall firewall delete rule name="Poolhouse phones"$\r$\n'
  ${EndIf}
!macroend
