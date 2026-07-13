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

A belépő nyomás tesztjei lefedik a harmadik NI-csatorna leképezését, kalibrálását,
CSV-mezőjét és önálló biztonsági maximumát. A projektadatbázis teszteli a szakaszok
metaadatait, mozgatását és törlés utáni újraszámozását. Az adatkezelési teszt a
magyar nyers CSV mellett a régi vesszős fájlok visszafelé kompatibilis megnyitását
és az év/projekt JSON-pillanatképeket is ellenőrzi.

Az adatkezelési tesztek ellenőrzik a projektenként eltérő, Windows-biztos
fájlútvonalat, a tizedesvesszős/pontosvesszős CSV-exportot, az Excel adat- és
diagramlapját, valamint a NAS-kiesés után SQLite-ban megmaradó várólista újbóli
megnyitását és sikeres szinkronját. A Qt-teszt a korábbi projektfájl visszatöltését,
az adatsorkapcsolókat, az egyéni időtartományt és a kézi Y-tengelyt is ellenőrzi.

Az SQLite-tesztek ideiglenes, valódi adatbázisfájlokon ellenőrzik a projekt
visszanyitását, a konfigurációs és kalibrációs pillanatképek változatlan
megőrzését, a szakaszok sorrendjét és átnevezését, az UTC-normalizálást, valamint
az ismeretlenül újabb sémaverzió elutasítását.

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

A hardverkonfigurációs tesztek ellenőrzik az eltérő COM-portokat, DASNET- és
NI-konfigurációk előállítását, valamint a szelep 1–5 V végpontjait. A fej nélküli
Qt-teszt igazolja, hogy az aktiválógomb csak sikeres kapcsolatpróba után válik
elérhetővé, sikertelen próba után pedig tiltva marad.

A diagnosztikai tesztek lefedik a kikapcsolt napló fájlmentességét, a
kategóriaszűrést, append-only fájlírást, inkrementális memóriaolvasást, DASNET
TX/RX eseményeket és az NI funkció szerinti kategorizálást. A Qt-teszt egyetlen
pumpakategóriát engedélyez, majd ellenőrzi, hogy a Developer táblában az NI esemény
nem, a pumpaesemény viszont megjelenik.

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
