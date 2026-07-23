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

Interaktív, állapotot megőrző terminálos szimuláció indítása:

```powershell
python -m eor_control terminal
# vagy a kiadott csomagból:
.\AFKI-EOR.exe terminal
```

A terminál `help` parancsa listázza a kapcsolat-, mérés-, vészleállítás- és
szabályozási parancsokat. A terminál mód jelenleg tudatosan csak szimulációt vezérel:
nem ment mérési adatot és nem ér el fizikai kimenetet. A fizikai hardvermód
felderítése, tesztje és kezelői engedélyezése továbbra is kizárólag a grafikus
eszközbeállítási folyamatban történhet.

A jelenlegi belépési pont szimulátoros PySide6 dashboardot indít. A felületen a
csatlakozás és a mérés külön indítható, a kézi/automata szelepvezérlés választható,
az élő nyomások diagramon láthatók. A nyers rekordok projektenként és mérési
fázisonként elkülönítve a
`data/projects/<év>/<dátum>_<projektazonosító>_<projektnév>/<projektnév>_<fázis>_live_raw.csv`
fájlokba kerülnek.
Szimulációs módban a mért értékek csak élőben jelennek meg: nyers CSV, projekt-
pillanatkép és NAS-feladat nem készül. A dashboard felső, állandó szöveges sávja
mindig megkülönbözteti a szimulációt a hardvermódtól; alatta az aktív, reteszelt
riasztás a kiváltó okkal, automatikus művelettel és következő kezelői lépéssel
megmarad a nyugtázásig. Minimalizált vagy háttérben lévő ablaknál a Windows
tálcagomb is figyelmeztet.
A Developer mód bekapcsolása után a `Developer` → `Szimulációs mód` kapcsolóval
lehet visszatérni a mentés nélküli szimulációhoz. A váltás csak leválasztott,
`IDLE` állapotban engedélyezett. A kapcsoló kikapcsolása az eszközbeállítási
ablakot nyitja meg; az éles mód továbbra is felderítést és külön megerősítést kér.

Első indításkor nyisd meg a `Projekt` → `Projektkezelő…` ablakot, hozz létre egy
projektet és legalább egy mérési szakaszt, majd kattints a `Csatlakozás` és a
`Mérés indítása` gombra. Indítás előtt kötelező, tételes ellenőrzőablak jelenik
meg a projekt, eszközkapcsolatok, szenzoradatok, kalibráció, biztonsági reteszek,
a konfigurált minimális köpenynyomás-többlet és tárhely állapotával. Hiba tiltja az
indítást; figyelmeztetés külön kezelői jóváhagyást igényel. A projekt és szakaszok a
`data/projects.sqlite3` adatbázisban maradnak meg. A PID erősítések, hatásirány és
a `Hiba nyugtázása` gombbal oldható; ezt követően újra csatlakozni kell.
kimeneti korlátok Developer módban módosíthatók. Normál kezelői nézetben csak az
üzemi vezérlési mezők láthatók. Vészleállítás után a reteszelt állapot a `Hiba
nyugtázása` gombbal oldható; ezt követően újra csatlakozni kell.
a `Hiba nyugtázása` gombbal oldható; ezt követően újra csatlakozni kell.
Projekt létrehozásakor nem szükséges felhasználót vagy tulajdonost megadni; minden
mérés minden kezelő számára elérhető.
A mérési szakasz neve egyben a típusa, ezért létrehozáskor és szerkesztéskor csak
egyetlen közös mezőt kell megadni.
Az **Aktív projekt** szakaszválasztójának utolsó eleme mindig az
**„+ Új szakasz hozzáadása…”** művelet. Ez külön ablakban kéri be a szakasz adatait
és az opcionális megjegyzést, majd mentés után automatikusan az új szakaszt választja.
Projekt a projektválasztó képernyőről és a projektkezelőből is törölhető külön
megerősítés után. Ez a projekt- és fázis-metaadatokat törli; a korábbi nyers mérési
CSV-k biztonsági és visszakövethetőségi okból megmaradnak.

A **Szelepvezérlés** panelen a PID-beállítások névvel menthető profilokba
rendezhetők. A profil a `Kp`, `Ki`, `Kd`, hatásirány, kimeneti minimum/maximum és
nyomásforrás értékeket tárolja. A profilok közös SQLite-adatok: kiválaszthatók,
felülírhatók és megerősítés után törölhetők. Egy mentett profil kézi módosítása
automatikusan **Egyéni beállítások** állapotra vált; az utoljára kiválasztott profil
alkalmazásindításkor visszatöltődik.

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

