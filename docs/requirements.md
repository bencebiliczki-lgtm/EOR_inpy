# Funkcionális követelmények

## Rendszercél

A rendszer egy EOR kőzetminta-vizsgálat során gyűjti, megjeleníti és menti a pumpák, nyomásmérők és a szabályozószelep adatait, valamint biztonsági felügyelet mellett támogatja az eszközök vezérlését.

## Mért és rögzített adatok

Minden mintavételi időpontban rögzítendő:

- köpenypumpa nyomása, térfogatárama és maradék cilindertérfogata;
- besajtoló pumpa nyomása, térfogatárama és maradék cilindertérfogata;
- a mérés kezdete óta besajtolt térfogat;
- vonali nyomás;
- differenciálnyomás;
- szelepvezérlés százalékos értéke;
- aktív mérési szakasz;
- rendszerállapot, adatminőség és aktív hibák.

A mintavételi gyakoriság 1 másodperc és 1 óra között konfigurálható. A vezérlési ciklus gyakorisága ettől független, gyorsabb belső beállítás legyen.

## Mérési projekt

- Egy mérés egy visszanyitható projekt.
- A projekt tartalmazza a nevét, létrehozási idejét, megjegyzéseit, konfigurációját
  és kalibrációs pillanatképét. A projektek közösek: nincs tulajdonosuk, minden
  kezelő dolgozhat minden mérésen.
- Egy projekt tetszőleges számú, hozzáadható és átnevezhető szakaszt tartalmazhat.
- A projektválasztó képernyő listázza a korábbi projekteket és projektenként az
  utoljára használt mérési fázist, valamint új projekt létrehozását is biztosítja.
- Az „Aktív projekt” dashboard-kártya dropdownban listázza az aktív projekt mérési
  szakaszait, és közvetlenül lehetővé teszi az aktív szakasz váltását.
- A szakasz-dropdown utolsó eleme mindig **„+ Új szakasz hozzáadása…”** legyen.
  Kiválasztása külön ablakot nyisson név/típus, folyadék, célértékek és megjegyzés
  megadásához; megszakításkor az előző aktív szakasz maradjon kiválasztva.
- Ha nincs érvényes utoljára megnyitott projekt, az alkalmazás indításkor
  automatikusan megjeleníti a projektválasztót, és nem választ projektet önkényesen.
- Projekt a projektválasztóból és a projektkezelőből is törölhető legyen, kötelező
  megerősítéssel. A törlés távolítsa el a projektet és fázis-metaadatait, valamint az
  utolsó kiválasztás érvénytelenné vált hivatkozásait, de ne törölje automatikusan a
  nyers mérési CSV-ket.
- A szakasz neve egyben a típusa. Emellett a folyadék/vegyszer, cél nyomás, cél
  térfogatáram és megjegyzés szerkeszthető; a szakaszok rendezhetők és törölhetők.
- Példák: hidegvizes, melegvizes, olajkiszorításos és különböző vegyszeres szakaszok.
- A projekt és a mérési fázis neve jelenjen meg az exportált fájl nevében.
- Javasolt NAS-struktúra: `EOR mérés/<év>/<projekt neve>/`.

## Megjelenítés

- Az embernek szánt időpontok a magyar `Europe/Budapest` időzónában jelenjenek
  meg, a téli/nyári időszámítást automatikusan követve. A nyers mérési és
  projektadatok időbélyege továbbra is UTC legyen.

- A szerkeszthető szövegmezők háttere legyen jól olvasható minden témában. A
  dashboard tetején mindig látható, szöveggel is azonosítható üzemmód- és
  riasztássáv legyen; a szín nem lehet az egyetlen állapothordozó. Háttérben vagy
  minimalizálva a Windows-értesítés és a tálcagomb is kérjen figyelmet.
- A bal és jobb oldalsáv splitterrel akkor is átméretezhető maradjon, amikor a
  tartalma miatt függőleges scrollbar jelenik meg.
