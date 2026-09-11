; Dedicated path-check fixture. Never embeds or starts an application/backend.
Unicode true
RequestExecutionLevel user
SilentInstall silent
SilentUnInstall silent
Name "Chaoxing NSIS path guard fixture"
OutFile "${FIXTURE_OUTPUT}"
!include LogicLib.nsh
!include FileFunc.nsh
!define MAINBINARYNAME "fixture-host"
!include "${FIXTURE_HOOKS}"
!include "${FIXTURE_PATH_TABLE}"
Var FixtureExtraPath
Var FixtureCheckOnly
Var FixtureLegacyTree
Var FixtureMsiMigration

Function ChaoxingValidateInstallPaths
  !insertmacro ChaoxingPayloadPathChecks ""
FunctionEnd

Function un.ChaoxingValidateInstallPaths
  !insertmacro ChaoxingPayloadPathChecks "un."
FunctionEnd

Function .onInit
  ReadEnvStr $0 "CHAOXING_NSIS_FIXTURE_BOOTSTRAP"
  ${If} $0 == "1"
    FileOpen $0 "$EXEDIR\string-limit.txt" w
    FileWrite $0 "${NSIS_MAX_STRLEN}"
    FileClose $0
    WriteUninstaller "$EXEDIR\guard-uninstall.exe"
    SetErrorLevel 0
    Quit
  ${EndIf}
  ReadEnvStr $INSTDIR "CHAOXING_NSIS_FIXTURE_ROOT"
  ReadEnvStr $FixtureExtraPath "CHAOXING_NSIS_FIXTURE_EXTRA"
  ReadEnvStr $FixtureCheckOnly "CHAOXING_NSIS_FIXTURE_CHECK_ONLY"
  ReadEnvStr $FixtureLegacyTree "CHAOXING_NSIS_FIXTURE_LEGACY_TREE"
  ReadEnvStr $FixtureMsiMigration "CHAOXING_NSIS_FIXTURE_MSI"
FunctionEnd

Function un.onInit
  ReadEnvStr $INSTDIR "CHAOXING_NSIS_FIXTURE_ROOT"
  ReadEnvStr $FixtureExtraPath "CHAOXING_NSIS_FIXTURE_EXTRA"
  ReadEnvStr $FixtureCheckOnly "CHAOXING_NSIS_FIXTURE_CHECK_ONLY"
FunctionEnd

Section Install
  ${If} $FixtureMsiMigration == "1"
    Call ChaoxingRefuseMsiMigration
  ${EndIf}
  !insertmacro NSIS_HOOK_PREINSTALL
  ${If} $FixtureLegacyTree == "1"
    Call ChaoxingCheckLegacyInstallTree
  ${EndIf}
  !ifdef CHAOXING_PATH_GUARD_VERSION
    ${If} $FixtureExtraPath != ""
      Push "$FixtureExtraPath"
      Push "file"
      Call ChaoxingCheckPath
    ${EndIf}
  !endif
  ${If} $FixtureCheckOnly != "1"
    SetOutPath "$INSTDIR\backend\_internal"
    FileOpen $0 "$INSTDIR\backend\_internal\payload.bin" w
    FileWrite $0 "installed fixture payload"
    FileClose $0
    FileOpen $0 "$INSTDIR\fixture-host.exe" w
    FileWrite $0 "fixture only, never executable"
    FileClose $0
  ${EndIf}
  SetErrorLevel 0
SectionEnd

Section Uninstall
  !ifmacrodef NSIS_HOOK_PREUNINSTALL
    !insertmacro NSIS_HOOK_PREUNINSTALL
  !endif
  !ifdef CHAOXING_PATH_GUARD_VERSION
    ${If} $FixtureExtraPath != ""
      Push "$FixtureExtraPath"
      Push "file"
      Call un.ChaoxingCheckPath
    ${EndIf}
  !endif
  ${If} $FixtureCheckOnly != "1"
    Delete "$INSTDIR\backend\_internal\payload.bin"
    Delete "$INSTDIR\fixture-host.exe"
  ${EndIf}
  SetErrorLevel 0
SectionEnd
