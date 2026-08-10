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
- A Megjelenés beállításokban a bal és jobb oldalsáv külön kapcsolható legyen. A
  dashboard elrendezésszerkesztőjében minden oldalsó kártya × gombbal elrejthető,
  az elérhető elemek pedig az ablak alján vízszintes sávban legyenek
  visszakapcsolhatók. A választás legyen tartós. A STOP és a szoftveres
  vészleállítás elérhetőségét futó mérés közben elrejtés nem szüntetheti meg.
- A jobb oldali vezérlőpanel minden inputmezője azonos szélességű legyen az
  oldalsáv bármely szélességén; hosszabb címke nem törheti a hozzá tartozó mezőt
  teljes szélességű külön sorba.
- Kapcsolati állapot minden eszközhöz.
- Aktuális értékek és aktív riasztások jól láthatóan.
- A köpeny- és besajtolási nyomás aktuális különbsége külön kijelzőn jelenjen meg.
- A dashboard nyomáskijelzői magyar tizedesvesszőt és legfeljebb három
  tizedesjegyet használjanak; felesleges záró nullákat ne jelenítsenek meg.
- A pumpák térfogatáramát a teljes alkalmazás `ml/h` egységben kezeli. Az
  `ML/HR` beállítás utáni egység nélküli `FLOW` és `SETFLOW` válaszokon tilos
  `ml/min` átváltást alkalmazni.
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
- A pumpatelemetria minden mezőjének (`pressure`, `flow`, `volume`, `status`)
  minőségátmenete külön, strukturált diagnosztikai esemény legyen. A napló
  tartalmazza a mérés- és szakaszazonosítót, az előző/új minőséget, az adat
  korát és STALE-határát, az utolsó sikeres időbélyeget és parancsidőt, valamint
  az aktivált safety szabályt, stratégiát, műveletet és eredményt. Az első
  átmenet és a helyreállás mindig naplózandó; tartós hibánál ismétlési
  összefoglaló használható ciklusonkénti azonos esemény helyett.
- A diagnosztikai naplók napi vagy méretalapú rotációja, legalább 30 napos
  alapértelmezett megőrzése és opcionális gzip-tömörítése legyen beállítható.
  A tisztítás indításkor és naponta háttérfeladatban fusson. Csak az ismert
  alkalmazás-, hardverkommunikációs és mérési diagnosztikai naplóminták
  kezelhetők; az aktív, megjelölt/zárolt vagy nyitott méréshez tartozó fájl,
  továbbá CSV, Excel és SQLite adat nem törölhető.
- Aktív HARDVER + `READY` állapotban a dashboard mérésindítás nélkül is
  folyamatosan jelenítse meg a két pumpa cache-elt nyomását, térfogatáramát,
  maradék térfogatát és telemetriaállapotát, valamint az engedélyezett NI
  nyomásbemenetek aktuális értékét és a szelep biztonságos alapállapotát. Ez a
  szolgáltatási nézet ne írjon mérési rekordot és ne adjon pontot a mérési
  grafikonhoz; blokkoló I/O nem futhat a Qt UI-szálán.
- A pumpák `PREPARING` előkészítési állapotában a biztonsági mintavételekből
  folyamatosan frissüljön a dashboard két pumpanyomása, vonali és
  differenciálnyomása, valamint a köpeny–besajtolás nyomáskülönbsége. Az
  előkészítési kijelzés nem indíthatja el idő előtt a PID-et, az adatmentést vagy
  a mérési grafikon pontgyűjtését.
- A mérés kezelői gombjai: **Mérés indítása**, **Előkészítés**, **Mérés
  szüneteltetése/folytatása** és **Mérés leállítása**. Szünetben a PID és az
  adatmentés álljon, de a biztonsági felügyelet fusson tovább és a fizikai
  kimenet maradjon a szünet kezdeti értékén. Leállításkor STOP/safe-state után
  a kapcsolat maradjon élő, a dashboard élő grafikonja, táblázata és értékei
  kerüljenek alaphelyzetbe.
- Kritikus hardverhiba, kapcsolatvesztés, biztonsági interlock, watchdoghiba vagy
  vészleállítás után a program best-effort STOP/safe-state-et kérjen, állítsa
  le a runtime-ot, és maradjon reteszelt `FAULT` állapotban. A hardvermódot és
  a munkamenet engedélyét megőrizheti, de a kezelői nyugtázásig nem indíthat
  automatikus újraellenőrzést, mérést vagy vezérlési runtime-ot. Sikeres friss
  biztonsági ellenőrzés és kezelői nyugtázás után egyetlen, csak olvasási
  hardverállapot-frissítés induljon. Nem kritikus előellenőrzési hiba ne bontsa
  az élő hardverkapcsolatot.
