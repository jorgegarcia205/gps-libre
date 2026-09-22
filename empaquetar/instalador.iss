; Instalador de GPS Libre (Inno Setup 6). Se compila con empaquetar\construir.ps1, después de PyInstaller.

#define Nombre "GPS Libre"
#define Version "1.0.0"
#define Exe "GPSLibre.exe"

[Setup]
AppId={{7F3C2A91-4B6E-4D2A-9C1F-5E8B0A7D3C21}
AppName={#Nombre}
AppVersion={#Version}
AppPublisher=GPS Libre
DefaultDirName={autopf}\GPS Libre
DefaultGroupName={#Nombre}
; --- Instalador ULTRA SENCILLO: sin pantallas de bienvenida, carpeta, tareas ni "listo para instalar" ---
DisableProgramGroupPage=yes
DisableWelcomePage=yes
DisableDirPage=yes
DisableReadyPage=yes
ShowLanguageDialog=no
; Si la app ya está instalada y corriendo, ciérrala automáticamente para poder reemplazar los archivos.
CloseApplications=force
RestartApplications=no
OutputDir=salida
OutputBaseFilename=GPSLibre-Instalador-{#Version}
SetupIconFile=gpslibre.ico
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#Nombre}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Administrador: instala en Archivos de programa y abre el firewall para el iPhone.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "dist\GPSLibre\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#Nombre}"; Filename: "{app}\{#Exe}"
Name: "{group}\Desinstalar {#Nombre}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#Nombre}"; Filename: "{app}\{#Exe}"

[Run]
; Deja entrar al iPhone por la Wi‑Fi (la página del puerto 8765) sin que salga el aviso del firewall.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GPS Libre"" dir=in action=allow program=""{app}\{#Exe}"" enable=yes profile=any"; Flags: runhidden
Filename: "{app}\{#Exe}"; Description: "Abrir {#Nombre}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GPS Libre"""; Flags: runhidden; RunOnceId: "QuitarReglaFirewall"

[Code]
function ControladorAppleInstalado(): Boolean;
begin
  { iTunes y «Dispositivos Apple» instalan el servicio que Windows usa para hablar con el iPhone por cable. }
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\Apple Mobile Device Service') or
            RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\AppleMobileDeviceService');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Resultado: Integer;
begin
  if (CurStep = ssPostInstall) and (not WizardSilent()) and (not ControladorAppleInstalado()) then
    if MsgBox('Para conectar el iPhone por cable, Windows necesita el controlador de Apple y no lo encontré.' + #13#10#13#10 +
              'Instala gratis «Dispositivos Apple» desde Microsoft Store (o iTunes). La primera conexión siempre es por cable.' + #13#10#13#10 +
              '¿Abrir Microsoft Store ahora?', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'ms-windows-store://pdp/?productid=9NP83LWLPZ9K', '', '', SW_SHOWNORMAL, ewNoWait, Resultado);
end;
