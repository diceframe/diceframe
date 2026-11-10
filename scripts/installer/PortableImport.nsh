; 从现有便携版导入存档、设置和 AI 配置（data\）。
;
; - 只在全新安装时出现：目标位置已有 data\ 时跳过（包括「安装到原便携版目录」的原地转换，
;   那种情况本身就是覆盖升级，数据原地不动），绝不覆盖已有数据。
; - 复制而不是移动：原来的便携版保持原样，确认安装版正常后由用户自己删除。
; - 跳过 data\_updater\（版本指针、下载包和解压残留，都指向旧目录）与 data\_launcher\
;   （应用窗口的 WebView2 副本和浏览数据，会按需重建）。
; - 命令行 /IMPORT=<便携版目录> 用于静默安装（/S）时指定导入来源。
;
; 使用方：在 MUI_LANGUAGE 之后 !include 本文件；页面序列里放
;   Page custom ImportPageShow ImportPageLeave
; .onInit 里 Call ImportParseCommandLine；安装区段在程序文件就位后 Call ImportPortableData。

!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

Var ImportDir
Var ImportEnabled
Var ImportCheckbox
Var ImportDirField
Var ImportBrowseButton

LangString IMPORT_TITLE ${LANG_SIMPCHINESE} "从便携版导入"
LangString IMPORT_TITLE ${LANG_ENGLISH} "Import from the portable version"
LangString IMPORT_SUBTITLE ${LANG_SIMPCHINESE} "把现有便携版的存档、设置和 AI 配置带过来（可选）。"
LangString IMPORT_SUBTITLE ${LANG_ENGLISH} "Bring saves, settings and AI configuration over from a portable copy (optional)."
LangString IMPORT_DESC ${LANG_SIMPCHINESE} "如果你之前用的是便携版，选择它所在的文件夹（里面有 DiceFrame.exe 和 data 文件夹），安装程序会把其中的 data 复制过来。原来的便携版不会被改动，确认安装版正常后可以自行删除。"
LangString IMPORT_DESC ${LANG_ENGLISH} "If you used the portable version, choose its folder (it contains DiceFrame.exe and a data folder). Setup copies its data folder. The portable folder is left untouched; delete it yourself once the installed version works."
LangString IMPORT_CHECK ${LANG_SIMPCHINESE} "从现有便携版导入数据"
LangString IMPORT_CHECK ${LANG_ENGLISH} "Import data from an existing portable copy"
LangString IMPORT_BROWSE ${LANG_SIMPCHINESE} "浏览…"
LangString IMPORT_BROWSE ${LANG_ENGLISH} "Browse..."
LangString IMPORT_FOUND ${LANG_SIMPCHINESE} "已自动找到一个便携版，请确认路径是否正确。"
LangString IMPORT_FOUND ${LANG_ENGLISH} "A portable copy was found automatically. Check that the path is right."
LangString IMPORT_INVALID ${LANG_SIMPCHINESE} "这个文件夹不是 DiceFrame 便携版：里面需要有 DiceFrame.exe 和 data 文件夹。"
LangString IMPORT_INVALID ${LANG_ENGLISH} "This folder is not a DiceFrame portable copy: it must contain DiceFrame.exe and a data folder."
LangString IMPORT_RUNNING ${LANG_SIMPCHINESE} "要导入的便携版 DiceFrame 正在运行。请先关闭它，再点「重试」；点「取消」会跳过导入。"
LangString IMPORT_RUNNING ${LANG_ENGLISH} "The portable DiceFrame you are importing is running. Close it and click Retry, or click Cancel to skip the import."
LangString IMPORT_PROGRESS ${LANG_SIMPCHINESE} "正在从便携版导入数据……"
LangString IMPORT_PROGRESS ${LANG_ENGLISH} "Importing data from the portable copy..."
LangString IMPORT_SKIPPED_EXISTING ${LANG_SIMPCHINESE} "安装位置已有数据，跳过导入。"
LangString IMPORT_SKIPPED_EXISTING ${LANG_ENGLISH} "The install location already has data; import skipped."
LangString IMPORT_PARTIAL ${LANG_SIMPCHINESE} "有部分文件没能导入。原来的便携版没有改动，你可以把它 data 文件夹里缺的内容手动复制到：$\n$INSTDIR\data"
LangString IMPORT_PARTIAL ${LANG_ENGLISH} "Some files could not be imported. The portable copy is unchanged; you can copy what is missing from its data folder to:$\n$INSTDIR\data"

