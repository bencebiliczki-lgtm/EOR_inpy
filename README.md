# AFKI EOR mérőrendszer

Windows alatt futó, Python-alapú mérésvezérlő és adatgyűjtő alkalmazás két Teledyne ISCO 260D pumpához, egy NI USB-6001 adatgyűjtőhöz, két nyomásmérőhöz és egy analóg vezérlésű szelephez.

> **Állapot:** működő szimulációs és hardveradapteres alkalmazás. A fizikai üzemhez
> továbbra is kötelező a helyszíni bekötési, kalibrációs és biztonsági validáció.

## Tervezett funkciók

- két ISCO pumpa adatainak lekérése és előkészítési vezérlése;
- NI analóg bemenetek beolvasása és kalibrálása;
- szelep kézi és PID-alapú vezérlése;
- konfigurálható biztonsági határértékek és vészleállítás;
- 1 másodperc és 1 óra közötti adatrögzítési gyakoriság;
- élő, tízperces és teljes mérési diagramok;
- projektek, tetszőleges mérési szakaszok, CSV/Excel export és NAS-mentés.

## Gyorsindítás

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,ui,hardware,export]"
pytest
python -m eor_control
```

A jelenlegi belépési pont szimulátoros PySide6 dashboardot indít. A felületen a
csatlakozás és a mérés külön indítható, a kézi/automata szelepvezérlés választható,
az élő nyomások diagramon láthatók. A nyers rekordok projektenként elkülönítve a
`data/projects/<projektazonosító>_<projektnév>/<projektnév>_raw.csv` fájlba kerülnek.

Első indításkor nyisd meg a `Projekt` → `Projektkezelő…` ablakot, hozz létre egy
projektet és legalább egy mérési szakaszt, majd kattints a `Csatlakozás` és a
`Mérés indítása` gombra. A projekt és szakaszok a
`data/projects.sqlite3` adatbázisban maradnak meg. A PID erősítések, hatásirány és
kimeneti korlátok a dashboardon módosíthatók. Vészleállítás után a reteszelt állapot
a `Hiba nyugtázása` gombbal oldható; ezt követően újra csatlakozni kell.
Projekt létrehozásakor nem szükséges felhasználót vagy tulajdonost megadni; minden
mérés minden kezelő számára elérhető.
A mérési szakasz neve egyben a típusa, ezért létrehozáskor és szerkesztéskor csak
egyetlen közös mezőt kell megadni.

A dashboardon a vonali és differenciálnyomás-csatorna kétpontos kalibrációja,
valamint a köpeny-, besajtolási és differenciálnyomás-határ és a minimális
köpenynyomás-többlet is beállítható. Ezek futó mérés közben zároltak. Az új projekt
kalibrációs pillanatképe az aktuálisan látható értékekből készül.

A dashboard reszponzív, átméretezhető hárompaneles elrendezést használ: az élő
állapotkártyák balra, a nyomás és a besajtolási ütem külön élő grafikonja középre,
a görgethető vezérlőpanel jobbra kerül. A középső grafikonok függőlegesen
átméretezhetők, azonos, méréskezdettől számított pozitív időtengelyt használnak. A
`Beállítások` menüből nyitható meg a kalibrációs/biztonsági panel, valamint itt
választható rendszer-, világos vagy sötét téma. A témaválasztás következő indításra
is megmarad.

A PID-vezérlés 100 ms-os háttérszálon fut, ezért nem blokkolja a Qt főszálat. A
nyers adatrögzítés ettől függetlenül 1 másodperc és 1 óra között állítható. A
dashboard külön kapcsolatjelzőt mutat a két pumpához, a két NI bemenethez és a
szelepaktuátorhoz. A vonali nyomás a berendezés belépő nyomása is, ezért egyetlen
csatornaként kalibrálható, jeleníthető meg és választható PID-forrásként.

A program `QSettings` segítségével megőrzi az utoljára használt projektet és
mérési szakaszt, a témát, a kézi/automata vezérlési mezőket, a PID-paramétereket,
az adatrögzítési időközt, a kalibrációkat és a biztonsági határértékeket. Törölt
projekt vagy sérült beállítás esetén biztonságos UI-alapértékre tér vissza.
A beállítások explicit INI-fájlja a hordozható alkalmazásmappa
`config/AFKI/EORControl.ini` útvonalán található. A korábbi Windows Registry-alapú
`AFKI/EORControl` beállításokat az alkalmazás az első induláskor automatikusan
átmásolja, ha az INI még üres. A témaválasztás azonnal lemezre kerül.

Az eszközök a `Beállítások` → `Eszközök…` ablakban konfigurálhatók. A dashboard és
az ablak is jól látható `SZIMULÁCIÓ` vagy `HARDVER` módszalagot mutat. Hardvermód
csak mindkét ISCO pumpa és mindkét NI analóg bemenet sikeres, csak olvasási tesztje
után, a `HARDVER mód aktiválása` gombbal kapcsolható be.
Mindkét NI bemenet és a szelep NI kimenete felhasználó által megadható és külön
menthető; egyik fizikai csatorna sincs kötelezően a forráskódba rögzítve.
Ugyanitt választható az NI bemeneti bekötési mód, megadható a pumpa- és NI-kábelezés
megjegyzése, a szelep 0%/100% és safe-state feszültsége, továbbá rögzíthető a
kábelkihúzási, vészleállítási és felügyelt kommunikációs próba teljesítése és előírt
időtartama. A differenciálnyomás tényleges tartományát a felhasználó a kalibrációs
ablakban adja meg.
Az alkalmazás minden új indításkor biztonsági okból szimulációs módból indul.

A `Beállítások` → `Naplózás…` ablakban a diagnosztikai napló teljesen ki- vagy
bekapcsolható, és külön választható a két pumpa, a négy NI funkció, a runtime és a
rendszer. Engedélyezve a napló a `data/logs/communication.log` fájlba kerül. A
`Developer` → `Eszközkommunikáció…` nézet élő TX/RX/timeout/hiba táblát és
kategóriaszűrőt biztosít.
A `Rendszer és módváltás` kategória a port- és NI-csatornafelderítés összesítését,
valamint a teljes import- vagy driverhibát `DISCOVERY` iránnyal rögzíti. A
Naplózás ablak a tényleges abszolút logfájlnevet mutatja.

Sikeresen aktivált hardvermódban és csatlakoztatott (`READY`) állapotban a
`Beállítások` → `Felügyelt pumpavezérlés…` ablakból kezelhető mindkét ISCO pumpa.
A sorrend: `REMOTE`, üzemmód/célérték beállítása, először köpenypumpa `RUN`, majd
csak legalább 20 bar igazolt köpenytöbbletnél a besajtolópumpa `RUN`. Mindkét RUN
külön pontos megerősítő szöveget kér. Elérhető külön STOP, STOP ALL, CLEAR és LOCAL.

A `Projekt` → `Adatkezelés és export…` ablak pontosvesszős vagy más elválasztójú,
igény szerint tizedesvesszős CSV-t, továbbá diagramot is tartalmazó Excel-fájlt
készít. Ugyanitt kapcsolható be a NAS-mentés. A másolás háttérszálon fut; sikertelen
hálózati művelet SQLite-várólistán marad, és a program automatikusan újrapróbálja.

A `Megjelenítés` → `Teljes rögzített mérés…` ablak a kiválasztott projekt korábbi
adatait is visszatölti. Az adatsorok külön kapcsolhatók, választható vagy egyéni
időtartomány adható meg, az Y tengely automatikus vagy kézi lehet, a grafikon pedig
egérrel szabadon nagyítható és mozgatható.

## Hordozható Windows-csomag

```powershell
python -m pip install -e ".[ui,hardware,export,package]"
.\scripts\build_windows.ps1
```

A PyInstaller `onedir` csomag a `dist/EOR_Controller/` könyvtárba kerül. A
beállítások a hordozható mappa `config/`, a projektek és várólisták a `data/`
könyvtárában maradnak; a program futásához nem szükséges internet. Az NI-DAQmx és
a soros adapter Windows-drivereit a célgépen külön kell telepíteni.

A GitHub Actions Windows workflow az `AFKI-EOR.spec` alapján egyfájlos
`dist/AFKI-EOR.exe` kiadást készít és ezt csomagolja a célgépre feltöltött ZIP-be.
A workflow a build után kötelezően ellenőrzi, hogy a `serial.tools.list_ports`, a
Windowsos sorosport-felderítő és a `nidaqmx.system` modulok bekerültek-e az EXE-be;
hiányos csomag nem tölthető fel.

A Windows-csomag a `constraints-windows-legacy.txt` alapján NumPy 1.26.4-et
használ, hogy ne igényelje a NumPy 2.x `X86_V2` CPU-baseline-ját. A workflow a
csomagolás előtt ellenőrzi a tényleges NumPy-verziót. A Core 2 Quad Q9400
kompatibilitását az OptiPlex célgépen indított próba igazolja.

## Dokumentáció

A fejlesztés előtt olvasd el az `AGENTS.md` és a `docs/` fájlokat. A még nem tisztázott kérdések a `docs/open-questions.md` dokumentumban találhatók.