- Hardveres és szimulációs módban az **Előkészítés** gomb külön ablakban
  kérje be mindkét pumpa elérendő
  kezdőnyomását, saját hardveres nyomáshatárát, a köpeny nyomásfelépítési
  térfogatáramát, a besajtoló térfogatáramát és a nyomástöbblet stabilitási idejét.
  A program a dokumentált `MAXPRESS` paranccsal állítsa be a két pumpa saját
  határát még a `RUN` előtt. A köpenypumpa induljon elsőként, érje el a saját
  célnyomását és legalább a Beállításokban megadott köpeny–besajtoló többletet,
  majd külön ciklusokban hajtsa végre a `STOP → CONST PRESS → RUN` átállást. A
  köpeny nyomástartásának a beállított ideig stabilnak kell maradnia, és csak
  ezután konfigurálható és indítható a besajtolópumpa. A
  nyomástöbblet kizárólag a besajtolópumpa `RUN` előtti indítási engedélyfeltétel:
  a `RUN` után és a mérési ciklusban nem kell fenntartani. A köpenypumpa a
  kezelő által megadott fix nyomáscélt tartsa, ne kövesse a besajtolási nyomást.
  A BES pumpa az első célnyomás-mintánál azonnal álljon le; a stabilitási
  idő alatt nem maradhat rajta előkészítési flow. Az előkészítés befejezése
  után a rendszer várjon. A PID- és adatrögzítési ciklus kizárólag a
  külön **Mérés indítása** gomb megnyomásakor kezdődhet; ez a gomb nem
  kérheti be újra az előkészítési adatokat, és nem adhat pumpa-
  `STOP`, `FLOW`, `PRESS` vagy `RUN` parancsot. Kizárólag a szelepvezérlést
  és a mért értékek rögzítését indíthatja el.
  A minimális indítási többlet alapértéke 20 bar, de a Beállításokban
  bármely pozitív, legalább 0,1 baros értékre módosítható. Az aktuális
  értéket az előellenőrzésnek és mindkét BES-indítási kapunak ugyanúgy kell
  használnia.
  A BES konfigurálása előtt és közvetlenül a BES `RUN` előtt ismét ellenőrizni
  kell a friss cache-elt különbséget; visszaesésnél a BES nem indulhat el.
  Az előkészítés explicit állapotgép legyen. Egy felügyeleti ciklus legfeljebb
  egy aszinkron pumpaparancsot helyezhet queue-ba; a ciklus nem várhatja meg a
  soros tranzakciót. Minden `STOP`, konfiguráció vagy `RUN` csak érvényes válasz
  és az előírt STATUS-igazolás után léptetheti tovább az állapotgépet. A DASNET-
  tranzakció saját timeoutja ne számítson vezérlésiciklus-deadline hibának.
  Pumpánként egyetlen worker birtokolja a COM-portot, ütemezi a pollingot és a
  prioritásos parancssort. Emergency/safety STOP prioritása előzze meg a még el
  nem kezdett normál parancsokat és telemetriát; futó keretet bájtszinten nem
  szabad megszakítani.
  Szimulációban ugyanez az állapotgép és sorrend fusson a szimulált
  pumpákkal. READY állapotban az **Előkészítés** és a **Mérés indítása** is legyen
  elérhető. A közvetlen mérésindítás a manuálisan beállított pumpákhoz friss
  biztonsági előellenőrzés után kihagyja a pumpa-előkészítést; pumpaparancsot nem
  ad, csak a szelepvezérlést és az adatrögzítést indítja. Az automatikus
  előkészítés után a **Mérés indítása** WAITING_CONFIRMATION állapotból ugyanazt
  a mérési runtime-ot indítsa.
- A pumpatelemetria minőségét mezőnként kell nyilvántartani. A nyomás
  biztonságkritikus; elavulása reteszelt hibát okozhat. A FLOW vagy VOLA önálló
  elavulása `DEGRADED` kapcsolatot jelezzen és maradjon látható, de önmagában ne
  állítsa le a nyomásszabályozást vagy a teljes mérést. A kapcsolatindításhoz
  nyomás és alapstátusz szükséges; a lassú mezők háttérben töltődhetnek fel.
  A nyomáslekérdezés elsőbbséget élvezzen. A biztonságkritikus `STATUS` külön
  ütemezést kapjon, és normál parancsfolyam se éheztethesse ki. A `FLOW` és
  `VOLA` egyetlen körforgásos lassú sorban fusson. A következő határidő az előző
  tranzakció tényleges befejezése után induljon; vezérlőparancs alatt a normál
  polling szüneteljen. Sorban állás közben kitimeoutolt, még el nem kezdett
  parancsot vissza kell vonni, hogy később ne futhasson le.
