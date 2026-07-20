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
CSV-exportot, az Excel adat- és
diagramlapját, valamint a NAS-kiesés után SQLite-ban megmaradó várólista újbóli
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
A Qt-teszt ellenőrzi a külön kalibrációs ablak két lapját, a 20 baros minimális
köpenytöbbletet, valamint a részletes áttekintő projekt-, fázis-, kalibráció- és
biztonsági kijelzéseit.
A dashboard UI-teszt ellenőrzi az **Élő mérés** és **Teljes mérés** füleket, a
beágyazott teljes mérési nézetet és azt, hogy a korábbi menüművelet a fülre vált.
A témateszt ellenőrzi, hogy a címkék és szerkeszthető szövegmezők háttere világos,
sötét és rendszer-témában is átlátszó, beleértve a mód- és riasztási jelzéseket.
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
`víz → olaj → víz` szegmentálást. Az exporttesztek egyetlen kiválasztott fázis
adatainak CSV- és Excel-kimenetét ellenőrzik; többfázisú export nem megengedett.
A szimulációs mentéstiltási tesztek külön igazolják, hogy sem a szolgáltatási
perzisztálási kérés, sem a writer közvetlen meghívása nem hoz létre rekordot,
könyvtárat, üres CSV-t vagy NAS-feladatot. A UI-teszt a „NINCS ADATMENTÉS” jelölést
és a szimuláció után változatlanul üres éles mérési előzményt is ellenőrzi.
A Developer szimulációs mód tesztje ellenőrzi a hardvermódból visszaépített
szimulációs eszközréteget, a kikapcsolt writert, az üres projektkönyvtárat és a
felület módjelzését.
A dashboard értesítési tesztje igazolja, hogy a korábbi állandó mód- és
riasztásbannerek nem kerülnek a widgetfába, háttérben vagy minimalizálva tálcagomb-
figyelmeztetés történik, azonos eseménykulcs pedig csak egy értesítést válthat ki.

A termináltesztek végigjárják a csatlakozás–indítás–leállítás–leválasztás
állapotgépet, a vészleállítás és nyugtázás útját, a hibás szabályozási értékek
elutasítását, valamint egy stdin/stdout alapon szkriptelt teljes munkamenetet.
Minden terminálteszt kizárólag szimulátorokat és letiltott adatwritert használ.

A hardverkonfigurációs tesztek ellenőrzik az eltérő COM-portokat, DASNET- és
NI-konfigurációk előállítását, a szelep 1–5 V végpontjait, valamint azt, hogy egy
részpróba hibája mellett a többi eszköz eredménye megmarad. A fej nélküli Qt-teszt
igazolja az eszközönkénti státuszkijelzést, és hogy az aktiválógomb csak mind a
négy szükséges kapcsolat sikere után válik elérhetővé. A dashboard tesztje ellenőrzi
a bal oldali állapotpanel reszponzív szélességét, sortörését és automatikus
függőleges görgetési beállítását, valamint a jobb panel reszponzív tördelését és
vízszintes görgetősávjának tiltását is.

A diagnosztikai tesztek lefedik a kikapcsolt napló fájlmentességét, a
kategóriaszűrést, az append-only HTML-fájlírást és HTML-escape-elést, az
inkrementális memóriaolvasást, DASNET
TX/RX eseményeket és az NI funkció szerinti kategorizálást. A Qt-teszt egyetlen
pumpakategóriát engedélyez, majd ellenőrzi, hogy a Developer táblában az NI esemény
nem, a pumpaesemény viszont megjelenik. A felderítési összegzés láthatóságát külön
teszt ellenőrzi normál és Developer módban.

A pumpavezérlési tesztek ellenőrzik a REMOTE–konfigurálás–RUN–STOP–LOCAL sorrendet,
a pontos RUN-megerősítést, a konfigurálatlan indítás tiltását, a 20 baros interlock
határ alatti és pontos határértékű esetét, valamint a globális safe STOP
állapotszinkronját. Külön teszt igazolja a B csatorna parancsutótagjait.

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
