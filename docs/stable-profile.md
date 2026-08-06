# Stabil alapbeállítások

A verziózott kiinduló profil a `config/stable-defaults.json` fájlban található.
Ez nem fizikailag validált hardverprofil. Célja, hogy az alkalmazás a Dell
OptiPlex 780 célgépen biztonságosan elinduljon, miközben minden ismeretlen
fizikai paraméter blokkolja a hardveres mérés indítását.

## Szoftveres alapértékek

| Beállítás | Érték | Indoklás |
| --- | ---: | --- |
| DAQ mintavétel | 10 Hz | Konzervatív terhelés a Q9400 processzoron |
| PID/vezérlési ciklus | 5 Hz | A fizikai I/O prioritása, 200 ms-os ciklus |
| Adatrögzítés | 1 s | A nyers adatok helyi, folyamatos mentése |
| Numerikus/diagram frissítés | 2 Hz | A Qt felület terhelésének korlátozása |
| Hardverstátusz polling | 1 s | Csak olvasási állapotfelügyelet |
| STALE-határ | 6 s | Három pollingperiódus és a soros timeout/retry keret lefedése |
| Soros timeout | 2 s | Egyetlen lassú válasz tolerálása |
| Soros próbálkozás | 2 | Ismétlődő hiba után egyértelmű leállás |
| Látható pont/sorozat | 2000 | Korlátozott UI-memória; a nyers CSV ettől független |
| NAS mérés alatt | kikapcsolva | Offline-first, helyi mentés nem blokkolható |

A profil migrációja csak hiányzó szoftveres kulcsokat tölt ki a felhasználó
`Dokumentumok/EOR/EORControl.ini` fájljában. Meglévő kezelői vagy hardverbeállítást
nem ír felül. Sémaverzió nélküli korábbi JSON-profil az 1-es verzióra
migrálható; ismeretlen jövőbeli verziót a program elutasít.

## Indulási és mérési kapu

Az alkalmazás hiányos profillal is elindul, így a diagnosztika és a
beállítási felület elérhető. Hardvermérés előtt a már meglévő, csak
olvasási preflight ellenőrzi a projektet, a tárhelyet, a pumpák aktuális
`PRESS/FLOW/VOL/STATUS` adatait, az NI bemeneteket, a kalibrációt és a
biztonsági reteszeket. A stabil profil ezen felül blokkol, amíg nincs külön
validálva:

- `hardware/valve_direction_validated`;
- `hardware/pump_shutdown_validated`;
- `safety/limits_validated`;
- `calibration/profile_validated`;

A szelep SAFE feszültségéhez és a PID-paraméterekhez nincs külön
validáltsági jelző. A SAFE feszültség meglétét a hardverkonfiguráció, a
PID-paramétereket pedig a `PidParameters` számszaki validációja ellenőrzi.

A preflight nem lép REMOTE módba, nem küld FLOW/PRESS/RUN/STOP parancsot, nem
ír NI AO-csatornára és nem indít PID-et. Sikertelen ellenőrzésnél az
alkalmazás HARDWARE módban marad, csak a mérésindítás tiltott.

## Telepítés és onedir build

Python 3.12 x64 környezetben:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints-windows-legacy.txt ".[ui,hardware,export,package]"
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm AFKI-EOR.spec
```

Az eredmény a `dist/AFKI-EOR/` könyvtár. A teljes könyvtárat kell a
célgépre másolni; a `config/stable-defaults.json` a csomag része. A build
`onedir`, nem `onefile`, ezért nincs minden indításkor ideiglenes
kicsomagolási terhelés.

## Helyszíni üzembe helyezési ellenőrzőlista

1. Ellenőrizd a Windows 10 buildet, az NI-DAQmx drivert és a szabad lemezhelyet.
2. Csak olvasási felderítéssel azonosítsd a pumpákat. A COM3 automatikusan
   kizárt; szervizmódban külön felülbírálható.
3. Rendeld a két különböző portot a BES és KÖP szerephez, majd mentsd a
   sikeresen igazolt baud rate-et, unit ID-t és csatornát.
4. NI MAX és az alkalmazás felderítése alapján válaszd ki az USB-6001-et,
   az AI/AO csatornákat és a támogatott terminálmódot.
5. Nyomásmentes rendszeren, több mintából mérd meg a szenzorok nullpontját.
6. Dokumentált etalonnal add meg a felső kalibrációs pontokat.
7. Független fizikai felügyelet mellett igazold a szelep irányát és SAFE
   feszültségét.
8. A rendszer leggyengébb eleméhez igazítva ellenőrizd a pumpák MAX PRESS és
   `Shutdown` beállítását.
9. Add meg a projektspecifikus nyomás-, flow- és PID-értékeket.
10. Futtass legalább 60 perces kétpumpás kommunikációs és felügyelt
    biztonsági próbát, majd csak ezután állítsd be a validációs jelzőket.

## Fizikai ellenőrzés nélkül nem véglegesíthető

- BES és KÖP COM-port, baud rate, unit ID és pumpacsatorna;
- NI-eszköznév, AI/AO kiosztás és terminálmód;
- differenciálnyomás-mérő tartománya;
- minden szenzor nullpontja és felső kalibrációs pontja;
- szelep nyitási iránya és SAFE feszültsége;
- pumpa MAX PRESS és `Shutdown`, illetve minden berendezéselem nyomáshatára;
- BES/KÖP flow-k, PID-paraméterek és kimeneti sebességkorlát.