; 便携版：根目录同时有 DiceFrame.exe 和 data\。
!macro _ImportIsPortable DIR RESULT
  StrCpy ${RESULT} 0
  ${If} ${FileExists} "${DIR}\DiceFrame.exe"
  ${AndIf} ${FileExists} "${DIR}\data\*.*"
    StrCpy ${RESULT} 1
  ${EndIf}
!macroend

Function ImportParseCommandLine
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/IMPORT=" $R1
  ${IfNot} ${Errors}
  ${AndIf} $R1 != ""
    StrCpy $ImportDir $R1
    StrCpy $ImportEnabled 1
  ${EndIf}
FunctionEnd

; 在一个目录下两层以内找便携版（解压常见的两层同名目录也能找到）。栈顶：要找的目录。
Function ImportScanFolder
  Exch $R0
  Push $R1
  Push $R2
  Push $R3
  Push $R4
  Push $R5
  FindFirst $R1 $R2 "$R0\*"
  ${DoWhile} $R2 != ""
    ${If} $R2 != "."
    ${AndIf} $R2 != ".."
    ${AndIf} ${FileExists} "$R0\$R2\*.*"
      !insertmacro _ImportIsPortable "$R0\$R2" $R5
      ${If} $R5 == 1
        StrCpy $ImportDir "$R0\$R2"
        ${Break}
      ${EndIf}
      FindFirst $R3 $R4 "$R0\$R2\*"
      ${DoWhile} $R4 != ""
        ${If} $R4 != "."
        ${AndIf} $R4 != ".."
          !insertmacro _ImportIsPortable "$R0\$R2\$R4" $R5
          ${If} $R5 == 1
            StrCpy $ImportDir "$R0\$R2\$R4"
            ${Break}
          ${EndIf}
        ${EndIf}
        FindNext $R3 $R4
      ${Loop}
      FindClose $R3
      ${If} $ImportDir != ""
        ${Break}
      ${EndIf}
    ${EndIf}
    FindNext $R1 $R2
  ${Loop}
  FindClose $R1
  Pop $R5
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R1
  Pop $R0
FunctionEnd

; 在桌面、下载、文档里找一个便携版，找到就预填；找不到留空让用户自己选。
Function ImportDetectPortable
  StrCpy $ImportDir ""
  Push "$DESKTOP"
  Call ImportScanFolder
  ${If} $ImportDir == ""
    Push "$PROFILE\Downloads"
    Call ImportScanFolder
  ${EndIf}
  ${If} $ImportDir == ""
    Push "$DOCUMENTS"
    Call ImportScanFolder
  ${EndIf}
FunctionEnd

