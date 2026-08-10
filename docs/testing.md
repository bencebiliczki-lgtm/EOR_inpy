# Tesztstratégia

## Automatikus tesztek

- kalibrációs képletek és tartományhiba;
- mérési modellek és állapotátmenetek;
- DASNET keretképzés és válaszfeldolgozás rögzített mintákból;
- pumpa- és NI-szimulátorok;
- összes biztonsági interlock;
- PID korlátozás, anti-windup, kézi/automata váltás és a fizikai szelephez
  tartozó fordított hatásirány (alacsony nyomásnál zárás, magas nyomásnál nyitás);
- CSV séma, időbélyeg és tizedesformátum;
- NAS-kiesés és helyi várólista;
- konfiguráció migrációja.

A szimulátoros automatikus tesztek jelenleg ellenőrzik a kalibrált mintavételt, a
besajtolt térfogat követését, az 1 másodperc–1 óra intervallumkorlátot, az interlock
és kapcsolatvesztés utáni biztonságos állapotot, valamint a nyers CSV fejlécét és
append működését. Az eszközszimulációs regressziók virtuális idővel ellenőrzik a
pumpa állapotgépét, nyomásrámpáját, térfogyását és saját túlnyomásvédelmét, a
mezőszintű nyomás-STALE hibát, a determinisztikus válaszkésést, az NI
freeze/spike/disconnect hibáit és a szelep véges sebességét, illetve beragadását.
A Qt-teszt a Developer beállítási oldal modellfrissítését, hibainjektálását és
hibatörlését is lefedi.

Az analóg jelfeldolgozás tesztjei lefedik a 20 mintás burstöt, az izolált tüske
mediános elutasítását, az EMA átmenetét és a három burst után elfogadott tartós
lépcsőt. Külön biztonsági teszt igazolja, hogy a nyers nyomás már akkor leállítást
vált ki, amikor a szűrt PID-/kijelzési érték még a határ alatt van. A CSV-tesztek a
nyers és szűrt oszlopokat, valamint a V1/V2 migrációt is ellenőrzik.

A vonali nyomás egyben a belépő nyomás; a regressziós tesztek biztosítják, hogy ne
jöjjön létre hozzá duplikált NI-csatorna, kalibráció vagy CSV-mező. A korábbi,
`inlet_pressure_bar` oszlopot tartalmazó mérési fájlok továbbra is megnyithatók.
A projektadatbázis teszteli a szakaszok metaadatait, mozgatását és törlés utáni
újraszámozását. Az adatkezelési teszt a
magyar nyers CSV mellett a régi vesszős fájlok visszafelé kompatibilis megnyitását
és az év/projekt JSON-pillanatképeket is ellenőrzi.

Az adatkezelési tesztek ellenőrzik a projektenként és fázisonként eltérő,
Windows-biztos fájlútvonalat, a fázisváltáskor létrejövő külön nyers CSV-ket, a
csak olvasáskor történő időrendi összefűzést, a tizedesvesszős/pontosvesszős
CSV-exportot, az egyetlen projekt-munkafüzet külön fázislapjait és azok beágyazott
diagramját, egy korábbi fázislap adatvesztés nélküli frissítését, a rekordot
tartalmazó fázis egyszeri lezárási eseményét, valamint
a NAS-kiesés után SQLite-ban megmaradó várólista újbóli
megnyitását és sikeres szinkronját. A Qt-teszt a korábbi projektfájl visszatöltését,
az indítási projektválasztó szükségességét, a projektenként mentett utolsó mérési
fázis megjelenítését, a választóból történő projektlétrehozást, az
adatsorkapcsolókat, az egyéni időtartományt és a kézi Y-tengelyt is ellenőrzi.

Az SQLite-tesztek ideiglenes, valódi adatbázisfájlokon ellenőrzik a projekt
visszanyitását, a konfigurációs és kalibrációs pillanatképek változatlan
megőrzését, a szakaszok sorrendjét és átnevezését, az UTC-normalizálást, valamint
az ismeretlenül újabb sémaverzió elutasítását. A projekttörlési teszt ellenőrzi a
fázis-metaadatok kaszkádos törlését; a UI-teszt az INI-hivatkozások tisztítását és a
nyers CSV-k változatlan megőrzését is igazolja.

