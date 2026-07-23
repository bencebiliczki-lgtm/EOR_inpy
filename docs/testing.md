# Tesztstratégia

## Automatikus tesztek

- kalibrációs képletek és tartományhiba;
- mérési modellek és állapotátmenetek;
- DASNET keretképzés és válaszfeldolgozás rögzített mintákból;
- pumpa- és NI-szimulátorok;
- összes biztonsági interlock;
- PID korlátozás, anti-windup és kézi/automata váltás;
- CSV séma, időbélyeg és tizedesformátum;
- NAS-kiesés és helyi várólista;
- konfiguráció migrációja.

A szimulátoros automatikus tesztek jelenleg ellenőrzik a kalibrált mintavételt, a
besajtolt térfogat követését, az 1 másodperc–1 óra intervallumkorlátot, az interlock
és kapcsolatvesztés utáni biztonságos állapotot, valamint a nyers CSV fejlécét és
append működését.

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
és safe-state-et vált ki. A biztonsági tesztek a céltúllövési határ alatti és
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
A mérési és adattárolási tesztek lefedik mindkét pumpa pozitív és negatív nettó
térfogatváltozását, a számlálók újraindítását, a V1→V2 biztonsági mentéses
migrációt, a fázisok első előfordulási sorrendjét, a fázisszűrést és a
`víz → olaj → víz` szegmentálást. Az exporttesztek ellenőrzik az egyfázisú
CSV-kimenetet, valamint azt, hogy a projekt Excel-fájljában minden lezárt fázis
saját munkalapot kap és egy ismételt fázisfrissítés nem törli a többi lapot.
A szimulációs mentéstiltási tesztek külön igazolják, hogy sem a szolgáltatási
perzisztálási kérés, sem a writer közvetlen meghívása nem hoz létre rekordot,
könyvtárat, üres CSV-t vagy NAS-feladatot. A UI-teszt a „NINCS ADATMENTÉS” jelölést
és a szimuláció után változatlanul üres éles mérési előzményt is ellenőrzi.
A Developer szimulációs mód tesztje ellenőrzi a hardvermódból visszaépített
szimulációs eszközréteget, a kikapcsolt writert, az üres projektkönyvtárat és a
felület módjelzését.
A dashboard értesítési tesztje igazolja az állandó mód- és riasztássáv jelenlétét,
a háttérben vagy minimalizálva történő tálcagomb-figyelmeztetést, valamint hogy
azonos eseménykulcs csak egy értesítést válthat ki. Az előellenőrzési tesztek
lefedik a figyelmeztetések külön jóváhagyását és bármely hibás tétel indítástiltását.
A tálcamenü tesztje ellenőrzi az ablak-visszaállítási és programbezárási műveletet,
valamint hogy a kilépési kérés a főablak biztonságos bezárási útvonalát hívja.

A termináltesztek végigjárják a csatlakozás–indítás–leállítás–leválasztás
állapotgépet, a vészleállítás és nyugtázás útját, a hibás szabályozási értékek
elutasítását, valamint egy stdin/stdout alapon szkriptelt teljes munkamenetet.
Minden terminálteszt kizárólag szimulátorokat és letiltott adatwritert használ.

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
a pontos RUN-megerősítést, a konfigurálatlan indítás tiltását, a konfigurált interlock
határ alatti és pontos határértékű esetét, valamint a globális safe STOP
állapotszinkronját. Külön teszt igazolja a B csatorna parancsutótagjait.

A mérési pumpaindítás tesztje ellenőrzi a köpenypumpa `CONST FLOW → RUN`, majd
`STOP → CONST PRESS → RUN` sorrendjét, a `ml/h`–`ml/min` átváltást, a pontos indítási
megerősítést, a két kezdőnyomás és a tervezett nyomástöbblet kötelező bevitelét,
a besajtoló kezdőnyomásának kivárását, valamint azt, hogy timeout vagy indulási
biztonsági hiba esetén egyik pumpa sem marad RUN állapotban.
A részleges kapcsolati tesztek igazolják, hogy az egyik pumpa vagy NI-bemenet hibája
mellett a többi eszköz sikeres státusza megmarad, a kapcsolódás és REMOTE módba
lépés egy műveletként fut, REMOTE-hibánál pedig a port bezáródik. Bezáráskor minden
pumpán külön STOP és portlezárás történik akkor is, ha az eszköz nem jutott el az
azonosított állapotig. A Qt-teszt emellett igazolja,
hogy a Developer manuális ablak részleges hardveres `IDLE` állapotból megnyitható,
a telemetria közben kiadott több parancs veszteség nélkül, sorrendben lefut, majd
végrehajtás után látható sikerállapotot kap. A DASNET-tesztek külön ellenőrzik,
hogy a soros timeouttal darabolt válasz a következő olvasási ablakból kiegészül.
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
olvasási kapcsolatpróba nélkül is beléphet részleges manuális tesztmódba, miközben
a többi eszköz nincs hozzáadva.

A Developer vezérlésiciklus-beállítás tesztje ellenőrzi a ciklusidő és a
watchdog-tűrés tartós mentését, valamint a számított végrehajtási határidőt.

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
