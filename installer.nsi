; ==============================================================================
; League Voice Recorder & Replay Synchronizer — NSIS Modern Installer Script
; Clean Native Rust Desktop Architecture (v0.6.1)
; ==============================================================================

Unicode true
; Use standard zlib compression (lowest false-positive profile on VirusTotal)
SetCompressor /FINAL zlib

; Manifest for modern Windows 10/11 compatibility
ManifestSupportedOS all
ManifestDPIAware true

!define APP_NAME "League Voice Recorder"
!define APP_VERSION "0.6.1"
!define APP_PUBLISHER "betterix"
!define APP_URL "https://github.com/betterix/league-voice-recorder"
!define APP_EXE "LeagueVoiceRecorder.exe"
!define APP_ID "com.betterix.leaguevoicerecorder"

; Product Name displayed in installer window title and taskbar
Name "${APP_NAME}"

; Output filename matching standard NSIS naming
OutFile "LeagueVoiceRecorder_${APP_VERSION}_x64-setup.exe"
InstallDir "$LOCALAPPDATA\Programs\League Voice Recorder"
InstallDirRegKey HKCU "Software\${APP_ID}" "InstallDir"
RequestExecutionLevel user

; Visual Branding
!define MUI_ICON "assets\icon.ico"
!define MUI_UNICON "assets\icon.ico"
!define MUI_ABORTWARNING

; ------------------------------------------------------------------------------
; Modern UI 2 Includes & Pages
; ------------------------------------------------------------------------------
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; Language Selection Dialog Settings
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "Software\${APP_ID}"
!define MUI_LANGDLL_REGISTRY_VALUENAME "InstallerLanguage"

; Installer Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT $(LaunchApp)
!insertmacro MUI_PAGE_FINISH

; Uninstaller Pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Languages (English & Hungarian)
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Hungarian"

; Localized Strings
LangString LaunchApp ${LANG_ENGLISH} "Launch League Voice Recorder"
LangString LaunchApp ${LANG_HUNGARIAN} "League Voice Recorder indítása"

LangString PreviousVersionFound ${LANG_ENGLISH} "A previous version of League Voice Recorder was detected.$\r$\n$\r$\nWould you like to uninstall it first before installing v${APP_VERSION}?"
LangString PreviousVersionFound ${LANG_HUNGARIAN} "A League Voice Recorder korábbi verziója található a gépen.$\r$\n$\r$\nSzeretnéd eltávolítani a régebbi verziót a v${APP_VERSION} telepítése előtt?"

; Version Information Resource
VIProductVersion "${APP_VERSION}.0"
VIFileVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "LegalCopyright" "Copyright (C) 2026 ${APP_PUBLISHER}. All rights reserved."
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileDescription" "${APP_NAME} Setup"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey /LANG=${LANG_ENGLISH} "OriginalFilename" "LeagueVoiceRecorder_${APP_VERSION}_x64-setup.exe"

VIAddVersionKey /LANG=${LANG_HUNGARIAN} "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "LegalCopyright" "Copyright (C) 2026 ${APP_PUBLISHER}. Minden jog fenntartva."
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "FileDescription" "${APP_NAME} Telepítő"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "OriginalFilename" "LeagueVoiceRecorder_${APP_VERSION}_x64-setup.exe"

