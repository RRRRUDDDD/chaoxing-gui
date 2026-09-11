!ifndef CHAOXING_PATH_GUARD_VERSION
!define CHAOXING_PATH_GUARD_VERSION 1
!include LogicLib.nsh
!include FileFunc.nsh

Var ChaoxingGuardRoot
Var ChaoxingGuardReportedPath
Var ChaoxingGuardTreeEntries

; Stack arguments: path, then kind (root/directory/file). Non-root paths must
; be relative to a successfully checked $INSTDIR. Preserve caller registers.
; These are preflight checks, not locks against concurrent junction swaps.
!macro ChaoxingPathGuardFunctions PREFIX
Function ${PREFIX}ChaoxingCheckPath
  Exch $1
  Exch
  Exch $0
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  StrCpy $ChaoxingGuardReportedPath $0

  ; Reject values that could have been truncated by NSIS before validation.
  StrLen $8 $0
  IntOp $9 ${NSIS_MAX_STRLEN} - 1
  ${If} $8 = 0
  ${OrIf} $8 >= $9
    Goto path_rejected
  ${EndIf}

  ${If} $1 == "root"
    StrCpy $ChaoxingGuardRoot ""
    ; A local, absolute drive path only: no UNC, device namespace or drive root.
    System::Call 'shlwapi::PathGetDriveNumberW(w r0) i.r5'
    StrCpy $4 $0 1 2
    ${If} $5 < 0
    ${OrIf} $8 <= 3
      Goto path_rejected
    ${EndIf}
    ${If} $4 != "\"
    ${AndIf} $4 != "/"
      Goto path_rejected
    ${EndIf}
    StrCpy $6 $0 2
    StrCpy $4 "$6\"
    System::Call 'kernel32::GetDriveTypeW(w r4) i.r5'
    ${If} $5 != 2 ; DRIVE_REMOVABLE
    ${AndIf} $5 != 3 ; DRIVE_FIXED
    ${AndIf} $5 != 6 ; DRIVE_RAMDISK
      Goto path_rejected
    ${EndIf}
    StrCpy $7 3
  ${Else}
    ${If} $1 != "directory"
    ${AndIf} $1 != "file"
      Goto path_rejected
    ${EndIf}
    ${If} $ChaoxingGuardRoot == ""
    ${OrIf} $ChaoxingGuardRoot != $INSTDIR
      Goto path_rejected
    ${EndIf}
    StrCpy $4 $0 1
    ${If} $4 == "\"
    ${OrIf} $4 == "/"
      Goto path_rejected
    ${EndIf}
    ${If} $1 == "file"
      StrCpy $4 $0 1 -1
      ${If} $4 == "\"
      ${OrIf} $4 == "/"
        Goto path_rejected
      ${EndIf}
    ${EndIf}
    StrCpy $6 $ChaoxingGuardRoot
    StrCpy $7 0
  ${EndIf}

  ; Validate every original component before canonicalization can hide '..',
  ; trailing dots/spaces, ADS or DOS devices. Both Windows separators and the
  ; repeated separators emitted by the upstream template are normalized.
  StrCpy $3 ""
  component_character:
    StrCpy $4 $0 1 $7
    StrCmp $4 "" component_end
    StrCmp $4 "\" component_end
    StrCmp $4 "/" component_end
    StrCpy $3 "$3$4"
    Goto component_next

  component_end:
    StrCmp $3 "" component_next
    StrLen $5 $3
    ${If} $5 > 255
    ${OrIf} $3 == "."
    ${OrIf} $3 == ".."
      Goto path_rejected
    ${EndIf}
    StrCpy $4 $3 1 -1
    ${If} $4 == "."
    ${OrIf} $4 == " "
      Goto path_rejected
    ${EndIf}
    ; PathCleanupSpec reports any replacement of forbidden filename characters
    ; (including control characters). Accept only an unchanged component.
    System::Call 'shell32::PathCleanupSpec(p 0, w r3) i.r5'
    ${If} $5 != 0
      Goto path_rejected
    ${EndIf}

    ; Device names remain reserved with an extension. Compare the stem without
    ; spaces before its first dot, including Windows' superscript COM/LPT digits.
    StrCpy $2 ""
    StrCpy $5 0
    device_stem:
      StrCpy $4 $3 1 $5
      StrCmp $4 "" device_trim
      StrCmp $4 "." device_trim
      StrCpy $2 "$2$4"
      IntOp $5 $5 + 1
      Goto device_stem
    device_trim:
      StrCpy $4 $2 1 -1
      ${If} $4 == " "
        StrCpy $2 $2 -1
        Goto device_trim
      ${EndIf}
    ${If} $2 == "CON"
    ${OrIf} $2 == "PRN"
    ${OrIf} $2 == "AUX"
    ${OrIf} $2 == "NUL"
    ${OrIf} $2 == "CONIN$$"
    ${OrIf} $2 == "CONOUT$$"
    ${OrIf} $2 == "CLOCK$$"
      Goto path_rejected
    ${EndIf}
    StrLen $5 $2
    ${If} $5 = 4
      StrCpy $4 $2 3
      ${If} $4 == "COM"
      ${OrIf} $4 == "LPT"
        StrCpy $4 $2 1 3
        System::Call 'shlwapi::StrSpnW(w r4, w "0123456789¹²³") i.r5'
        ${If} $5 != 0
          Goto path_rejected
        ${EndIf}
      ${EndIf}
    ${EndIf}

    ; Check before concatenating, so the checked destination cannot silently
    ; differ from the path used by File, Delete, CreateDirectory or WriteUninstaller.
    StrLen $2 $6
    StrLen $5 $3
    IntOp $2 $2 + $5
    IntOp $2 $2 + 1
    ${If} $2 >= $9
      Goto path_rejected
    ${EndIf}
    StrCpy $6 "$6\$3"
    StrCpy $3 ""
  component_next:
    IntOp $7 $7 + 1
    ${If} $7 <= $8
      Goto component_character
    ${EndIf}

  StrLen $2 $6
  ${If} $2 <= 3
    Goto path_rejected
  ${EndIf}
  System::Call 'kernel32::GetFullPathNameW(w r6, i ${NSIS_MAX_STRLEN}, w .r0, p 0) i.r5'
  ${If} $5 = 0
  ${OrIf} $5 >= $9
  ${OrIf} $0 != $6
    Goto path_rejected
  ${EndIf}
  ${If} $1 != "root"
    StrLen $2 $ChaoxingGuardRoot
    StrCpy $3 $0 $2
    StrCpy $4 $0 1 $2
    ${If} $3 != $ChaoxingGuardRoot
    ${OrIf} $4 != "\"
      Goto path_rejected
    ${EndIf}
  ${EndIf}

  ; Walk from the destination all the way to the drive root. Reading attributes
  ; on a missing leaf is not enough: any existing ancestor may be a junction.
  StrCpy $8 $1
  path_attributes:
    System::Call 'kernel32::GetFileAttributesW(w r0) i.r2 ?e'
    Pop $3
    ${If} $2 = -1
      ; Only file/path-not-found can mean a directory we will create later.
      ; Access denied, invalid names and all other errors are a refusal.
      ${If} $3 != 2
      ${AndIf} $3 != 3
        Goto path_rejected
      ${EndIf}
      StrLen $4 $0
      ${If} $4 <= 3
        Goto path_rejected
      ${EndIf}
    ${Else}
      IntOp $4 $2 & 0x400 ; FILE_ATTRIBUTE_REPARSE_POINT
      ${If} $4 != 0
        Goto path_rejected
      ${EndIf}
      IntOp $4 $2 & 0x10 ; FILE_ATTRIBUTE_DIRECTORY
      ${If} $1 == "file"
        ${If} $4 != 0
          Goto path_rejected
        ${EndIf}
      ${Else}
        ${If} $4 = 0
          Goto path_rejected
        ${EndIf}
      ${EndIf}
    ${EndIf}
    StrLen $4 $0
    ${If} $4 > 3
      ${GetParent} "$0" $0
      StrLen $5 $0
      ${If} $5 >= $4
      ${OrIf} $5 < 2
        Goto path_rejected
      ${EndIf}
      ${If} $5 = 2
        StrCpy $0 "$0\"
      ${EndIf}
      StrCpy $1 "directory"
      Goto path_attributes
    ${EndIf}

  ${If} $8 == "root"
    StrCpy $ChaoxingGuardRoot $6
    StrCpy $INSTDIR $6
  ${EndIf}
  Pop $9
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $0
  Pop $1
  Return

  path_rejected:
    Call ${PREFIX}ChaoxingRejectPath
