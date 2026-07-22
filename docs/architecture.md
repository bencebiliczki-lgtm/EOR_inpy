# Architektúra

## Rétegek

1. **UI:** állapotmegjelenítés, projektkezelés, diagramok és kezelői parancsok.
2. **Alkalmazási szolgáltatások:** mérési munkamenet, parancsengedélyezés, állapotgép és koordináció.
3. **Biztonsági felügyelet:** határértékek, watchdog, interlockok és biztonságos állapot.
4. **Szabályozás:** PID és kézi szelepjel előállítása a biztonsági korlátozásokon belül.
5. **Eszközadapterek:** ISCO/DASNET, NI-DAQmx és ezek szimulátorai.
6. **Adatkezelés:** lokális napló, SQLite metaadatok, CSV/Excel export és NAS-szinkron.

## Függőségi szabály

A felső réteg ismerheti az alatta lévő absztrakciót, de a hardveradapter nem ismerheti a UI-t. A biztonsági felügyelet képes felülírni minden normál vezérlési kimenetet.

## Fő absztrakciók

- `Pump`: csatlakozás, státuszlekérés, felügyelt parancs és leállítás.
- `DataAcquisition`: analóg bemenetek és kimenetek kezelése.
- `SafetyMonitor`: mérési pillanatkép kiértékelése.
- `MeasurementWriter`: nyers rekord tartós mentése.
- `Clock`: valós és tesztidő leválasztása.

## Folyamat

1. Eszközadatok párhuzamos lekérése.
2. Érvényesség és frissesség ellenőrzése.
3. Kalibrált mérési pillanatkép összeállítása.
4. Biztonsági kiértékelés.
5. Veszély esetén biztonságos állapot; egyébként kézi/PID kimenet korlátozása.
6. Rekord helyi tartósítása.
7. UI frissítése és háttérben NAS-szinkron.

## Szálkezelés

A hardver I/O, adatmentés és NAS-művelet nem futhat a Qt főszálán. A szabályozási ciklusnak mérhető határideje és watchdogja legyen. A konkrét concurrency-modell az első hardverprototípus mérései után véglegesítendő.

## Jelenlegi mérési prototípus

A `MeasurementService` egy konfigurálható, 1 másodperc és 1 óra közötti ciklusban
olvassa a két pumpát és a két kalibrált analóg csatornát. Minden pillanatképet előbb
biztonságilag kiértékel, majd projektenként és mérési fázisonként elkülönített
append-only nyers CSV-fájlba ír. Interlock vagy
eszközhiba esetén mindkét pumpán szimulált STOP-ot, a DAQ-on pedig biztonságos
állapotot kér. A ciklust a `BackgroundControlRunner` külön munkaszálon futtatja;
Qt főszálból közvetlenül nem fut.

## Projektfájlok, export és NAS

A `ProjectMeasurementWriter` az aktív projekt azonosítójából és Windows-biztos
nevéből és dátumából `projects/<év>/<dátum>_<azonosító>_<név>` könyvtárat képez.
Minden éles fázis saját `<projektnév>_<fázisnév>_live_raw.csv` fájlt kap. Projekt- vagy
fázisváltáskor az író lezárja az előző fájlt, így a fázisok adatai nem keveredhetnek.
A nyers CSV UTF-8, append-only, minden teljes
sor után flush és `fsync` történik.
Ugyanitt hordozható `project.json`, `config_snapshot.json` és
`calibration_snapshot.json` készül. A nyers CSV pontosvesszős és tizedesvesszős;
a beolvasó a korábbi vesszős fájlokat is támogatja.
A V2 nyers CSV külön `jacket_net_volume_ml` és `injection_net_volume_ml` oszlopot
tartalmaz. A nettó érték mindkét pumpánál az indításkori és aktuális maradék
térfogat különbsége, ezért negatív is lehet. Régi V1 fájl írásra történő
megnyitásakor előbb `_v1_backup.csv` biztonsági másolat készül, majd atomikus
cserével V2 munkafájl jön létre; csak olvasáskor ugyanez memóriabeli
normalizálással, a forrásfájl módosítása nélkül történik.