A NAS-kapcsolati teszt ideiglenes mappában ellenőrzi az írás–visszaolvasás–törlés
kört és azt, hogy nem marad próbfájl. A UI-regresszió ellenőrzi a központi NAS
beállítási oldalt, a csak olvasható útvonalkijelzést, a natív mappaválasztót, a
tartós `nas/enabled` és `nas/target_path` kulcsokat, valamint hogy a CSV-export
natív fájlmentő ablakot használ és megjegyzi az utolsó célmappát.

Az időzónatesztek külön téli és nyári UTC-időponttal ellenőrzik az
`Europe/Budapest` átváltást, valamint az UTC szerint előző napra eső, de magyar
helyi idő szerint már következő napi projektmappa elnevezését.

A szabályozási tesztek lefedik a kézi kimenet korlátozását, mindkét választható
nyomásforrást, a közvetlen és fordított hatásirányt, a kimeneti telítést,
anti-windupot, a derivált setpoint-kick elkerülését és a biztonsági felülbírálást.

A DASNET-tesztek a gyártói kézikönyv `REMOTE`, `CONST FLOW`, `FLOW=1.00`, `RUN`
és üres poll mintakereteit bájtpontosan ellenőrzik. Lefedik a checksum- és
hosszhubát, timeoutot, háromszori újrapróbálkozást és pumpa `PROBLEM=` válaszát.
Az ISCO adapter tesztjei az azonosítást, egységellenőrzést, státuszlekérdezést és a
dokumentált vezérlési sorrendet szimulálják. Az NI-tesztek fake backenddel igazolják,
hogy explicit engedély nélkül nincs fizikai kimenet, a safe-state visszavonja az
engedélyt, és a százalék–feszültség kalibráció mindkét irányban működik.