Az eszközök projektenkénti moduláris profilként konfigurálhatók: a
`Projektbeállítások` ablakban a két pumpa, a két nyomásbemenet és a
szelepkimenet külön-külön hozzáadható vagy eltávolítható. Az
`Eszközök…` ablak automatikusan a kiválasztott projekt profilját tölti be. A
dashboardon nincs állandó módszalag vagy „nincs riasztás” sáv: az üzemmód az
ablak címében és az állapotsorban látható, a piros riasztási sáv pedig csak
tényleges aktív hiba idejére jelenik meg. Hardvermód
csak az aktív mérési profil kötelező eszközeinek sikeres, csak olvasási tesztje
után, a `HARDVER mód aktiválása` gombbal kapcsolható be. Az eszközbeállításokban
a két pumpa és a két NI bemenet külön-külön is tesztelhető; az összesített próba
részleges hiba esetén is megmutatja, mely kapcsolatok voltak sikeresek. A szelep
AO kapcsolatát a program nem jelöli olvasással igazoltnak, mert annak próbája
fizikai kimeneti írást igényelne.
Developer módban legalább egy sikeres egyedi kapcsolatpróba után külön
**Részleges HARDVER tesztmód** aktiválható. Ebben a módban normál mérés nem
indítható, viszont a manuális hardverablakban a két pumpa egymástól és az
NI-bemenetektől függetlenül kapcsolható vagy választható le. A működő szenzorok
értéke részleges telemetriaként megmarad. A manuális parancsot külön,
céleszköz-specifikus biztonsági profil felügyeli, ezért egy nem hozzáadott
vonali nyomásmérő nem blokkolja a pumpa vagy szelep kezelését.
Mindkét NI bemenet és a szelep NI kimenete felhasználó által megadható és külön
menthető; egyik fizikai csatorna sincs kötelezően a forráskódba rögzítve.
Ugyanitt választható az NI bemeneti bekötési mód, megadható a pumpa- és NI-kábelezés
megjegyzése, valamint a szelep 0%/100% és safe-state feszültsége. A korábbi
helyszíni validációs adatblokk nem része az eszközbeállításoknak. A
differenciálnyomás tényleges tartományát a felhasználó a kalibrációs ablakban adja meg.
Az alkalmazás minden új indításkor biztonsági okból szimulációs módból indul.
A Windows tálca rejtett ikonjának helyi menüjéből az ablak újra megnyitható, vagy
a `Program bezárása` művelettel az alkalmazás biztonságosan leállítható. A tálcás
A dashboard jobb oldali vezérlősávja egysávos, teljes szélességű gombokat és
beviteli mezőket használ; a
címkék keskenyebb ablaknál a mezők fölé törnek, ezért a vezérlők nem vágódnak le.
kilépés ugyanazt a runtime-leállítási, safe-state, eszközleválasztási és
adattároló-lezárási útvonalat használja, mint a főablak bezárása.

A `Beállítások` → `Naplózás…` ablakban a diagnosztikai napló teljesen ki- vagy
bekapcsolható, és külön választható a két pumpa, a négy NI funkció, a runtime és a
rendszer. Engedélyezve a rendszer- és runtime események a
`data/logs/application.html`, a pumpa- és NI-kommunikáció pedig a külön
`data/logs/hardware_communication.html` fájlba kerül. Mindkét önálló HTML-riport
kereshető és szint szerint szűrhető, rögzített táblázatfejlécet, eseményszámlálót,
valamint INFO/WARNING/ERROR színezést használ. A
`Developer` → `Eszközkommunikáció…` nézet élő TX/RX/timeout/hiba táblát és
kategóriaszűrőt biztosít.
A `Rendszer és módváltás` kategória a port- és NI-csatornafelderítés összesítését,
valamint a teljes import- vagy driverhibát `DISCOVERY` iránnyal rögzíti. A
Naplózás ablak a tényleges abszolút logfájlnevet mutatja.

Sikeresen aktivált hardverprofilban a
Developer módban a `Developer` → `Felügyelt manuális hardvervezérlés…` ablakból
kezelhető minden hozzáadott ISCO pumpa és a szelep. Az ablak élőben mutatja a pumpák
áramlását és nyomását, valamint a vonali és differenciálnyomást. Pumpaindítás és
szelepírás előtt a manuális biztonsági profil a megcélzott eszköz kapcsolatát,
saját visszajelzését és határértékét ellenőrzi. A `CSATLAKOZÁS + REMOTE` egyetlen
műveletként azonosítja a pumpát és REMOTE módba állítja, ezt követi az
üzemmód/célérték beállítása, majd az adott pumpa `RUN`. Mindkét RUN külön pontos
megerősítő szöveget kér. Az ablak bezárása minden pumpán megkísérli a STOP-ot,
bontja a kapcsolatot és lezárja a COM-portokat.