A szimulációs összeállításban a `MeasurementService.persistence_enabled=False` és
a `ProjectMeasurementWriter.enabled=False` egymástól függetlenül tiltja a tartós
írást. A letiltott writer a projekt és fázis útvonalát csak a korábbi éles adatok
kereséséhez számítja ki, könyvtárat vagy üres fájlt sem hoz létre. Hardvermód
aktiválásakor új, engedélyezett writer és `measurement_kind=live` konfigurációs
pillanatkép készül. Az előzménynézet és a NAS csak `*_live_raw.csv` fájlokat gyűjt,
ezért régi, nem jelölt szimulációs fájl nem keveredhet az éles adatok közé.

A felhasználói CSV-export választható elválasztót és tizedesvesszőt támogat. Az
Excel-export külön adat- és diagramlapot készít. Mindkét export kizárólag az aktív
fázis nyers fájlját használja; többfázisú export nem készül. A `NasSyncQueue` tartós SQLite
várólistája alkalmazás- és hálózati hiba után is megmarad. A
`BackgroundNasSynchronizer` külön szálon, ideiglenes fájl és atomikus csere
használatával másol; revíziószám akadályozza meg, hogy másolás közben érkező frissebb
rekord lekerüljön a várólistáról.

## Projektadatbázis

A `ProjectRepository` SQLite-adatbázisban tárolja a visszanyitható mérési
projekteket és azok rendezett szakaszait. A projekt létrehozásakor a konfiguráció
és a kalibráció teljes JSON-pillanatképe bekerül az adatbázisba, így egy későbbi
konfigurációváltozás nem írja át a korábbi mérés értelmezését. Az adatbázisséma az
SQLite `user_version` értékével verziózott; a jelenlegi verzió `3`, ismeretlenül
újabb sémát a program nem nyit meg.

A szakasz neve egyben a típusa; mellette a sorrendet, folyadékot/vegyszert, cél
nyomást, cél térfogatáramot és megjegyzést tároljuk. A külön régi típuskódot a
3-as sémamigráció automatikusan a névhez igazítja. A régi adatbázisok adatvesztés
nélkül migrálódnak; a projektkezelő szerkesztést, rendezést és törlést biztosít.
Projekt létrehozásakor nem kér kezelőnevet, és nincs projektgazda vagy hozzáférési
korlátozás: minden projekt minden kezelő számára elérhető.
Projekt törlésekor az SQLite idegen kulcsos `ON DELETE CASCADE` kapcsolat eltávolítja
a projekt fázis-metaadatait is. A nyers mérési fájlok nem az adatbázis tulajdonában
vannak, ezért archivált mérési adatként változatlanul megmaradnak. A UI törli az
érvénytelenné vált utolsó projekt- és fázisazonosítókat a `QSettings` INI-ből.

Az adatbázis 4-es sémája közös, név szerint kis- és nagybetűtől függetlenül egyedi
`pid_profiles` táblát tartalmaz. Egy profil a három erősítés, a hatásirány, a
kimeneti korlátok és a nyomásforrás értékeit tárolja létrehozási és módosítási
időbélyeggel. A mentés azonos névnél explicit UI-megerősítés után felülír, a törlés
szintén megerősítést kér. A kiválasztott profil azonosítója az INI-be és a projekt
konfigurációs pillanatképébe is bekerül; a profilérték kézi változtatása leválasztja
az űrlapot a mentett profilról, ezért a tárolt profil hallgatólagosan nem módosul.

## Szabályozási mag

A `ValveController` kézi és automata módot támogat. Automata módban a
besajtolópumpa nyomása vagy a vonali nyomás választható visszacsatolásként. A
`PidController` konfigurálható hatásirányt, kimeneti korlátot, mérési jelre számolt
derivált tagot és feltételes integrálást használ az integral windup ellen. Aktív
biztonsági hiba esetén nem állít elő kimeneti százalékot.

A szabályozási kimenet `0–100%` értékét az `AnalogValveActuator` a felhasználó által
megadott 0%/100% feszültségvégpontokból alakítja NI-kimenetté. A safe-state
feszültség szintén felhasználói eszközbeállítás; a fizikai helyességét helyszíni
teszttel kell igazolni.

## UI-integráció