A runtime-tesztek igazolják, hogy a vezérlési ciklus az adatrögzítésnél gyorsabban
fut, csak az esedékes ciklus ír tartós rekordot, valamint a lassú ciklus watchdogot
és safe-state-et vált ki. A szünetteszt igazolja, hogy szünetben sem PID-ciklus,
sem perzisztálás nem fut, miközben a biztonsági hold-felügyelet aktív marad, majd
a folytatás ugyanabban a runtime-ban helyreáll. A biztonsági tesztek a céltúllövési határ alatti és
pontosan határértékű esetet is lefedik. A Qt-integrációs teszt valódi háttérszálról
fogad cikluseredményt és ellenőrzi az eszközkapcsolati jelzőket.
A Qt-teszt ellenőrzi a külön kalibrációs ablak két lapját, a 20 bar alá is
konfigurálható minimális köpenytöbbletet, valamint a részletes áttekintő projekt-, fázis-, kalibráció- és
biztonsági kijelzéseit.
A dashboard UI-teszt ellenőrzi az **Élő mérés** és **Teljes mérés** füleket, a
beágyazott teljes mérési nézetet és azt, hogy a korábbi menüművelet a fülre vált.
A mérési táblázat tesztje ellenőrzi az Excel-fejléccel azonos oszlopokat, a magyar
helyi időt, az 1000 soros lapozást, valamint a grafikonnal közös fázis- és
időtartomány-szűrést.
A témateszt ellenőrzi, hogy a címkék és szerkeszthető szövegmezők háttere világos,
sötét és rendszer-témában is megfelelő; a mód- és riasztássáv szándékosan kiemelt.
A reszponzív dashboard-teszt kis ablakmagasságnál láthatóvá teszi mindkét oldalsáv
scrollbarját, majd külön ellenőrzi a bal és jobb sáv splitterrel történő
átméretezhetőségét.
Az elrendezésszerkesztő regressziója ellenőrzi a Megjelenés oldal két
oldalsáv-kapcsolóját, a kártyák × gombját, az alsó vízszintes visszaállító sávot és
a láthatóság QSettings-alapú újraindítás utáni megőrzését. Külön biztonsági teszt
tiltja a jobb oldali mérésvezérlés elrejtését futó mérés alatt.
Ugyanez a UI-teszt ellenőrzi, hogy az **Aktív projekt** kártya szakasz-dropdownja
projektváltáskor feltöltődik, módosítása frissíti a runtime aktív szakaszát, projekt
hiányában pedig letiltott és egyértelmű üresállapotot mutat.
A szakaszlétrehozási UI-teszt ellenőrzi, hogy a dropdown utolsó eleme mindig a
létrehozási művelet, az elfogadott ablak megjegyzése SQLite-ba kerül, az új szakasz
aktív lesz, megszakításkor pedig megmarad a korábbi választás.
A PID-profil tesztek lefedik a 4-es SQLite-sémára migrálást, a validációt, a
kis-/nagybetűtől független név szerinti felülírást, a betöltést és törlést. A
UI-teszt ellenőrzi a személyre szabott mezők mentését, a kézi módosításkor történő
„Egyéni beállítások” váltást és az alkalmazás újraindítása utáni visszatöltést.
A runtime-regresszió ellenőrzi, hogy a sorba állított PID-csomagot pontosan a
következő háttér-vezérlési ciklus alkalmazza. A UI-regresszió futó mérésnél
ellenőrzi a kézi/automata mód azonnali runtime-frissítését, valamint a több
mezőváltozást összevonó, valós idejű PID-frissítést. Külön mezőzárolási teszt
igazolja, hogy az indítási előkészítés alatt nincs szerkeszthető folyamatérték,
majd csak a valóban alkalmazható runtime-mezők aktiválódnak; a hardveres BES-flow
szimulációs mérés közben inaktív marad.
A mérési és adattárolási tesztek lefedik mindkét pumpa pozitív és negatív nettó
térfogatváltozását, a számlálók újraindítását, a V1→V2 biztonsági mentéses
migrációt, a fázisok első előfordulási sorrendjét, a fázisszűrést és a
`víz → olaj → víz` szegmentálást. Az exporttesztek ellenőrzik az egyfázisú
CSV-kimenetet, valamint azt, hogy a projekt Excel-fájljában minden lezárt fázis
saját munkalapot kap és egy ismételt fázisfrissítés nem törli a többi lapot.
A szimulációs perzisztenciatesztek igazolják az eredetjelölt CSV és pillanatképek
létrejöttét, valamint azt, hogy szimulált rekord nem kerül a fizikai mérés
fájljába. A UI-teszt ellenőrzi az aktív szimulációs adatmentés jelölését és a
mentett rekord megjelenését az előzménynézetben. A fázislezárási regresszió ugyanazt
a writer-életciklust futtatja `live` és `simulation` eredettel, és mindkettőnél
ellenőrzi az egyszeri lezárási eseményt és a nyers rekordok megmaradását.
A Developer szimulációs mód tesztje ellenőrzi a hardvermódból visszaépített
szimulációs eszközréteget, az engedélyezett writert és a felület módjelzését.
A dashboard értesítési tesztje igazolja az állandó mód- és riasztássáv jelenlétét,
a háttérben vagy minimalizálva történő tálcagomb-figyelmeztetést, valamint hogy
azonos eseménykulcs csak egy értesítést válthat ki. A riasztásbezárási tesztek
ellenőrzik a friss biztonsági mérést, a veszélyes állapotban megmaradó reteszt,
a szimulációs `READY` visszaállítást, valamint azt, hogy hardveres
vészleállítás után az állapot kezelői nyugtázásig `FAULT` marad, és nem indul
automatikus hardverállapot-frissítési hurok. Az előellenőrzési tesztek
lefedik a figyelmeztetések külön jóváhagyását és bármely hibás tétel indítástiltását.
A tálcamenü tesztje ellenőrzi az ablak-visszaállítási és programbezárási műveletet,
valamint hogy a kilépési kérés a főablak biztonságos bezárási útvonalát hívja.

A termináltesztek végigjárják a csatlakozás–indítás–leállítás–leválasztás
állapotgépet, a vészleállítás és nyugtázás útját, a hibás szabályozási értékek
elutasítását, valamint egy stdin/stdout alapon szkriptelt teljes munkamenetet.
Minden terminálteszt kizárólag szimulátorokat és letiltott adatwritert használ.

A manuális fizikai pumpa-RUN, szelepírás, vezetett AO-próba és
hardver-újracsatlakozás UI-tesztjei ellenőrzik, hogy az Igen/Nem message box
`Yes` eredménye érték szerinti összehasonlítással valóban továbbítja a
parancsot; a teszt nem támaszkodik a PySide enum Python-objektumazonosságára.

