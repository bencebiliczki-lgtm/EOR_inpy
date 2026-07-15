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
biztonságilag kiértékel, majd projektenként elkülönített append-only nyers CSV-fájlba ír. Interlock vagy
eszközhiba esetén mindkét pumpán szimulált STOP-ot, a DAQ-on pedig biztonságos
állapotot kér. A ciklust a `BackgroundControlRunner` külön munkaszálon futtatja;
Qt főszálból közvetlenül nem fut.

## Projektfájlok, export és NAS

A `ProjectMeasurementWriter` az aktív projekt azonosítójából és Windows-biztos
nevéből és dátumából `projects/<év>/<dátum>_<azonosító>_<név>` könyvtárat és nyers
fájlnevet képez. Projektváltáskor lezárja az előző írót,
így projektek adatai nem keveredhetnek. A nyers CSV UTF-8, append-only, minden teljes
sor után flush és `fsync` történik.
Ugyanitt hordozható `project.json`, `config_snapshot.json` és
`calibration_snapshot.json` készül. A nyers CSV pontosvesszős és tizedesvesszős;
a beolvasó a korábbi vesszős fájlokat is támogatja.

A felhasználói CSV-export választható elválasztót és tizedesvesszőt támogat. Az
Excel-export külön adat- és diagramlapot készít. A `NasSyncQueue` tartós SQLite
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

A kalibrációs és biztonsági panel a `MeasurementService` alkalmazási rétegen
keresztül frissíti a két lineáris kalibrációt és a `SafetyMonitor` határértékeit.
Futó mérés közben a panel tiltott. Biztonsági újrakonfigurálás nem törli a monitor
reteszelt okait; azok továbbra is csak külön kezelői nyugtázással oldhatók.

A főablak vízszintes `QSplitter` elrendezésében a grafikon kapja a nagyobb,
rugalmas területet, a jobb oldali vezérlőpanel pedig kis ablakszélességnél
görgethető marad. A státuszkártyák 3×2 rácsban törnek. A világos, sötét és
rendszertéma alkalmazásszinten érvényes, a választást `QSettings` őrzi.

A projekt kiválasztása és szerkesztése külön modális `ProjectSettingsDialog`
ablakban történik, amely a főablak `Projekt` menüjéből vagy az aktív projekt
összefoglalójából nyitható meg. A főablak csak az elfogadott projekt- és
szakaszazonosítót veszi át; a párbeszéd megszakítása nem módosítja az aktív mérést.

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

A dashboard felhasználói preferenciái `QSettings` alatt, az `AFKI/EORControl`
alkalmazásnévvel tárolódnak. Az ablak bezárásakor mentésre kerül az aktív
projekt/szakasz, téma, vezérlési mód és forrás, PID, rögzítési időköz, kalibráció és
biztonsági határérték. A visszatöltés a Qt widgetek tartományellenőrzésén keresztül
történik; ismeretlen enum, hibás szám vagy már nem létező projekt nem írja felül a
biztonságos alapértéket.

## Eszközmód és kapcsolatpróba

A `DeviceSettingsDialog` tartósan tárolja a két ISCO soros konfigurációját, az NI
AI/AO csatornákat és a szelep feszültségkalibrációját. A kapcsolatpróba külön
háttérszálon fut: mindkét pumpán `RSVP`, `IDENTIFY` és státuszlekérdezést végez,
majd beolvassa a két NI analóg bemenetet. Nem küld `REMOTE`, `RUN` vagy más
motorparancsot, és nem hoz létre NI analóg kimeneti taskot.

Sikeres teszt után a külön `HARDVER mód aktiválása` gombbal a dashboard a
szimulátoros eszközstacket ISCO-, NI-DAQmx- és `AnalogValveActuator` példányokra
cseréli, új háttér-runtime-mal és ugyanahhoz az aktív projekthez tartozó CSV-naplóval. A módváltás csak
leválasztott `IDLE` állapotban engedélyezett. Új programindítás soha nem aktivál
automatikusan fizikai hardvert.

## Diagnosztika és Developer nézet

A `DiagnosticLogger` szálbiztos, kategóriaszűrt eseménynapló. Kikapcsolt állapotban
nem hoz létre fájlt. Bekapcsolva legfeljebb 5000 eseményt tart memóriában, és
append-only UTF-8 sorokat ír a `data/logs/communication.log` fájlba. Külön
kategória tartozik a két pumpához, a két NI bemenethez, a szelep AO-hoz, a
runtime-hoz és a rendszerhez.

A DASNET kliens a nyers TX/RX kereteket, timeoutokat, kerethibákat és pumpa
`PROBLEM=` válaszokat naplózza. Az NI adapter az AI-olvasást, AO-írást és
safe-state-et rögzíti. A `DeveloperViewDialog` 250 ms-os UI-időzítővel csak az új
memóriaeseményeket olvassa, így nem blokkolja a kommunikációs szálakat. A naplózás
engedélye és kategóriái `QSettings` alatt megmaradnak.

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

Az A/B/C/D csatorna az írási parancsokban is érvényesül: A csatornán utótag
nélküli, B/C/D csatornán `FLOWx`, `PRESSx`, `RUNx`, `STOPx` alak készül.