- Developer/szerviz módban a közös Beállítások ablak külön
  **Pumpatelemetria / STALE** oldala szerkessze a nyomás- és telemetria-polling
  időközét, a PRESS, FLOW/VOLA és STATUS STALE-határát, valamint a kezdő
  telemetria timeoutját.
  Nyomás-STALE-határ nem lehet rövidebb három pollingperiódusnál, illetve a
  soros timeout/próbálkozási keret plusz két pollingperiódusnál. A felület
  jelezze, hogy a nyomás STALE-határának növelése késlelteti a kapcsolatvesztés
  felismerését; az értékek csak a következő hardveraktiváláskor lépjenek életbe.
  Az alapértelmezett nyomáspolling 0,5 s; a lassú körforgás egymást követő
  elemei között 0,5 s szünet van. A nyomás-STALE-határ 6 s, a STATUS-é 8 s,
  a FLOW/VOLA mezőké 33 s. A PRESS és FLOW/VOLA minimuma vegye figyelembe a
  tényleges soros timeout/retry keretet; a STATUS biztonsági felismerési határa
  az explicit jóváhagyott 8 s. Alacsonyabb elmentett érték ne kerülhessen
  aktívan a workerbe. A kezdő timeout legalább két teljes soros timeout/retry keretet fedjen
  le (`PRESS` + `STATUS`); az alapértelmezett 2 s × 2 próbálkozás mellett ez
  8 s. Előkészítés és mérés ugyanazt az egy
  cache-elt pollingfolyamatot használja. A felület külön jelezze a mentett és
  a jelenlegi workerekben ténylegesen aktív időzítést.
- A köpenynyomás felépülése alatt minden egyéb szenzor-, kapcsolat- és
  nyomáshatár maradjon aktív. Timeout, kezelői megszakítás vagy bármely hiba
  mindkét pumpán STOP-ot és a mérési runtime indításának tiltását váltsa ki.
- A `valve_direction_validated`, `limits_validated`, `profile_validated` és
  `pump_shutdown_validated` üzembe helyezési jelzők hiánya sárga, kezelő által
  elfogadandó preflight-figyelmeztetés, nem mérésindítási tiltás. A tényleges
  kapcsolat-, nyomásjel-, szelep-AO-, aktív vészleállítás-, nyomáshatár- és
  érvénytelen mérési paraméterhibák továbbra is blokkolók.
- Az alkalmazás ugyanabból az onedir kiadásban levő EXE-ből `terminal` argumentummal interaktív,
  állapotot megőrző parancssori vezérlést biztosítson.
- A terminálból elérhető legyen a státusz, csatlakozás, mérésindítás/-leállítás,
  vészleállítás, hibanyugtázás, leválasztás, kézi/automata szabályozás, mérési
  szakasz és adatrögzítési időköz beállítása.
- A terminál mód alapértelmezetten és jelen kiadásban kizárólag szimulációt
  vezérelhet; a szimulált rekordokat külön eredetjelölésű CSV-be menti, de nem
  érhet el fizikai kimenetet. A hardveres terminálvezérlés csak külön, később jóváhagyott,
  a felderítést és explicit kezelői megerősítést megtartó terv alapján engedhető.
- Külön előkészítési nézet a pumpák felügyelt vezérlésére.
- Developer tesztmódban az eszközök külön-külön kapcsolhatók és kérdezhetők le;
  egy még be nem kötött eszköz hibája nem rejtheti el a többi sikeres kapcsolatát,
  és nem tilthatja azok STOP vagy leválasztási műveletét. Normál méréshez továbbra
  is minden, az aktív mérési profilban kötelező eszköz szükséges. A vonali
  nyomásmérő opcionális; hiányában nem olvasható és nem választható PID-forrásnak,
  de a besajtolópumpa nyomásáról szabályozott mérést nem blokkolhatja.
- A vezetett szelepteszt sikeres irányellenőrzését az alkalmazás az aktuális
  NI kimeneti csatornához és a szelep 0/100%-os feszültségleképezéséhez
  kötve mentse. A csatorna vagy a leképezés módosítása után az irányt
  ismét fizikailag ellenőrizni kell.
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
- Automatikus nyomásfelépítéskor a besajtolópumpa indítása előtt a konfigurált
  köpeny–besajtolási nyomáskülönbség stabil meglétének ellenőrzése.
- A BES indulása után a minimum nyomáskülönbség folyamatos felügyelete; elvesztése
  állítsa le a BES-t, helyreállása pedig hiszterézissel engedje az újraindítást.
- A két célnyomás elérésének ne legyen időalapú határideje. A kommunikációs és
  konkrét parancs-timeoutok, a telemetry minőségvédelem és az operátori
  megszakítás változatlanul maradjon aktív.