A szimulátoros dashboard az SQLite repositoryból választ projektet és aktív mérési
szakaszt; szakasz nélkül mérés nem indítható. A felületről projekt hozható létre,
szakasz adható hozzá vagy nevezhető át, továbbá futás közben is módosíthatók a PID
erősítések, a hatásirány és a kimeneti korlátok. A PID újrakonfigurálása az aktuális
kimenetről inicializálja az integrált tagot. A leválasztás és a reteszelt hiba
nyugtázása kizárólag az alkalmazási állapotgépen keresztül történik.
Az aktív szakasz egyetlen `QComboBox` példánya az **Aktív projekt** összefoglaló
kártyán jelenik meg. Ez a runtime, az INI-ben mentett utolsó szakasz és a
fázisonkénti CSV-író közös kiválasztási forrása; nincs külön, eltérő állapotú
összefoglaló mező. Projekt vagy szakasz hiányában a dropdown letiltva, magyarázó
üresállapot-szöveggel jelenik meg.
A szakaszlista utolsó, nem szakaszazonosítót tartalmazó eleme az új szakasz
létrehozási művelete. Kiválasztásakor a combo előbb visszaáll az utolsó valódi
szakaszra, majd megnyitja a `StageSettingsDialog` ablakot. Elfogadáskor a teljes
szakaszmetaadat — a megjegyzést is beleértve — SQLite-ba kerül, a lista újratöltődik,
és az új szakasz válik aktívvá; megszakításkor nem változik az aktív szakasz.

A külön `CalibrationSettingsDialog` lapokra bontva kezeli a két érzékelő
kalibrációját és a biztonsági határértékeket. Elfogadás előtt validálja mindkét
lineáris kalibrációt és a `SafetyLimits` értékeit, majd a `MeasurementService`
alkalmazási rétegen keresztül alkalmazza őket. Futó mérés közben az ablak tiltott.
A köpenynyomás minimális többletének UI alsó korlátja 20 bar. Biztonsági
újrakonfigurálás nem törli a monitor reteszelt okait; azok továbbra is csak külön
kezelői nyugtázással oldhatók.

A nem modális, görgethető `MeasurementOverviewDialog` 250 ms-os UI-időzítővel
olvassa a főablak aktuális megjelenítési pillanatképét. Egy oldalon részletezi a
projektet, mérési fázist, eszközkapcsolatokat, pumpa- és NI-értékeket, szelepjelet,
kalibrációkat és biztonsági korlátokat. Az ablak nem kommunikál közvetlenül a
hardveradapterekkel.

A dashboard középső `QTabWidget` eleme külön **Élő mérés** és **Teljes mérés** fület
tartalmaz. A menüpont a teljes mérési fülre vált, nem modális ablakot nyit. A
`MeasurementHistoryView` a projekt külön fázis-CSV-it beolvasáskor memóriában,
UTC időbélyeg szerint rendezi, tartós összesített fájl létrehozása nélkül. Az
`active_stage` nyers mezőből első előfordulási
sorrendben építi fel a fázisválasztót. A szűrés az idő- és értéksorokat azonos
indexekkel kezeli. Az összes nézet külön, X-tengelyben összekapcsolt idővonalon
rajzolja a folytonos fázisszakaszokat, ezért a később megismételt azonos fázisok
nem olvadnak össze.

A főablak vízszintes `QSplitter` elrendezésében a grafikon kapja a nagyobb,
rugalmas területet. A bal oldali állapotpanel és a jobb oldali vezérlőpanel
`QScrollArea` konténerben marad: a bal panel szélessége a splitterrel együtt
csökkenthető, a hosszabb állapotszövegek sort törnek, elégtelen magasságnál pedig
függőleges görgetősáv jelenik meg. A jobb panel szintén elhagyja a fix
konténerszélességet; keskeny nézetben az űrlapsorok egymás alá törnek, ezért
vízszintes görgetősáv egyik oldalsávon sem jelenhet meg. A világos, sötét és rendszertéma
alkalmazásszinten érvényes, a választást `QSettings` őrzi.
A fő splitter 5 pixeles, témázott fogantyúkat és valós idejű átméretezést használ.
Az oldalsó vezérlőpanel űrlapja nem törheti a hosszabb címkéjű inputokat teljes
szélességű sorba: minden combo- és spinbox ugyanabban a közös, reszponzív
mezőoszlopban marad, ezért azonos szélességűek bármely panelszélességnél.
Az oldalsó `QScrollArea` elemek `AdjustIgnored`, a középső tab és grafikonok pedig
nulla minimumszélességű `Ignored/Expanding` méretpolitikát kapnak, ezért egy látható
scrollbar vagy a középső nézet méretjavaslata nem zárolhatja az oldalsávokat.