A hardverkonfigurációs tesztek ellenőrzik az eltérő COM-portokat, DASNET- és
NI-konfigurációk előállítását, a szelep 1–5 V végpontjait, valamint azt, hogy egy
részpróba hibája mellett a többi eszköz eredménye megmarad. A fej nélküli Qt-teszt
igazolja az eszközönkénti státuszkijelzést, és hogy az aktiválógomb csak mind a
négy szükséges, kalibrációs tartományon belüli kapcsolat sikere után válik
elérhetővé. Külön teszt igazolja, hogy a hardver által sikeresen kiolvasott, de az
1–5 V kalibrációból kieső negatív feszültség blokkolja az aktiválást, valamint hogy
az indítás előtti próba nem engedi futó állapotba a rendszert és biztonságos állapotot
kér. A dashboard tesztje ellenőrzi
a bal oldali állapotpanel reszponzív szélességét, sortörését és automatikus
függőleges görgetési beállítását, valamint a jobb panel reszponzív tördelését és
vízszintes görgetősávjának tiltását is.

A Qt inputmező-audit a főablak, a projekt-, szakasz-, eszköz-, naplózási és
Developer nézet minden szövegmezőjén, legördülőjén és számmezőjén ellenőrzi, hogy
van látható, programozottan társított címke vagy akadálymentes név. A saját
feliratú jelölőnégyzetek címkézett mezőnek számítanak.

A diagnosztikai tesztek lefedik a kikapcsolt napló fájlmentességét, a
kategóriaszűrést, az append-only HTML-fájlírást és HTML-escape-elést, az
inkrementális memóriaolvasást, DASNET
TX/RX eseményeket és az NI funkció szerinti kategorizálást. A Qt-teszt egyetlen
pumpakategóriát engedélyez, majd ellenőrzi, hogy a Developer táblában az NI esemény
nem, a pumpaesemény viszont megjelenik. A felderítési összegzés láthatóságát külön
teszt ellenőrzi normál és Developer módban.

A pumpavezérlési tesztek ellenőrzik a REMOTE–konfigurálás–RUN–STOP–LOCAL sorrendet,
a belső RUN-engedélytoken ellenőrzését, a konfigurálatlan indítás tiltását, a
konfigurált interlock
határ alatti és pontos határértékű esetét, valamint a globális safe STOP
állapotszinkronját. Külön teszt igazolja a B csatorna parancsutótagjait.
Az NI-engedélyezési regresszió ellenőrzi a kezelői hardverengedély → NI fizikai
kimenet engedély → eszközkapcsolódás sorrendet, valamint azt, hogy safe-state és
normál hardveres leállítás után egyik engedély sem marad érvényes.

A telemetria-minőség elfogadási tesztjei külön-külön fagyasztják a pressure,
flow és volume mezőt, ellenőrzik több mező egymást követő STALE átmenetét,
a parse-hibából származó INVALID állapotot, a GOOD helyreállást, valamint a
kapcsolatvesztés és újracsatlakozás eseményeit. A safe-state teszt minden
pumpa- és szelepművelet naplózott eredményét ellenőrzi.

A naplómegőrzési tesztek igazolják a lejárt ismert naplók törlését, a lezárt
naplók tömörítését, az aktív és zárolt fájlok védelmét, valamint azt, hogy a
nyers CSV nem kerül az automatikus tisztítás hatókörébe.