- A szelep automata és kézi módban működhet.
- Automata módban a szabályozási forrás választható legyen: besajtoló pumpa nyomása vagy vonali nyomásmérő.
- A fizikai szelepskála jelentése `0% = teljesen zárt`, `100% = teljesen nyitott`.
  Mivel a nagyobb szelepnyitás csökkenti a besajtolási nyomást, az automatikus
  nyomásszabályozás alapértelmezett hatásiránya `REVERSE`: a célérték alatti
  nyomás záró, a célérték feletti nyomás nyitó beavatkozást kér.
- A vezérlési mód és a PID paraméterei futó mérés közben is módosíthatók. A mód a
  következő vezérlési ciklus beállításaként, az érvényes PID-paramétercsomag pedig
  a háttérszál következő felügyelt ciklushatárán, versenyhelyzet nélkül lépjen
  életbe. Érvénytelen átmeneti mezőérték nem írhatja felül az utolsó érvényes PID-et.
  A PID-beállítások névvel menthető profilokba rendezhetők.
- Folyamatban lévő mérés alatt csak olyan értékmező maradhat aktív, amelyhez
  tényleges futásidejű alkalmazási út tartozik. Az indítási előkészítés alatt a
  futásidejű mezők is legyenek zárolva; a BES mérési flow szimulációban ne legyen
  szerkeszthető, mert ott nincs hozzá alkalmazható fizikai pumpaművelet.
- A részletes PID-hangolás és a felügyelt manuális hardvervezérlés csak Developer
  módban jelenhet meg; a normál kezelői nézet az üzemi műveletekre korlátozódjon.
- Developer módban a háttér-vezérlési ciklus időköze és watchdog-tűrése külön
  beállítható és tartósan mentett legyen. Módosításuk futó mérés közben tilos;
  a watchdog és a ciklushiba miatti safe-state nem kapcsolható ki.
  Ugyanez a ciklusidő és watchdog-tűrés vezérelje a pumpák `PREPARING`
  állapotának biztonsági ellenőrzéseit is; az előkészítés nem használhat
  kódba égetett saját ciklusidőt.
- Cél a beállított nyomás ±1 bar tartása, ennek igazolási módszerét még rögzíteni kell.

## Adatmentés

- A nyers mérési adatok folyamatos, összeomlástűrő helyi mentése kötelező.
- Az NI nyomásbemeneteknél a ciklusonkénti mintaburst mediánját és az abból képzett
  EMA-szűrt nyomást külön kell megőrizni. A PID és a kijelzés a szűrt értéket, a
  kemény nyomás-interlock a szűretlen értéket használja.
- A szimulációs mérési adatot az éles adattal azonos tartóssági, export- és
  NAS-szinkronfolyamat kezeli, de a fizikai mérés fájljával nem keverheti. A fájlnév
  `_simulation_live_raw.csv`, a hozzá tartozó pillanatképekben pedig kötelező a
  `measurement_kind=simulation` eredetjelölés.
- A **SZIMULÁCIÓ – nincs fizikai kimenet; adatmentés aktív, szimulált eredettel**, illetve a
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
  nem használhat. A mérési perzisztencia, a fáziskezelés, az eseménymentés, az
  export és a NAS-szinkron logikája egyezzen meg a hardvermérésével; kizárólag az
  adatforrás és a kötelező szimulációs eredetjelölés térhet el.
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
- A központi Beállítások ablakban legyen NAS-célválasztás, háttérben futó
  kapcsolat-/írhatósági próba, várólistastátusz és olvasható fájlrendszernézet.
  A Windows-hitelesítést kell használni; jelszó nem kerülhet az alkalmazás
  konfigurációjába.
- A nyers és felhasználói magyar CSV pontosvesszős, tizedesvesszős formátumot használ;
  a felhasználói exportnál más elválasztó és tizedespont is választható.
- Az export célútvonalát natív fájlválasztóval kell megadni, nem szerkeszthető
  szöveges útvonalmezővel.
- Az export nem helyettesíti a belső nyers adatforrást.
- A mérési események egyedi azonosítóval, időbélyeggel, eltelt idővel,
  állapot- és hardverkontextussal append-only `*.events.jsonl` oldalfájlban
  maradnak meg. Ugyanaz az esemény jelenjen meg az aktuális és a teljes
  diagramon; az Excel-export eseménylapot és marker-sorozatot tartalmazzon.
Mérésindítási konfiguráció
---------------------------------

- `pump_startup/injection_startup_flow_ml_per_hour`: BES előkészítési
  térfogatáram.
- `pump_startup/injection_measurement_flow_ml_per_hour`: korábbi
  kompatibilitási kulcs; a mérésindítás nem alkalmazza.
- A korábbi `pump_startup/injection_target_flow_ml_per_hour` kulcs csak
  migrációs fallback az előkészítési értékhez. Ha az új mérési kulcs
  hiányzik, az aktív szakasz mentett cél-flow-ja az egyértelmű alapérték.