- A jobb oldali vezérlőpanel minden inputmezője azonos szélességű legyen az
  oldalsáv bármely szélességén; hosszabb címke nem törheti a hozzá tartozó mezőt
  teljes szélességű külön sorba.
- Kapcsolati állapot minden eszközhöz.
- Aktuális értékek és aktív riasztások jól láthatóan.
- A köpeny- és besajtolási nyomás aktuális különbsége külön kijelzőn jelenjen meg.
- Az adatrögzítés állapota mutassa az aktív fájlt, méretet, utolsó rögzítési ciklust
  és a NAS-szinkron várólistáját.
- Az elmúlt 10 perc élő diagramja.
- A besajtolási térfogatáram külön, `ml/h` egységű élő diagramja.
- A teljes rögzített mérés a dashboard középső területének külön füle legyen; ne
  külön felugró ablakban jelenjen meg.
- A teljes mérés diagramja legyen mérési fázisra szűrhető, és külön idővonalon
  mutassa a fázisváltásokat, az ismételt fázisszakaszokat is elkülönítve.
- A teljes mérés nézetben a grafikon mellett kezelői táblázat is legyen. A két
  megjelenítés ugyanazt a fázis- és időtartomány-szűrést használja, a táblázat
  oszlopai egyezzenek az aktuális Excel-export oszlopaival, az időpontok magyar
  helyi időben jelenjenek meg. Nagy adathalmaznál lapozás korlátozza az egyszerre
  megjelenített sorok számát.
- Választható adatsorok és szabadon skálázható tengelyek.
- A szelep aktuális állásának folyamatos kijelzése.
- Mindkét pumpánál jelenjen meg a mérés indítása óta számított, előjeles nettó
  térfogatváltozás; a negatív értéket a felület nem rejtheti el.
- Külön, görgethető mérési áttekintő ablak jelenítse meg részletesen az aktív
  projektet és fázist, az eszközkapcsolatokat, az összes élő mérési értéket, a
  szelepjelet, a kalibrációkat és a biztonsági határértékeket.
- A vonali nyomás egyben a berendezés belépő nyomása; nem kezelhető külön,
  duplikált érzékelőként vagy adatsorként.

## Kalibráció

A nyomásmérőkhöz megadható legalább két kalibrációs pont: alsó/felső feszültség és
a hozzájuk tartozó fizikai érték. A kalibráció és a biztonsági határértékek külön,
áttekinthető felugró ablakban szerkeszthetők; futó mérés közben nem módosíthatók.
A konfiguráció legyen verziózott, és a mérés indulásakor készüljön róla pillanatkép.

## Vezérlés

- Az utoljára sikeresen választott HARDVER mód minden alkalmazásindításkor
  maradjon az előnyben részesített mód; a fizikai kimenetek külön kezelői
  megerősítése ettől még kötelező.
- Normál kezelői módban a HARDVER mód aktiválása és az élő hardverkapcsolat egy
  művelet legyen. Sikeres aktiválás után a kapcsolat a mérések között is maradjon
  `READY` állapotban; külön Csatlakozás és Leválasztás gomb ne jelenjen meg.
- A HARDVER mód kezelői engedélyezése és az NI fizikai analóg kimenetének
  engedélyezése két egymást követő biztonsági lépés legyen. Előbb az
  alkalmazásszintű hardverengedély, utána – csak konfigurált szelepkimenetnél –
  az NI-kimenet engedélye jöjjön létre, és csak ezután indulhat az
  eszközkapcsolódás. Safe-state vagy normál mérésleállítás mindkét engedélyt
  érvénytelenítse; új méréshez ismételt, explicit hardvermód-aktiválás szükséges.
