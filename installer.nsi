; ==============================================================================
; League Voice Recorder & Replay Synchronizer — NSIS Modern Installer Script
; Pure Rust Desktop Architecture (v0.6.0)
; ==============================================================================

Unicode true
SetCompressor /SOLID lzma

!define APP_NAME "League Voice Recorder"
!define APP_VERSION "0.6.0"
!define APP_PUBLISHER "betterix"
!define APP_URL "https://github.com/betterix/league-voice-recorder"
!define APP_EXE "LeagueVoiceRecorder.exe"
!define APP_ID "com.betterix.leaguevoicerecorder"

; Output filename matching standard NSIS naming
OutFile "dist\LeagueVoiceRecorder_0.6.0_x64-setup.exe"
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

; Installer Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch League Voice Recorder"
!insertmacro MUI_PAGE_FINISH

; Uninstaller Pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Languages (English & Hungarian)
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Hungarian"

; Version Information Resource
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "LegalCopyright" "Copyright (C) 2026 ${APP_PUBLISHER}. GPL-3.0 License."
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileDescription" "${APP_NAME} Setup Wizard (NSIS)"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileVersion" "${APP_VERSION}.0"

; ------------------------------------------------------------------------------
; Installer Section
; ------------------------------------------------------------------------------
Section "MainProgram" SecMain
    SetOutPath "$INSTDIR"

    ; Close running instances if active
    DetailPrint "Checking for running instances..."
    nsExec::Exec 'taskkill /F /IM "${APP_EXE}"'

    ; 1. Clean up legacy Python files, folders, and bytecode
    DetailPrint "Cleaning up legacy Python runtime and build clutter..."
    RMDir /r "$INSTDIR\tcl"
    RMDir /r "$INSTDIR\tk"
    RMDir /r "$INSTDIR\app"
    RMDir /r "$INSTDIR\_internal"
    RMDir /r "$INSTDIR\lib"
    Delete "$INSTDIR\*.pyd"
    Delete "$INSTDIR\python*.dll"
    Delete "$INSTDIR\*.pyc"
    Delete "$INSTDIR\*.whl"

    ; 2. Install Rust binary and visual assets
    DetailPrint "Installing League Voice Recorder v${APP_VERSION}..."
    File "dist\LeagueVoiceRecorder.exe"
    
    SetOutPath "$INSTDIR\assets"
    File "assets\icon.ico"
    File "assets\icon.png"

    SetOutPath "$INSTDIR"

    ; 3. Shortcuts
    CreateDirectory "$SMPROGRAMS\League Voice Recorder"
    CreateShortcut "$SMPROGRAMS\League Voice Recorder\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\assets\icon.ico" 0
    CreateShortcut "$SMPROGRAMS\League Voice Recorder\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
    CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\assets\icon.ico" 0

    ; 4. Write Registry Keys for Windows "Apps & Features"
    WriteRegStr HKCU "Software\${APP_ID}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\${APP_ID}" "Version" "${APP_VERSION}"

    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayIcon" "$INSTDIR\assets\icon.ico"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "InstallLocation" "$INSTDIR"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "NoRepair" 1

    ; Estimate installed size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "EstimatedSize" "$0"

    ; 5. Create Uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

; ------------------------------------------------------------------------------
; Uninstaller Section
; ------------------------------------------------------------------------------
Section "Uninstall"
    ; Close running instances
    nsExec::Exec 'taskkill /F /IM "${APP_EXE}"'

    ; Delete Files & Assets
    Delete "$INSTDIR\${APP_EXE}"
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

    ; Remove folder if empty
    RMDir "$INSTDIR"
SectionEnd