; ------------------------------------------------------------------------------
; Language Initialization & Pre-Install Detection
; ------------------------------------------------------------------------------
Function .onInit
    ${IfNot} ${Silent}
        !insertmacro MUI_LANGDLL_DISPLAY
    ${EndIf}

    ; Gracefully and forcefully terminate any active running instance (preventing file lock errors)
    nsExec::Exec 'taskkill /F /IM "${APP_EXE}" /T'
    Sleep 500

    ; Detect legacy Python Inno Setup installation (unins000.exe)
    StrCpy $R0 ""

    ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\League Voice Recorder_is1" "UninstallString"
    ${If} $R0 == ""
        ReadRegStr $R0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\League Voice Recorder_is1" "UninstallString"
    ${EndIf}

    ${If} $R0 == ""
        ${If} ${FileExists} "$LOCALAPPDATA\Programs\League Voice Recorder\unins000.exe"
            StrCpy $R0 '"$LOCALAPPDATA\Programs\League Voice Recorder\unins000.exe"'
        ${EndIf}
    ${EndIf}

    ; If legacy version is detected, prompt the user in the selected language and execute uninstaller before continuing
    ${If} $R0 != ""
        ${If} $LANGUAGE == ${LANG_ENGLISH}
            MessageBox MB_YESNO|MB_ICONQUESTION "A legacy version of League Voice Recorder was detected.$\r$\n$\r$\nWould you like to uninstall it first before installing v${APP_VERSION}?" IDNO +3
        ${Else}
            MessageBox MB_YESNO|MB_ICONQUESTION "A League Voice Recorder korábbi verziója található a gépen.$\r$\n$\r$\nSzeretnéd eltávolítani a régebbi verziót a v${APP_VERSION} telepítése előtt?" IDNO +3
        ${EndIf}
        ExecWait '$R0'
        Sleep 1000
    ${EndIf}
FunctionEnd

Function un.onInit
    ReadRegStr $LANGUAGE HKCU "Software\${APP_ID}" "InstallerLanguage"
FunctionEnd

Function .onInstSuccess
    ${If} ${Silent}
        Exec '"$INSTDIR\${APP_EXE}"'
    ${EndIf}
FunctionEnd

; ------------------------------------------------------------------------------
; Installer Section
; ------------------------------------------------------------------------------
Section "MainProgram" SecMain
    SetShellVarContext current
    SetOutPath "$INSTDIR"

    ; Gracefully and forcefully terminate any active running instance (preventing file lock errors)
    nsExec::Exec 'taskkill /F /IM "${APP_EXE}" /T'
    Sleep 500

    ; Clean up any leftover legacy uninstaller files if present
    Delete "$INSTDIR\unins000.exe"
    Delete "$INSTDIR\unins000.dat"
    Delete "$INSTDIR\unins000.msg"

    ; Install application binary and documentation
    DetailPrint "Installing League Voice Recorder v${APP_VERSION}..."
    File "LeagueVoiceRecorder.exe"
    File "LICENSE"
    File "README.md"

    ; Create Start Menu and Desktop Shortcuts (using embedded exe icon)
    CreateDirectory "$SMPROGRAMS\League Voice Recorder"
    CreateShortcut "$SMPROGRAMS\League Voice Recorder\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
    CreateShortcut "$SMPROGRAMS\League Voice Recorder\Uninstall.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\${APP_EXE}" 0
    CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0

    ; Write Registry Keys for Windows "Apps & Features" / Add or Remove Programs
    WriteRegStr HKCU "Software\${APP_ID}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\${APP_ID}" "Version" "${APP_VERSION}"

    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "InstallLocation" "$INSTDIR"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "NoRepair" 1

    ; Estimate installed size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "EstimatedSize" "$0"

    ; Create clean uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

; ------------------------------------------------------------------------------
; Uninstaller Section
; ------------------------------------------------------------------------------
Section "Uninstall"
    SetShellVarContext current

    ; Gracefully and forcefully terminate any active running instance
    nsExec::Exec 'taskkill /F /IM "${APP_EXE}" /T'
    Sleep 500

    ; Delete Installed Files & Documentation
    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\LICENSE"
    Delete "$INSTDIR\README.md"
    Delete "$INSTDIR\locales\en.json"
    Delete "$INSTDIR\locales\hu.json"
    RMDir "$INSTDIR\locales"
    Delete "$INSTDIR\assets\icon.ico"
    Delete "$INSTDIR\assets\icon.png"
    RMDir "$INSTDIR\assets"
    Delete "$INSTDIR\Uninstall.exe"

    ; Delete Shortcuts
    Delete "$SMPROGRAMS\League Voice Recorder\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\League Voice Recorder\Uninstall.lnk"
    RMDir "$SMPROGRAMS\League Voice Recorder"
    Delete "$DESKTOP\${APP_NAME}.lnk"

    ; Delete Registry Entries
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"
    DeleteRegKey HKCU "Software\${APP_ID}"

    ; Remove directory if empty
    RMDir "$INSTDIR"
SectionEnd