- Aktív HARDVER + `READY` állapotban a dashboard mérésindítás nélkül is
  folyamatosan jelenítse meg a két pumpa cache-elt nyomását, térfogatáramát,
  maradék térfogatát és telemetriaállapotát, valamint az engedélyezett NI
  nyomásbemenetek aktuális értékét és a szelep biztonságos alapállapotát. Ez a
  szolgáltatási nézet ne írjon mérési rekordot és ne adjon pontot a mérési
  grafikonhoz; blokkoló I/O nem futhat a Qt UI-szálán.
- A mérés kezelői gombjai: **Mérés indítása**, **Mérés
  szüneteltetése/folytatása** és **Mérés leállítása**. Szünetben a PID és az
  adatmentés álljon, de a biztonsági felügyelet fusson tovább és a fizikai
  kimenet maradjon a szünet kezdeti értékén. Leállításkor STOP/safe-state után
  a kapcsolat maradjon élő, a dashboard élő grafikonja, táblázata és értékei
  kerüljenek alaphelyzetbe.
- Kritikus hardverhiba, kapcsolatvesztés, biztonsági interlock, watchdoghiba vagy
  vészleállítás után a program minden portot best-effort módon zárjon le, dobja
  el az aktív hardvermódot, mutasson konkrét hibaüzenetet, majd automatikusan
  nyissa meg az Eszközbeállításokat. Nem kritikus előellenőrzési hiba ne bontsa
  az élő hardverkapcsolatot.
- Hardveres módban a mérésindítás külön ablakban kérje be mindkét pumpa elérendő
  kezdőnyomását, saját hardveres nyomáshatárát, a köpeny nyomásfelépítési
  térfogatáramát, a besajtoló térfogatáramát és a nyomástöbblet stabilitási idejét.
  A program a dokumentált `MAXPRESS` paranccsal állítsa be a két pumpa saját
  határát még a `RUN` előtt. A köpenypumpa induljon elsőként; amikor a legalább
  20 bar köpeny–besajtoló többlet a beállított ideig stabil, induljon el a
  besajtolópumpa is. Ezután mindkét pumpa együtt haladjon a kezdő célértéke felé,
  a köpeny pedig saját célján STOP után váltson állandó nyomástartásra. A
  nyomástöbbletet minden felügyeleti ciklus ellenőrizze. A PID- és adatrögzítési
  ciklus csak mindkét kezdőnyomás elérése után kezdődhet.
- A pumpatelemetria minőségét mezőnként kell nyilvántartani. A nyomás
  biztonságkritikus; elavulása reteszelt hibát okozhat. A FLOW vagy VOLA önálló
  elavulása `DEGRADED` kapcsolatot jelezzen és maradjon látható, de önmagában ne
  állítsa le a nyomásszabályozást vagy a teljes mérést. A kapcsolatindításhoz
  nyomás és alapstátusz szükséges; a lassú mezők háttérben töltődhetnek fel.
- Developer/szerviz módban a közös Beállítások ablak külön
  **Pumpatelemetria / STALE** oldala szerkessze a nyomás- és lassú polling
  időközét, a két mezőcsoport STALE-határát és a kezdő telemetria timeoutját.
  STALE-határ nem lehet rövidebb a hozzá tartozó polling időköznél. A felület
  jelezze, hogy a nyomás STALE-határának növelése késlelteti a kapcsolatvesztés
  felismerését; az értékek csak a következő hardveraktiváláskor lépjenek életbe.
- A köpenynyomás felépülése alatt minden egyéb szenzor-, kapcsolat- és
  nyomáshatár maradjon aktív. Timeout, kezelői megszakítás vagy bármely hiba
  mindkét pumpán STOP-ot és a mérési runtime indításának tiltását váltsa ki.
- Az alkalmazás ugyanabból a onefile EXE-ből `terminal` argumentummal interaktív,
  állapotot megőrző parancssori vezérlést biztosítson.
- A terminálból elérhető legyen a státusz, csatlakozás, mérésindítás/-leállítás,
  vészleállítás, hibanyugtázás, leválasztás, kézi/automata szabályozás, mérési
  szakasz és adatrögzítési időköz beállítása.