FunctionEnd

Function ${PREFIX}ChaoxingRejectPath
    MessageBox MB_OK|MB_ICONSTOP "安装或卸载已停止：路径无效、不可检查，或包含目录联接/符号链接。请选择普通的本地安装目录。$\r$\nInstallation/uninstallation refused an invalid, inaccessible or redirected path:$\r$\n$ChaoxingGuardReportedPath" /SD IDOK
    SetErrorLevel 2
    Quit
FunctionEnd
!macroend

!insertmacro ChaoxingPathGuardFunctions ""
!insertmacro ChaoxingPathGuardFunctions "un."

; Older uninstallers may delete files absent from this version's manifest.
; Inspect their entire existing program tree before invoking them. Keep the
; scan bounded and never descend into a reparse point. Business AppData is not
; traversed. Depth/count limits fail closed rather than leaving a partial scan.
Function ChaoxingCheckLegacyInstallTree
  Push "$INSTDIR"
  Push "root"
  Call ChaoxingCheckPath
  StrCpy $ChaoxingGuardTreeEntries 0
  Push ""
  Push 0
  Call ChaoxingCheckTreeDirectory
FunctionEnd

; Stack arguments: relative directory (empty for root), then depth.
Function ChaoxingCheckTreeDirectory
  Exch $1
  Exch
  Exch $0
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  ${If} $1 > 64
    Goto tree_rejected
  ${EndIf}
  StrCpy $8 $ChaoxingGuardRoot
  ${If} $0 != ""
    Push "$0"
    Push "directory"
    Call ChaoxingCheckPath
    StrCpy $8 "$ChaoxingGuardRoot\$0"
  ${EndIf}
  StrCpy $ChaoxingGuardReportedPath $8
  StrLen $9 $8
  IntOp $9 $9 + 3 ; separator, wildcard and terminating NUL
  ${If} $9 >= ${NSIS_MAX_STRLEN}
    Goto tree_rejected
  ${EndIf}

  ; WIN32_FIND_DATAW: eleven DWORDs then WCHAR cFileName[MAX_PATH].
  System::Alloc 592
  Pop $2
  ${If} $2 = 0
    Goto tree_rejected
  ${EndIf}
  System::Call 'kernel32::FindFirstFileW(w "$8\*", p r2) p.r3 ?e'
  Pop $7
  ${If} $3 = -1
    System::Free $2
    ${If} $7 = 2 ; no matching entries in an empty directory
      Goto tree_done
    ${EndIf}
    Goto tree_rejected
  ${EndIf}
  tree_entry:
    System::Call '*$2(i .r4, i, i, i, i, i, i, i, i, i, i, &w260 .r5)'
    StrCmp $5 "." tree_next
    StrCmp $5 ".." tree_next
    StrCpy $ChaoxingGuardReportedPath "$8\$5"
    IntOp $ChaoxingGuardTreeEntries $ChaoxingGuardTreeEntries + 1
    ${If} $ChaoxingGuardTreeEntries > 65536
      Goto tree_rejected
    ${EndIf}
    IntOp $7 $4 & 0x400
    ${If} $7 != 0
      Goto tree_rejected
    ${EndIf}
    StrLen $7 $0
    StrLen $9 $5
    IntOp $9 $9 + $7
    IntOp $9 $9 + 2
    ${If} $9 >= ${NSIS_MAX_STRLEN}
      Goto tree_rejected
    ${EndIf}
    StrCpy $6 $5
    ${If} $0 != ""
      StrCpy $6 "$0\$5"
    ${EndIf}
    IntOp $7 $4 & 0x10
    ${If} $7 != 0
      IntOp $7 $1 + 1
      Push "$6"
      Push $7
      Call ChaoxingCheckTreeDirectory
    ${Else}
      Push "$6"
      Push "file"
      Call ChaoxingCheckPath
    ${EndIf}
  tree_next:
    System::Call 'kernel32::FindNextFileW(p r3, p r2) i.r4 ?e'
    Pop $7
    ${If} $4 != 0
      Goto tree_entry
    ${EndIf}
    ${If} $7 != 18 ; ERROR_NO_MORE_FILES is the only successful end of scan
      Goto tree_rejected
    ${EndIf}
    System::Call 'kernel32::FindClose(p r3) i.r4'
    System::Free $2
    ${If} $4 = 0
      Goto tree_rejected
    ${EndIf}
  tree_done:
    Pop $9
    Pop $8
    Pop $7
    Pop $6
    Pop $5
    Pop $4
    Pop $3
    Pop $2
    Pop $0
    Pop $1
    Return
  tree_rejected:
    ; Quit closes any outstanding enumeration handles and buffers in this
    ; process; there is no fallthrough to the previous uninstaller.
    Call ChaoxingRejectPath