A mérési pumpaindítás tesztje ellenőrzi a köpenypumpa `CONST FLOW → RUN`, a
köpenycél és a konfigurált margin kivárását, majd a külön ciklusokra bontott
`STOP → CONST PRESS → RUN` nyomástartási sorrendet. A BES csak ennek stabilitása
után indulhat. A teszt ellenőrzi továbbá mindkét dokumentált
`MAXPRESS` hardverhatár-parancsot és az `ML/HR` pumpaegység explicit beállítását,
valamint hogy az `1000 ml/h` kezelői célérték `FLOW=1000` parancsként jut el a
pumpához. Ellenőrzi továbbá a pontos indítási
megerősítést, a két kezdőnyomás és a tervezett nyomástöbblet kötelező bevitelét,
a besajtoló kezdőnyomásának kivárását, a cél első elérésekor kiadott azonnali
STOP-ot, valamint azt, hogy timeout vagy indulási
biztonsági hiba esetén egyik pumpa sem marad RUN állapotban.
Külön regresszió igazolja, hogy a besajtoló sikeres `RUN` parancsa után a
nyomáskülönbség 20 bar alá esése nem állítja le a pumpákat: a köpeny a megadott
fix `CONST PRESS` célon marad, a normál és szüneteltetett mérési safety pedig nem
alkalmazza újra az indítási marginfeltételt.
Dinamikus nyomásfelfutási regresszió ellenőrzi, hogy 20 bar alatti KÖP–BES
különbségnél a BES még `REMOTE` vagy `FLOW` parancsot sem kap, valamint hogy
a konfigurálás közben visszaeső margin a BES `RUN` előtt leállítja az indítást.
Külön deadline-regresszió lassú `STOP`, `PRESS` és `RUN` tesztdublákkal igazolja,
az aszinkron worker alatt tovább futó safety ciklust és azt, hogy a lassú
parancs nem okoz control-cycle deadline hibát, miközben a lassú biztonsági
kiértékelés továbbra is watchdoghibát okoz. Parancssorrend-teszt
igazolja a KÖP átállítási lépései közötti friss safety ciklust. A rollback-teszt
`PROBLEM=LOCAL MODE` válasznál ellenőrzi az egyszeri `REMOTE → STOP` helyreállítást,
ha az álló Local állapot nem igazolható cache-ből. Igazolt `STOP LOCAL` esetén
külön regresszió ellenőrzi a felesleges STOP kihagyását és azt, hogy a másik pumpa
STOP-ja ettől függetlenül lefut.
Worker-regresszió ellenőrzi, hogy a már sorban álló STOP megelőzi a következő
telemetriát, az egyik pumpa blokkolt tranzakciója nem állítja meg a másik workerét,
a STATUS igazolja a STOP/RUN eredményt, és a parancstimeout nem control-cycle
deadline néven jelenik meg. Külön teszt igazolja, hogy a sorban állás közben
kitimeoutolt parancs törlődik és a blokkoló tranzakció után sem fut le.
Külön ütemezési regresszió igazolja a biztonságkritikus `PRESS`/`STATUS`
prioritást, a `FLOW → VOLA` körforgást, a tényleges tranzakcióidő utáni
újraütemezést és a felzárkózó burst hiányát. A UI-teszt különböző
értékekkel ellenőrzi a mentett és az aktív pollingbeállítás megjelenítését.
Prioritási regresszió blokkolt telemetriakeret után igazolja a
`STOP → CONFIG → PRESS` sorrendet, továbbá azt, hogy a sorban álló normál
vezérlőparancsok is megelőzik a következő nyomásfrissítést. Külön teszt ellenőrzi,
hogy előkészítés alatt csak `PRESS/STATUS` fut, majd a `FLOW/VOLA` polling az
előkészítés lezárása után helyreáll. A queue-, execution- és verification-timeout
külön regressziót kap. LOCAL módban maradó pumpa esetén a REMOTE-visszaigazolás
hibás, ezért konfiguráció vagy RUN parancs nem követheti. A puszta
`STATUS=STOP/RUN` sem elegendő: az előkészítés explicit Remote állapotot követel.
Más tesztek igazolják, hogy a `STOP LOCAL` és `RUN LOCAL` ettől még `GOOD`
adatminőségű, valamint a REMOTE-hiba pumpaszerepet megnevező üzenetet ad.
A startup-budget teszt két teljes PRESS/STATUS retry-keretet követel meg. A
blokkolt worker leválasztási regressziója igazolja, hogy timeoutnál a port nem
záródik be, reconnect nem indul, és a cleanup csak a régi worker befejezése
után hajtható végre.
A Remote-felügyeleti regresszió igazolja, hogy Local státusz dashboard/üresjárati
polling alatt nem küld parancsot, aktív vezérlési felügyelet mellett viszont a
következő periodikus STATUS után visszaellenőrzött `REMOTE` helyreállítás indul.
A részleges kapcsolati tesztek igazolják, hogy az egyik pumpa vagy NI-bemenet hibája
mellett a többi eszköz sikeres státusza megmarad. A közvetlen pumpakapcsolódás
nem vált módot; az első vezérlőművelet ellenőrzi és szükség esetén helyreállítja
a Remote módot. Külön regresszió fedi a futás közbeni Remote-vesztést és az
ellenőrzés utáni `LOCAL MODE` válasz egyszeri helyreállítását. Bezáráskor minden
pumpán külön STOP és portlezárás történik akkor is, ha az eszköz nem jutott el az
azonosított állapotig. A Qt-teszt emellett igazolja, hogy a normál kezelői
Csatlakozás/Leválasztás gombok rejtettek, a mérés szüneteltethető és folytatható,
a Leállítás pedig `READY` kapcsolat mellett alaphelyzetbe állítja az élő grafikont
és táblázatot. Külön regresszió ellenőrzi, hogy kritikus hardverhiba felszabadítja
mindkét pumpaportot, megtartja a HARDVER indítási preferenciát és megnyitja az
Eszközbeállításokat. A Developer manuális ablak élő hardvermódból megnyitható,
a telemetria közben kiadott több parancs veszteség nélkül, sorrendben lefut, majd
végrehajtás után látható sikerállapotot kap. A DASNET-tesztek külön ellenőrzik,
hogy a soros timeouttal darabolt válasz a következő olvasási ablakból kiegészül.
Külön regresszió rögzíti, hogy a nyomás `STALE` határa legalább három
pollingperiódust és a soros timeout/próbálkozási keretet lefedi. A DASNET-teszt
ellenőrzi, hogy a töredékes válaszolvasás egy próbálkozáson belül nem nyit több
teljes timeoutablakot, a pumpatelemetria-regresszió pedig valós blokkolási idővel
igazolja, hogy a tranzakció befejezésétől számított ütemezés nem hoz létre
felzárkózó pollingburstöt. Külön teszt ellenőrzi a vezérlőparancsok közötti
kötelező biztonsági PRESS/STATUS lehetőséget és a normál parancsok kifutását.
Külön telemetriateszt igazolja, hogy FLOW/VOLA timeout mellett a nyomáspolling
tovább fut, a nyomás minősége `GOOD` marad, míg a kapcsolat `DEGRADED` állapotot
és mezőszintű hibát ad.
A STALE szervizoldal UI-tesztje ellenőrzi a polling-, külön PRESS/FLOW-VOLA/STATUS
STALE- és startup timeoutértékek tartós mentését, visszatöltését, valamint a
soros retry-keretnél rövidebb STALE-határ mentésének tiltását.
Hiányos telemetria mellett a működő szenzor értéke látható,
miközben a kapcsolatfrissítés nem indít közös biztonsági mérési ciklust, a
biztonságkritikus RUN külön ellenőrzése pedig változatlan marad.
A moduláris profil tesztje kikapcsolt vonali nyomásmérővel ellenőrzi, hogy a
csatorna nem kötelező, nem kerül a kapcsolattesztbe, a mérési rekordban hiányzó
érték marad, és nem keletkezik biztonsági hiba. Külön regresszió igazolja,
hogy a manuális szelepírás nem indít teljes mérési mintavételt, valamint a
manuális pumpabiztonság nem kér nem kapcsolódó eszközadatot.
Külön projektprofil-regresszió ellenőrzi az eszközök projektenkénti hozzáadását
és eltávolítását, a projektprofil Eszközbeállításokba töltését, valamint azt, hogy
ehhez nem jelenik meg helyszíni validációs adatblokk.
A csak szelepet tartalmazó projektprofil UI-tesztje igazolja, hogy sikeres
olvasási kapcsolatpróba és globális hardvermód nélkül is megnyithatja a
közvetlen eszközkezelést, miközben a többi eszköz nincs hozzáadva.