- A terminál mód alapértelmezetten és jelen kiadásban kizárólag szimulációt
  vezérelhet; nem írhat CSV-t, nem hozhat létre NAS-feladatot és nem érhet el
  fizikai kimenetet. A hardveres terminálvezérlés csak külön, később jóváhagyott,
  a felderítést és explicit kezelői megerősítést megtartó terv alapján engedhető.
- Külön előkészítési nézet a pumpák felügyelt vezérlésére.
- Developer tesztmódban az eszközök külön-külön kapcsolhatók és kérdezhetők le;
  egy még be nem kötött eszköz hibája nem rejtheti el a többi sikeres kapcsolatát,
  és nem tilthatja azok STOP vagy leválasztási műveletét. Normál méréshez továbbra
  is minden, az aktív mérési profilban kötelező eszköz szükséges. A vonali
  nyomásmérő opcionális; hiányában nem olvasható és nem választható PID-forrásnak,
  de a besajtolópumpa nyomásáról szabályozott mérést nem blokkolhatja.
- A hozzáadott eszközök listája projektenként tárolódjon és a
  Projektbeállításokban kapcsolatpróba vagy helyszíni validáció nélkül legyen
  szerkeszthető. Az Eszközbeállítások a kiválasztott projekt profilját használja;
  eltérő aktív hardverprofillal normál mérés nem indulhat.
- A manuális hardvervezérlés külön biztonsági profilt használjon: a megcélzott
  pumpa kapcsolatát, véges saját státuszát és maximális nyomását, illetve a
  szelep 0–100%-os tartományát ellenőrizze. Nem kapcsolódó, ki nem épített
  érzékelő hiánya nem tilthatja a manuális parancsot. A fizikai kimenet
  megerősítése, a STOP/safe-state elsőbbsége és a véges kommunikációs timeout megmarad.
- Developer közvetlen eszközkezelésben minden hozzáadott eszköz a többi eszköz
  kapcsolati eredményétől és a globális hardvermódtól függetlenül legyen
  kezelhető. A csak szelepet tartalmazó profil is megnyithassa ezt a felületet;
  minden tényleges AO-írás külön megerősítést igényeljen.
- Minden beállítási menüpont egy közös, átméretezhető, bal oldali
  kategórianavigációs ablak megfelelő oldalát nyissa meg.
- A diagnosztikai napló alapértelmezetten legyen engedélyezve.
- Az élő grafikon a figyelmeztetést sárga, a kritikus riasztást piros ponttal
  jelölje; hover esetén jelenjen meg a magyar idő, szakasz és hibarészlet.
- Automatikus nyomásfelépítés közben a köpeny- és besajtolási nyomás megengedett különbségének betartása.
- A szelep automata és kézi módban működhet.
- Automata módban a szabályozási forrás választható legyen: besajtoló pumpa nyomása vagy vonali nyomásmérő.
- A PID paraméterei módosíthatók és névvel menthető profilokba rendezhetők.
- A részletes PID-hangolás és a felügyelt manuális hardvervezérlés csak Developer
  módban jelenhet meg; a normál kezelői nézet az üzemi műveletekre korlátozódjon.
- Developer módban a háttér-vezérlési ciklus időköze és watchdog-tűrése külön
  beállítható és tartósan mentett legyen. Módosításuk futó mérés közben tilos;
  a watchdog és a ciklushiba miatti safe-state nem kapcsolható ki.
- Cél a beállított nyomás ±1 bar tartása, ennek igazolási módszerét még rögzíteni kell.

## Adatmentés

- A nyers mérési adatok folyamatos, összeomlástűrő helyi mentése kötelező.
- Az NI nyomásbemeneteknél a ciklusonkénti mintaburst mediánját és az abból képzett
  EMA-szűrt nyomást külön kell megőrizni. A PID és a kijelzés a szűrt értéket, a
  kemény nyomás-interlock a szűretlen értéket használja.
