# Mintaidős PID-szabályozás

## Egységes bemenet

A szelepvezérlés a kiválasztott forrást `PressureMeasurement` objektummá alakítja.
Az objektum együtt viszi a nyers és előszűrt nyomást, a monoton mintaidőt, az
adatkort, a sequence értéket, a forrásspecifikus minőséget és az utolsó hibát.
A támogatott források az ISCO besajtolópumpa `PRESS` telemetriája és a vonali NI
nyomásmérő. Automatikus fallback nincs.

Az ISCO cache `PRESS` frissítése külön sequence értéket kap. A 0,2 másodperces
felügyeleti ciklus ugyanazt a sequence-et csak `HOLD` állapotban felügyeli; ekkor
nem fut újra EMA, integrálás vagy deriválás. A vonali NI minden új mintacsomaghoz
külön sequence-et rendel. A nem kiválasztott forrás továbbra is mérve, mentve és
biztonságilag felügyelve marad.

## Szűrés és PID-idő

A pumpaforrás PID EMA-ja fizikai időállandót használ:

`alpha = 1 - exp(-measurement_dt / time_constant)`

Az első minta közvetlenül inicializál. A vonali forrás medián-, tüskeszűrését és
időalapú EMA-ját az analóg réteg végzi, ezért a PID ennél a forrásnál nem alkalmaz
második EMA-t. Az I- és D-tag a valódi forrásminták közötti idővel számol. A
konfigurált maximális mintaköznél hosszabb kimaradás után mindkettő kimarad. Az
integrátor külön minimum/maximum korlátot, clamp- és rate-limit anti-windupot kap.

A holtsáv hiszterézises: külön belépési és kilépési érték tartozik hozzá. A
holtsávban a kimenet és az integrátor változatlan, miközben az adatkor és a
minőség felügyelete tovább fut.

## Átmenetek és biztonság

Kézi–automata átmenet, futás közbeni PID-paraméterezés és nyomásforrás-váltás
bumpless inicializálást használ. Futás közbeni paramétermódosításnál az integrátor
az alkalmazott szelepkimenetből, valamint az új P- és D-tagból indul. Kézi–automata
és forrásváltáskor az első új mintán nincs I- vagy normál D-frissítés. Mérés közbeni
forrásváltás csak az **Alkalmaz** művelet külön megerősítése után történik, és csak
friss `GOOD` új forrásra engedélyezett.

Safe state után a vezérlési ciklus a safe feszültségből számított százalékot
követi, a PID integrálását és deriváltállapotát törli, állapota `SAFE`. Újraindulás
csak friss, `GOOD` mintával történhet. A PID-adatkorlát és a safety stale-határ
külön konfiguráció.

## Felület és diagnosztika

A Szelepvezérlés panel mutatja az aktív forrás nyers és PID által használt értékét,
adatkorát, minőségét, a PID állapotát, a szelep százalékát és az NI-feszültséget.
A részletes diagnosztika tartalmazza a sequence-et, measurement dt-t, P/I/D-tagokat,
a korlátozás előtti, clampelt és alkalmazott kimenetet, valamint a HOLD/BLOCKED
okot; a tartalom a releváns PID-konfigurációval együtt vágólapra másolható. A PID
nyers, szűrt és célérték görbéi, a szelepállás, valamint az új minták és
állapotváltások jelölései alapértelmezetten rejtettek, külön kapcsolhatók be.

## Migráció és fizikai validáció

A `pid/filter_alpha` a mentett felügyeleti ciklusból időállandóvá migrálódik, a
`pid/deadband_bar` pedig belépési és 1,4-szeres kilépési holtsávvá. A migrált
beállítás `migrated_unvalidated=true` jelölést kap. A PID-profil adatbázis sémája
6-os, a stabil profil sémája 4-es. A régi profilok használhatók, de fizikailag nem
validáltnak minősülnek.

A Kp/Ki/Kd, EMA-időállandó, kimeneti korlátok, forrásonkénti szelepirány és a
vonali kalibráció célgépes/hidraulikus validációt igényel. A `Kd` alapértéke 0.
A `REVERSE` konfiguráció önmagában nem bizonyít fizikai validációt. A szelepirány
validációja nyomásforrásonként külön tárolódik; az egyik forrás tesztje nem
validálja a másikat. Hardveres futás közben a vonali forrás validált kalibráció
nélkül nem aktiválható.