Function ImportPageShow
  ${If} ${FileExists} "$INSTDIR\data\*.*"
    StrCpy $ImportEnabled 0
    Abort
  ${EndIf}
  ${If} $ImportEnabled == ""
    Call ImportDetectPortable
    ${If} $ImportDir != ""
      StrCpy $ImportEnabled 1
    ${Else}
      StrCpy $ImportEnabled 0
    ${EndIf}
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "$(IMPORT_TITLE)" "$(IMPORT_SUBTITLE)"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 40u "$(IMPORT_DESC)"
  Pop $0
  ${NSD_CreateCheckbox} 0 46u 100% 12u "$(IMPORT_CHECK)"
  Pop $ImportCheckbox
  ${NSD_CreateDirRequest} 0 64u 75% 13u "$ImportDir"
  Pop $ImportDirField
  ${NSD_CreateBrowseButton} 77% 63u 23% 15u "$(IMPORT_BROWSE)"
  Pop $ImportBrowseButton
  ${NSD_OnClick} $ImportCheckbox ImportOnToggle
  ${NSD_OnClick} $ImportBrowseButton ImportOnBrowse
  ${If} $ImportEnabled == 1
    ${NSD_Check} $ImportCheckbox
    ${If} $ImportDir != ""
      ${NSD_CreateLabel} 0 86u 100% 24u "$(IMPORT_FOUND)"
      Pop $0
    ${EndIf}
  ${Else}
    EnableWindow $ImportDirField 0
    EnableWindow $ImportBrowseButton 0
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function ImportOnToggle
  Pop $0
  ${NSD_GetState} $ImportCheckbox $1
  ${If} $1 == ${BST_CHECKED}
    EnableWindow $ImportDirField 1
    EnableWindow $ImportBrowseButton 1
  ${Else}
    EnableWindow $ImportDirField 0
    EnableWindow $ImportBrowseButton 0
  ${EndIf}
FunctionEnd

Function ImportOnBrowse
  Pop $0
  ${NSD_GetText} $ImportDirField $1
  nsDialogs::SelectFolderDialog "$(IMPORT_CHECK)" "$1"
  Pop $1
  ${If} $1 != error
  ${AndIf} $1 != ""
    ${NSD_SetText} $ImportDirField $1
  ${EndIf}
FunctionEnd

Function ImportPageLeave
  ${NSD_GetState} $ImportCheckbox $0
  ${NSD_GetText} $ImportDirField $ImportDir
  ${If} $0 != ${BST_CHECKED}
    StrCpy $ImportEnabled 0
    Return
  ${EndIf}
  !insertmacro _ImportIsPortable "$ImportDir" $1
  ${If} $1 != 1
    MessageBox MB_OK|MB_ICONEXCLAMATION "$(IMPORT_INVALID)"
    Abort
  ${EndIf}
  StrCpy $ImportEnabled 1
FunctionEnd

Function ImportPortableData
  ${If} $ImportEnabled != 1
    Return
  ${EndIf}
  !insertmacro _ImportIsPortable "$ImportDir" $0
  ${If} $0 != 1
    DetailPrint "$(IMPORT_INVALID)"
    Return
  ${EndIf}
  ${If} ${FileExists} "$INSTDIR\data\*.*"
    DetailPrint "$(IMPORT_SKIPPED_EXISTING)"
    Return
  ${EndIf}
  ; 运行中的便携版可能还有没写完的数据库：先等它关掉，否则跳过导入。
  retry:
  ClearErrors
  FileOpen $0 "$ImportDir\DiceFrame.exe" a
  ${If} ${Errors}
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(IMPORT_RUNNING)" /SD IDCANCEL IDRETRY retry
    Return
  ${EndIf}
  FileClose $0
  DetailPrint "$(IMPORT_PROGRESS)"
  CreateDirectory "$INSTDIR\data"
  StrCpy $2 0
  FindFirst $0 $1 "$ImportDir\data\*"
  ${DoWhile} $1 != ""
    ${If} $1 != "."
    ${AndIf} $1 != ".."
    ${AndIf} $1 != "_updater"
    ${AndIf} $1 != "_launcher"
      ClearErrors
      CopyFiles /SILENT "$ImportDir\data\$1" "$INSTDIR\data"
      ${If} ${Errors}
        IntOp $2 $2 + 1
        DetailPrint "  ! $1"
      ${EndIf}
    ${EndIf}
    FindNext $0 $1
  ${Loop}
  FindClose $0
  ${If} $2 > 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "$(IMPORT_PARTIAL)" /SD IDOK
  ${EndIf}
FunctionEnd