A projekt kiválasztását a modális `ProjectSelectionDialog` végzi. A korábbi
projekteket legutóbbi elöl sorrendben, a projektenként az INI-ben tárolt utolsó
mérési fázissal listázza, és új projektet is létre tud hozni az alapértelmezett
fázisokkal. Érvényes `project/last_project_id` hiányában a főablak első
megjelenésekor automatikusan megnyílik. A részletes szakaszszerkesztést továbbra is
a külön `ProjectSettingsDialog` biztosítja. A főablak csak az elfogadott projekt-
és szakaszazonosítót veszi át; a párbeszéd megszakítása nem módosítja az aktív
mérést.

## Háttér-vezérlési runtime

A `BackgroundControlRunner` dedikált Python-szálon, alapértelmezetten 100 ms-os
ütemben futtatja a mérés–biztonság–PID–aktuátor láncot. A Qt widgeteket nem éri el;
az eredményeket Qt signal továbbítja a főszálra. Az adatrögzítés ugyanebben a
háttérszálban, de külön 1–3600 másodperces ütemezéssel történik, ezért a gyors PID
ciklusok közül csak az esedékes rekord kerül CSV-be.

A runtime méri a ciklus végrehajtási idejét és késését. Határidőtúllépés,
kommunikációs kivétel vagy érvénytelen adat esetén minden safe-state műveletet
megkísérel, majd reteszelt hibát jelez a UI-nak. A futó beállítások zárral védett
pillanatképként frissülnek.

## Felhasználói beállítások

Windows alatt a belépési pont még a `QApplication` létrehozása előtt beállítja az
`AFKI.EOR.Control` AppUserModelID-t. Ezután a Qt alkalmazás- és ablakikon, valamint
a PyInstaller EXE erőforrásikonja ugyanazt a csomagolt `img/icon.png` forrást
használja, így a címsorban és a tálcán sem a Python alapikon jelenik meg.

A dashboard felhasználói preferenciái `QSettings` alatt, az `AFKI/EORControl`
alkalmazásnévvel tárolódnak. Az ablak bezárásakor mentésre kerül az aktív
projekt/szakasz, téma, vezérlési mód és forrás, PID, rögzítési időköz, kalibráció és
biztonsági határérték. Az INI projektenként is tárolja az utoljára használt mérési
fázist a projektválasztó számára. A visszatöltés a Qt widgetek tartományellenőrzésén keresztül
történik; ismeretlen enum, hibás szám vagy már nem létező projekt nem írja felül a
biztonságos alapértéket.
Az alkalmazás nem hagyatkozik a platformfüggő QSettings-alapértelmezésre: explicit
`config/AFKI/EORControl.ini` fájlt nyit. Üres INI esetén egyszer átmásolja a korábbi
Windows Registry `AFKI/EORControl` kulcsait. A témaváltás külön azonnali `sync()`
műveletet végez, és az írási hibát az állapotsorban jelzi.

## Eszközmód és kapcsolatpróba

A `DeviceSettingsDialog` tartósan tárolja a két ISCO soros konfigurációját, az NI
AI/AO csatornákat és a szelep feszültségkalibrációját. A két pumpa és a két NI
bemenet külön-külön, háttérszálon tesztelhető. Az összesített kapcsolatpróba is
négy független részpróbát futtat, ezért egy eszköz hibája nem takarja el a többi
eszköz sikeres kapcsolatát. A pumpapróba `RSVP`, `IDENTIFY` és státuszlekérdezést
végez, az NI-próba csak a kiválasztott analóg bemenetet olvassa. A szelep AO
kapcsolatát a felület nem jelöli teszteltnek, mert az fizikai írás nélkül nem
igazolható. A kapcsolatpróba nem küld `REMOTE`, `RUN` vagy más motorparancsot, és
nem hoz létre NI analóg kimeneti taskot.

Mind a négy szükséges részpróba sikere után a külön `HARDVER mód aktiválása`
gombbal a dashboard a
szimulátoros eszközstacket ISCO-, NI-DAQmx- és `AnalogValveActuator` példányokra
cseréli, új háttér-runtime-mal és ugyanahhoz az aktív projekthez tartozó CSV-naplóval. A módváltás csak
leválasztott `IDLE` állapotban engedélyezett. Új programindítás soha nem aktivál
automatikusan fizikai hardvert.

