; ==============================================================================
; League Voice Recorder & Replay Synchronizer — NSIS Modern Installer Script
; Clean Native Rust Desktop Architecture (v0.6.0)
; ==============================================================================

Unicode true
; Use standard zlib compression (lowest false-positive profile on VirusTotal)
SetCompressor /FINAL zlib

; Manifest for modern Windows 10/11 compatibility
ManifestSupportedOS all
ManifestDPIAware true

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

; Version Information Resource
VIProductVersion "${APP_VERSION}.0"
VIFileVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "LegalCopyright" "Copyright (C) 2026 ${APP_PUBLISHER}. All rights reserved."
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileDescription" "${APP_NAME} Setup"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey /LANG=${LANG_ENGLISH} "OriginalFilename" "LeagueVoiceRecorder_0.6.0_x64-setup.exe"

VIAddVersionKey /LANG=${LANG_HUNGARIAN} "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "LegalCopyright" "Copyright (C) 2026 ${APP_PUBLISHER}. Minden jog fenntartva."
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "FileDescription" "${APP_NAME} Telepítő"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey /LANG=${LANG_HUNGARIAN} "OriginalFilename" "LeagueVoiceRecorder_0.6.0_x64-setup.exe"

; ------------------------------------------------------------------------------
; Language Initialization Hooks
; ------------------------------------------------------------------------------
Function .onInit
    !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Function un.onInit
    ReadRegStr $LANGUAGE HKCU "Software\${APP_ID}" "InstallerLanguage"
FunctionEnd

; ------------------------------------------------------------------------------
; Installer Section
; ------------------------------------------------------------------------------
Section "MainProgram" SecMain
    SetShellVarContext current
    SetOutPath "$INSTDIR"

    ; Gracefully close any existing instance via clean WM_CLOSE window message
    FindWindow $0 "" "League Voice Recorder & Replay Synchronizer v0.6.0"
    IntCmp $0 0 +3
    SendMessage $0 16 0 0 ; WM_CLOSE
    Sleep 600

    ; If legacy Inno Setup uninstaller exists from older version, launch it to cleanly remove it
    IfFileExists "$INSTDIR\unins000.exe" 0 +3
    DetailPrint "Launching uninstaller for previous version..."
    ExecWait '"$INSTDIR\unins000.exe"'

    ; Install application binary, WebView2 runtime loader, and visual assets
    DetailPrint "Installing League Voice Recorder v${APP_VERSION}..."
    File "dist\LeagueVoiceRecorder.exe"
    File "assets\WebView2Loader.dll"

    SetOutPath "$INSTDIR\assets"
    File "assets\icon.ico"
    File "assets\icon.png"

    SetOutPath "$INSTDIR"

    ; Create Start Menu and Desktop Shortcuts (with icon)
    CreateDirectory "$SMPROGRAMS\League Voice Recorder"
    CreateShortcut "$SMPROGRAMS\League Voice Recorder\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\assets\icon.ico" 0
    CreateShortcut "$SMPROGRAMS\League Voice Recorder\Uninstall.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\assets\icon.ico" 0
    CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\assets\icon.ico" 0

    ; Write Registry Keys for Windows "Apps & Features" / Add or Remove Programs
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

    ; Create clean uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

; ------------------------------------------------------------------------------
; Uninstaller Section
; ------------------------------------------------------------------------------
Section "Uninstall"
    SetShellVarContext current

    ; Gracefully request the application to close if running
    FindWindow $0 "" "League Voice Recorder & Replay Synchronizer v0.6.0"
    IntCmp $0 0 +3
    SendMessage $0 16 0 0 ; WM_CLOSE
    Sleep 600

    ; Delete Installed Files & Assets
    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\WebView2Loader.dll"
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