A beállítási központ tesztje ellenőrzi az átméretezhetőséget, a bal oldali
kategórianavigációt, a megadott kezdőoldalt, valamint azt, hogy az Eszközök,
Naplózás, Kalibráció, Megjelenés és Vezérlési ciklus tényleges szerkesztői a
jobb oldali oldalterületbe ágyazódnak, és nem nyitógombos második dialógusok.
A grafikon regressziója
ellenőrzi a sárga figyelmeztetési és piros kritikus pontok részletes hover
adatait, valamint a pontok törlését a dashboard alaphelyzetbe állításakor.
Az aktív hardver-dashboard regressziója `READY` állapotban, futó mérés nélkül
ellenőrzi az élő pumpa- és NI-értékeket, a SAFE szelepállapotot, továbbá azt,
hogy a szolgáltatási frissítés nem hoz létre grafikonpontot.
Az előkészítési dashboard regressziója `PREPARING` állapotban ellenőrzi a két
pumpanyomás, a vonali és differenciálnyomás, valamint a nyomáskülönbség élő
frissítését és magyar, legfeljebb három tizedesjegyes megjelenítését,
miközben a mérési grafikon pufferében még nem keletkezik pont.
Külön UI-regresszió rögzíti, hogy READY állapotban az **Előkészítés** és a
**Mérés indítása** egyszerre elérhető. A közvetlen mérésindítás friss
előellenőrzést futtat, de nem nyit előkészítési adatablakot; az előkészített út
WAITING_CONFIRMATION állapotból közvetlenül ugyanazt a runtime-indítást hívja.
Külön regresszió tiltja, hogy a közvetlen út pumpa-előkészítést vagy pumpa-flow
parancsot adjon ki: a mérésindítás csak a szelepvezérlést és az adatrögzítést
aktiválja.