## Diagnosztika és Developer nézet

A `DiagnosticLogger` szálbiztos, kategóriaszűrt eseménynapló. Kikapcsolt állapotban
nem hoz létre fájlt. Bekapcsolva legfeljebb 5000 eseményt tart memóriában, és
append-only UTF-8 sorokat ír. A rendszer- és runtime események a
`data/logs/application.html`, a pumpa DASNET TX/RX és NI hardveresemények a külön
`data/logs/hardware_communication.html` fájlba kerülnek. A naplók append-only,
önálló HTML-riportok: keresőt, szintszűrőt, eseményszámlálót, rögzített fejlécű
táblázatot és szintenkénti színezést tartalmaznak. Minden dinamikus mező HTML-
escape-elten kerül a fájlba. Külön
kategória tartozik a két pumpához, a két NI bemenethez, a szelep AO-hoz, a
runtime-hoz és a rendszerhez.

A DASNET kliens a nyers TX/RX kereteket, timeoutokat, kerethibákat és pumpa
`PROBLEM=` válaszokat naplózza. Az NI adapter az AI-olvasást, AO-írást és
safe-state-et rögzíti. A `DeveloperViewDialog` 250 ms-os UI-időzítővel csak az új
memóriaeseményeket olvassa, így nem blokkolja a kommunikációs szálakat. A naplózás
engedélye és kategóriái `QSettings` alatt megmaradnak.
A nézet a többi beállítóablakkal azonos cím–magyarázat–kártya–tartalom–alsó
műveletsor hierarchiát használja. A reszponzív eseménytábla külön forrás, cél,
irány, kapcsolat és tartalom mezőben mutatja a kommunikáció útvonalát; a változó
szélességű oszlopok kitöltik az elérhető helyet, a technikai oszlopok pedig csak a
szükséges szélességet foglalják el.
A Developer mód külön, az INI-ben megmaradó kapcsoló. Normál módban a sikeres
eszközfelderítés technikai összegzése és az eszközkommunikációs Developer nézet
rejtve marad; a felderítési figyelmeztetések és hibák azonban minden módban
láthatók.
A Developer menü **Szimulációs mód** kapcsolója az aktuális `RunMode` állapotát
mutatja. Bekapcsolása csak leállított, leválasztott `IDLE` állapotban cserélheti le
a hardveradaptereket szimulátorokra. Az új `MeasurementService` és
`ProjectMeasurementWriter` külön-külön is tiltott perzisztenciával indul. A
kapcsoló kikapcsolása a meglévő, felderítést és kezelői megerősítést végző
eszközbeállítási folyamatot nyitja meg; közvetlenül nem aktivál fizikai kimenetet.

A `DashboardWindow` állandó, szöveges mód- és riasztássávot tart fenn. A reteszelt
riasztás időpontot, okot, automatikus safe-state műveletet és kezelői következő
lépést tartalmaz, és csak sikeres hibanyugtázáskor törlődik. A `QSystemTrayIcon`
ezzel párhuzamosan Windows rendszerértesítést jelenít meg; minimalizált vagy
inaktív ablaknál a `QApplication.alert()` a tálcagombot is figyelemkérésre állítja.
Az értesítési kulcs megakadályozza, hogy ugyanaz a biztonsági ok minden mérési
ciklusban újra megjelenjen.
A tálcaikon helyi menüje ablak-visszaállítási és `Program bezárása` műveletet ad.
Az utóbbi a `DashboardWindow.closeEvent()` útvonalán állítja le a runtime-ot,
leválasztja az eszközöket, safe-state-et kér, majd lezárja a vezérlési ciklust,
NAS-szinkront és projekt-adatbázist; ezután eltávolítja a tálcaikont és kilépteti
a Qt alkalmazást.

A mérésindítás első, háttérszálas `observe_once` ciklusából a főszál
`PreflightReport` modellt készít. A `PreflightDialog` minden tételt állapottal,
részlettel és javítási teendővel mutat; `FAILED` tételnél nincs indítógomb, a
`WARNING` tételekhez pedig külön jelölőnégyzetes kezelői jóváhagyás szükséges.
Csak elfogadott jelentés után indul el az állapotgép és a háttér-runtime.

## Terminálos vezérlés