A `Projekt` → `Adatkezelés és export…` ablak pontosvesszős vagy más elválasztójú,
igény szerint tizedesvesszős CSV-t készít az aktív mérési fázis saját nyers
CSV-jéből. A diagramot is tartalmazó Excel-fájl automatikusan, csak a mérési
fázis lezárásakor készül el; futó fázisból nem indítható kézi Excel-export.
Projektenként egy munkafüzet készül, amelyben minden lezárt szakasz saját,
szűrhető adatokkal és beágyazott nyomás-/szelepdiagrammal rendelkező munkalapot
kap. Ugyanitt kapcsolható be a NAS-mentés. Az elkészült projekt-Excel is a
háttérben futó NAS-sorba kerül; sikertelen
hálózati művelet SQLite-várólistán marad, és a program automatikusan újrapróbálja.

A dashboard középső részén az **Élő mérés** és a **Teljes mérés** fülek között lehet
váltani. A **Teljes mérés** fül adatsor-, fázis-, időtartomány- és tengelybeállításai
a **Megjelenítési beállítások** fejléc segítségével elrejthetők, így a diagram a
rendelkezésre álló teljes függőleges helyet használhatja.
A **Teljes mérés** fül a kiválasztott projekt korábbi adatait is
visszatölti. Az adatsorok külön kapcsolhatók, választható vagy egyéni
időtartomány adható meg, az Y tengely automatikus vagy kézi lehet, a grafikon pedig
egérrel szabadon nagyítható és mozgatható. A nézet mérési fázisra szűrhető, az
összes fázis folytonos szakaszait pedig külön, színes idősáv mutatja. Ehhez a nézet
a külön fázis-CSV-ket csak memóriában rendezi közös időrendbe. A belső
**Grafikon/Táblázat** váltó ugyanarra a szűrésre épül. A táblázat az Excel aktuális
oszlopait és magyar helyi időt mutat, nagy mérésnél pedig 1000 soros lapokkal
védi a célgépet a túlzott UI-terheléstől.
A `Megjelenítés` → `Teljes mérés fül` menüpont és a `Ctrl+Shift+G` gyorsbillentyű
közvetlenül erre a dashboard-fülre vált, külön ablakot nem nyit.

A dashboard mindkét pumpánál előjelesen mutatja az indítás óta számított nettó
térfogatváltozást. Negatív érték azt jelenti, hogy a pumpa maradék térfogata az
indításkori érték fölé nőtt.

## Hordozható Windows-csomag

```powershell
# A .venv környezetet Python 3.12 x64 verzióval kell létrehozni.
py -3.12 -m venv .venv
.\scripts\build_windows.ps1
```

A build script kizárólag Python 3.12-es virtuális környezetet fogad el, és a
`constraints-windows-legacy.txt` alapján telepíti az összes csomagolási függőséget.
Ez garantálja, hogy a csomagolt alkalmazás NumPy 1.26.4-et tartalmazzon; a célgépen
nem szükséges külön Python- vagy NumPy-telepítés. Python 3.14-es környezetből a
célgépes csomag szándékosan nem készíthető el.

A célgépen a becsomagolt Python-, NumPy- és NI-DAQmx környezet, valamint a helyileg
felismert NI-eszközök kimeneti parancs nélküli ellenőrzése:

```powershell
.\AFKI-EOR.exe diagnose-ni
```

A PyInstaller `onefile` csomag egyetlen `dist/EOR_Controller.exe` fájlba kerül. A
beállítások, projektek és várólisták az EXE-től független, írható `config/` és
`data/` könyvtárakban maradnak; a program futásához nem szükséges internet. Az
NI-DAQmx és a soros adapter Windows-drivereit a célgépen külön kell telepíteni.

A GitHub Actions Windows workflow az `AFKI-EOR.spec` alapján egyfájlos
`dist/AFKI-EOR.exe` kiadást készít és ezt csomagolja a célgépre feltöltött ZIP-be.
A Windows EXE erőforrásikonja, az alkalmazásablak és a tálcagomb az `img/icon.png`
képet használja. A stabil `AFKI.EOR.Control` Windows AppUserModelID megakadályozza,
hogy futás közben a Python alapikonja jelenjen meg a tálcán.
A workflow a build után kötelezően ellenőrzi, hogy a `serial.tools.list_ports`, a
Windowsos sorosport-felderítő és a `nidaqmx.system` modulok bekerültek-e az EXE-be;
hiányos csomag nem tölthető fel.

A Windows-csomag a `constraints-windows-legacy.txt` alapján NumPy 1.26.4-et
használ, hogy ne igényelje a NumPy 2.x `X86_V2` CPU-baseline-ját. A workflow a
csomagolás előtt ellenőrzi a tényleges NumPy-verziót. A Core 2 Quad Q9400
kompatibilitását az OptiPlex célgépen indított próba igazolja.

## Dokumentáció

A fejlesztés előtt olvasd el az `AGENTS.md` és a `docs/` fájlokat. A még nem tisztázott kérdések a `docs/open-questions.md` dokumentumban találhatók.