Az ISCO regressziós teszt ellenőrzi, hogy az `ML/HR` beállítás után az egység
nélküli `FLOW`/`SETFLOW` visszaolvasás nem kap hibás hatvanszoros szorzót.

A Developer vezérlésiciklus-beállítás tesztje ellenőrzi a ciklusidő és a
watchdog-tűrés tartós mentését, valamint a számított végrehajtási határidőt.
Az előkészítési regresszió igazolja, hogy ugyanezek az értékek jutnak a
pumpafelügyeleti ciklusba, az ütemezés nem halmoz driftet, a watchdog-túllépés
pedig mindkét pumpát leállítja.

## Hardveres smoke test

Felügyelt környezetben, nyomásfelépítés nélkül vagy meghatározott biztonságos tesztállapotban:

1. COM-portok és pumpaazonosítók felismerése.
2. Mindkét pumpa legalább 60 perces stabil státuszlekérése.
3. NI nyers feszültségek és kalibrált értékek összevetése referenciajellel.
4. Szelep kézi jelének ellenőrzése engedélyezett tartományban.
5. Kábelkihúzás és timeout biztonságos kezelése.
6. Helyi mentés és NAS-kiesés tesztje.

## Kiadási kapu

- Ruff, mypy és pytest sikeres.
- Biztonsági tesztek sikeresek.
- Windows build elkészül.
- Felügyelt hardveres teszt jegyzőkönyve elfogadott.
- Telepítés manuálisan jóváhagyott; automatikus üzemi telepítés nincs.

## Vezetett eszközteszt és PID-védelem

Az automatikus tesztek lefedik a részleges kapcsolatpróba-összesítést és célzott
érvénytelenítést, a szimulációs/futó-runtime tiltást, a többmintás statisztikát, a
nem véges jelet, AO- és szelephibánál a központi STOP/SAFE útvonalat, a kötelező
kihagyási indokot és a JSON round-tripet. A PID-tesztek ellenőrzik a holtsávban
befagyó integrátort, az időalapú slew rate-et, szűrést, irányváltásszámlálást,
oszcillációs hibát és az ugrásmentes kézi–automata átmenetet.
Mérésindítási regressziók
-------------------------------

A fake pumpás tesztek a parancssorrendet is ellenőrzik: az első írás előtt
mindkét pumpán sikeres olvasásnak kell történnie, olvasási hiba után pedig
nem jelenhet meg `REMOTE`, `FLOW` vagy `RUN`. Külön teszt fedi a
`STOP → FLOW → visszaolvasás → RUN` mérési flow-váltást, a 0,9 V-os
véges jel elfogadását és a nem véges jel elutasítását. A kezdő BES- és
KÖP-nyomásnak a teljes stabilitási időn át fenn kell maradnia, majd a BES
pumpának az operátori döntésig STOP állapotban kell várnia. Az exportteszt
ellenőrzi az eseményazonosító deduplikálását, az eseménylapot és a diagram
marker-sorozatát.
Az egységes vezérlési regresszió a `PumpStartupPlan` alapú belépési pontot,
a közös STOP/FLOW/RUN állapotútvonalat és a flow-váltás RUN előtti
biztonsági kapuját is ellenőrzi.
Az UI-komponens refaktor után a dashboard-regresszió változatlan object name-ek,
splitter-sorrend, widgetméretek, signalok, projektválasztás és mérési
állapotátmenetek mellett ellenőrzi a felépítést.
A SETFLOW firmware-kompatibilitási teszt elfogadja az azonos csatornájú
`FLOWx=érték` választ, de elutasítja a másik csatorna kulcsát, a hibás
mértékegységet, a nem véges értéket és a tolerancián kívüli célértéket.

A `test_stable_profile.py` ellenőrzi a stabil profil sémáját, a hiányzó
fizikai paraméterek mérésblokkoló hatását, a három pollingperiódusos
STALE-ablakot, a csak hiányzó INI-kulcsokra alkalmazott migrációt és a COM3
automatikus jelöltlistából való kizárását. A COM3 manuális szervizmódú
felülbírálása külön teszteset.