FunctionEnd

Function ChaoxingRefuseMsiMigration
  MessageBox MB_OK|MB_ICONSTOP "检测到旧 MSI 安装。本安装包无法安全自动迁移 MSI。请先在 Windows 设置中卸载旧 MSI 版本，再运行此安装包。$\r$\nAutomatic MSI migration is not supported. Uninstall the previous MSI version in Windows Settings before running this installer." /SD IDOK
  SetErrorLevel 2
  Quit
FunctionEnd

; A manually selected path must not turn the coexistence build into an
; in-place Electron upgrade. Each installer owns its own program directory.
Function ChaoxingCheckElectronDirectory
  IfFileExists "$INSTDIR\chaoxing-gui.exe" legacy_electron_directory
  IfFileExists "$INSTDIR\resources\app.asar" legacy_electron_directory
  Goto tauri_directory_ready
  legacy_electron_directory:
    MessageBox MB_OK|MB_ICONSTOP "此目录包含旧版 Electron 程序。请选择新的 Tauri 安装目录，原程序与数据将保留。$\r$\nThis directory contains the Electron app. Choose a separate Tauri installation directory." /SD IDOK
    SetErrorLevel 2
    Abort
  tauri_directory_ready:
FunctionEnd

!macro NSIS_HOOK_PREINSTALL
  Call ChaoxingValidateInstallPaths
  Call ChaoxingCheckElectronDirectory
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  Call un.ChaoxingValidateInstallPaths
!macroend
!endif
