; DiceFrame Windows 安装程序（NSIS）。
; 按用户安装到 %LOCALAPPDATA%\Programs\DiceFrame，不需要管理员权限：目录用户自己可写，
; 应用内更新（versions/ 并排安装 + 健康检查 + 回滚）照常工作，路径也足够短。
; 安装内容与便携版完全相同：根目录 DiceFrame.exe + app\ + python\。
; 已有安装再次运行新版安装程序 = 覆盖升级，由启动器接管（OverwriteUpgrade.cs）。
; 全新安装时可以从现有便携版导入 data\（PortableImport.nsh）。
;
; 由 scripts/build_installer.py 调用：
; makensis -INPUTCHARSET UTF8 -DVERSION=x.y.z[-pre] -DNUMERIC_VERSION=x.y.z -DPAYLOAD=<目录>
;          -DICON=<ico> -DOUTFILE=<exe> DiceFrame.nsi

Unicode true
ManifestDPIAware true
!addincludedir "${__FILEDIR__}"
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !error "VERSION is required"
!endif
; 文件版本信息只接受纯数字 x.y.z（预发布后缀由构建脚本去掉）。
!ifndef NUMERIC_VERSION
  !define NUMERIC_VERSION "${VERSION}"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD (directory with DiceFrame.exe, app\, python\) is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "DiceFrame-v${VERSION}-windows-setup.exe"
!endif

!define APP_NAME "DiceFrame"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\DiceFrame"

Name "${APP_NAME}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\DiceFrame"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "DiceFrame ${VERSION}"
VIProductVersion "${NUMERIC_VERSION}.0"
VIAddVersionKey /LANG=0 "ProductName" "DiceFrame"
VIAddVersionKey /LANG=0 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=0 "FileVersion" "${NUMERIC_VERSION}"
VIAddVersionKey /LANG=0 "FileDescription" "DiceFrame Setup"
VIAddVersionKey /LANG=0 "LegalCopyright" "DiceFrame contributors"

!ifdef ICON
  !define MUI_ICON "${ICON}"
  !define MUI_UNICON "${ICON}"
!endif
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\DiceFrame.exe"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom ImportPageShow ImportPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"
!include "PortableImport.nsh"

Function .onInit
  Call ImportParseCommandLine
FunctionEnd

LangString MSG_RUNNING ${LANG_SIMPCHINESE} "DiceFrame 正在运行。请先关闭 DiceFrame 窗口和控制台窗口，再点「重试」。"
LangString MSG_RUNNING ${LANG_ENGLISH} "DiceFrame is running. Close the DiceFrame window and its console window, then click Retry."
LangString MSG_DELETE_DATA ${LANG_SIMPCHINESE} "是否同时删除存档、设置和 AI 配置（data 文件夹）？$\n$\n选「否」会保留它们，以后重新安装可以继续使用。"
LangString MSG_DELETE_DATA ${LANG_ENGLISH} "Also delete saves, settings and AI configuration (the data folder)?$\n$\nChoose No to keep them for a future reinstall."

; 运行中的 DiceFrame.exe 无法以写方式打开：据此判断，提示用户先关掉再重试。
!macro WaitUntilClosed UN
  Function ${UN}WaitUntilClosed
    retry:
    ${If} ${FileExists} "$INSTDIR\DiceFrame.exe"
      ClearErrors
      FileOpen $0 "$INSTDIR\DiceFrame.exe" a
      ${If} ${Errors}
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(MSG_RUNNING)" /SD IDCANCEL IDRETRY retry
        Abort
      ${EndIf}
      FileClose $0
    ${EndIf}
  FunctionEnd
!macroend
!insertmacro WaitUntilClosed ""
!insertmacro WaitUntilClosed "un."

Section "DiceFrame"
  Call WaitUntilClosed
  SetOutPath "$INSTDIR"
  ; 先清掉根目录旧的程序文件再放新的，免得旧依赖残留与新版混在一起。data\、logs\、versions\ 不动。
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  File "${PAYLOAD}\DiceFrame.exe"
  File /r "${PAYLOAD}\app"
  File /r "${PAYLOAD}\python"
  Call ImportPortableData
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateShortcut "$SMPROGRAMS\DiceFrame.lnk" "$INSTDIR\DiceFrame.exe"
  CreateShortcut "$DESKTOP\DiceFrame.lnk" "$INSTDIR\DiceFrame.exe"

  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "DiceFrame"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "DiceFrame"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\DiceFrame.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" $0
SectionEnd

Section "Uninstall"
  Call un.WaitUntilClosed
  Delete "$SMPROGRAMS\DiceFrame.lnk"
  Delete "$DESKTOP\DiceFrame.lnk"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\versions"
  RMDir /r "$INSTDIR\logs"
  Delete "$INSTDIR\DiceFrame.exe"
  Delete "$INSTDIR\DiceFrame.exe.old"
  Delete "$INSTDIR\Uninstall.exe"
  ; 存档与设置默认保留；静默卸载（/S）也保留。
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "$(MSG_DELETE_DATA)" /SD IDNO IDNO keep_data
    RMDir /r "$INSTDIR\data"
  keep_data:
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