- Nyers mérési adat kizárólag explicit, megerősített hardvermódban menthető. A
  szimuláció nem hozhat létre vagy módosíthat CSV-t, projekt-pillanatképet vagy
  NAS-szinkronfeladatot, még akkor sem, ha a runtime perzisztálást kérne.
- A **SZIMULÁCIÓ – nincs fizikai kimenet és nincs mérési adatmentés**, illetve a
  **HARDVER – fizikai berendezés vezérlése és mérési adatmentés** állapot mindig
  látható dashboard-sávban jelenjen meg.
- A biztonsági hiba reteszelt, állandó dashboard-sávban maradjon meg biztonságos
  bezárásig,
  és tartalmazza az időpontot, okot, automatikus műveletet és következő lépést.
  Külön hibanyugtázó gomb nincs. A riasztás bezárása aktív hibánál friss
  szenzor- és biztonsági ellenőrzést igényel; sikertelen ellenőrzéskor a retesz
  és a riasztás megmarad. Biztonságos szimulációban a bezárás után az alkalmazás
  automatikusan `READY` állapotba tér vissza.
  Minimalizált vagy háttérben lévő alkalmazásnál Windows tálcaértesítés és
  tálcagomb-figyelmeztetés is szükséges; azonos aktív hiba ciklusonként ne
  ismétlődjön.
- Developer módban külön **Szimulációs mód** kapcsoló legyen. Az átváltás csak
  leválasztott, IDLE állapotban történhet; a szimulációs runtime fizikai kimenetet
  és mérési perzisztenciát nem használhat.
- Developer módban a szimuláció külön hibatesztelő beállítási oldalt kapjon.
  A pumpamodell explicit `LOCAL/REMOTE/CONFIGURED/RUNNING/HOLDING/STOPPED/FAULT`
  állapotokat, időfüggő nyomásrámpát, térfogyást és a PC-től független saját
  túlnyomásvédelmet modellezzen. Legyen determinisztikus virtuális idő, állítható
  pumpaválasz-késés, valamint pumpa-STALE, kapcsolatvesztés, üres cilinder,
  motorhiba, túlnyomás, NI spike/fagyás/kiesés és szelepberagadás/fordított irány
  injektálható. Minden injektálás kerüljön a diagnosztikai naplóba.
- Az éles nyers fájl neve `_live_raw.csv` végződést kapjon; automatikus
  előzménybetöltés és NAS-szinkron csak ilyen, egyértelműen jelölt fájlt használhat.
- Minden mérési fázis külön nyers CSV-fájlba kerüljön; fázisváltás nem írhatja az
  új fázis rekordjait az előző fázis fájljába.
- A teljes mérés nézet a külön fázisfájlokat csak megjelenítéskor egyesítheti
  memóriában. Összesített nyers CSV nem készülhet.
- A felhasználói CSV-export mindig egy kiválasztott mérési fázisra vonatkozzon.
  Projektenként egy Excel-munkafüzet készüljön, amelyben minden lezárt mérési
  szakasz saját, a szakasz nevét viselő munkalapot kap. A munkalapon legyenek
  szűrhető mérési oszlopok és beágyazott nyomás-/szelepdiagram.
- Az Excel adott szakaszlapja csak a szakasz lezárásakor, háttérben készülhet el
  vagy frissülhet; futó fázisból kézi Excel-export nem indítható. Az elkészült
  projekt-munkafüzetet az
  engedélyezett NAS-szinkron ugyanúgy tartós várólistán kezelje.
- A NAS-ra írás ne blokkolja az adatgyűjtést; hálózati hiba esetén helyi várólista szükséges.
- A nyers és felhasználói magyar CSV pontosvesszős, tizedesvesszős formátumot használ;
  a felhasználói exportnál más elválasztó és tizedespont is választható.
- Az export nem helyettesíti a belső nyers adatforrást.