Az `AFKI-EOR.exe terminal` ugyanabból a PyInstaller onefile kiadásból indít
interaktív parancssori munkamenetet. A `TerminalApplication` nem kommunikál
közvetlenül eszközdriverrel: a `DeviceControlService`, `ControlLoop`,
`BackgroundControlRunner`, `MeasurementService` és `SafetyMonitor` rétegeket
használja. Emiatt az állapotátmenetek, a reteszelt hiba és a safe-state viselkedés
azonos az alkalmazás többi részével.

A terminál összeállítása szimulált pumpákat, DAQ-ot és szelepet, valamint
`persistence_enabled=False` mérési szolgáltatást és eldobó writert használ. Így a
terminálos szimuláció akkor sem készíthet fájlt, ha a háttér-runtime perzisztálást
kér. A folyamatból kilépés és a `Ctrl+C` előbb leállítja a runtime-ot, safe-state-et
kér, majd leválasztja az eszközöket. Fizikai hardverösszeállítás nincs bekötve a
terminál belépési pontjába.

A Windows onefile EXE konzolos alrendszerrel készül, hogy a terminál mód szabályos
stdin/stdout kapcsolatot kapjon. Grafikus, dupla kattintásos indításnál a belépési
pont csak a saját, külön létrehozott konzolablakát rejti el; egy már megnyitott
PowerShell vagy parancssor konzolát nem zárja be.

## Felügyelt pumpavezérlés

A `PumpControlService` külön alkalmazási állapotgépet tart fenn mindkét pumpához:
helyi, remote, konfigurált és futó állapotot követ. Az írási parancsok csak sikeres
hardveraktiválás után engedélyezettek. A kezelő `CONST FLOW` vagy `CONST PRESS`
módot és célértéket állíthat, majd külön megerősítéssel indíthatja a pumpát.

A besajtolópumpa `RUN` előtt a szolgáltatás friss státuszt olvas mindkét pumpáról,
és 20 bar alatti köpenynyomás-többletnél megtagadja az indítást. A `STOP ALL`
mindkét STOP-ot egymástól függetlenül megkísérli. `CLEAR` és `LOCAL` futó
pumpánál tiltott. A dashboard globális STOP-ja szinkronizálja, a vészállapot pedig
visszavonja a pumpavezérlési jogosultságot.

A manuális pumpa- és szelepvezérlés csak Developer módban, egy közös ablakból
érhető el. Részleges hardver tesztmódban az ablak `IDLE` állapotból is megnyitható,
és a két pumpa külön kapcsolható, azonosítható és választható le. A pumpastátuszok
és a két NI-bemenet külön hibahatárral olvasható, ezért egy hiányzó érzékelő nem
rejti el a működő eszközök adatait. Minden pumpa-RUN előtt továbbra is a teljes
`SafetyMonitor`-lánc fut le; a kézi szelepjel pedig a `ControlLoop` útvonalán jut
az aktuátorra. Hiányos biztonsági telemetria a RUN és nem-SAFE szelepírást tiltja,
de a STOP, safe-state és leválasztás mindig külön-külön megkísérelhető.

A Developer menüpont Developer módban mindig kattintható. Futó mérés vagy
reteszelt hiba esetén a dashboard konkrét hibaüzenetben jelzi a szükséges
előfeltételt. A vezérlőablak a telemetria-lekérdezést és a kezelői parancsot külön
foglalt állapottal kezeli: a lekérdezés közben érkező parancs sorba áll, majd a
lekérdezés befejezése után lefut. A felület minden parancsnál folyamatban, sikeres
vagy sikertelen állapotot jelenít meg.

Az A/B/C/D csatorna az írási parancsokban is érvényesül: A csatornán utótag
nélküli, B/C/D csatornán `FLOWx`, `PRESSx`, `RUNx`, `STOPx` alak készül.

## Vezetett eszközteszt

A `ConnectionTestRegistry` eszközönként, az érintett konfiguráció ujjlenyomatával
tárolja a csak olvasásos kapcsolatpróbát. A `device_testing.py` hardverfüggetlen
állapotgépe kezeli a funkcionális tesztet, a központi megszakítást és a JSON-jelentést.
A Qt `DeviceTestWizard` signalokon veszi át a háttérműveletek eredményét; hosszú
szenzorteszt nem fut a UI-szálon. Funkcionális teszt és normál runtime nem futhat
egyszerre, bezárás és kivétel ugyanazt a STOP/SAFE útvonalat használja.
