# Biztonsági modell

## Alapelv

A rendszer magas nyomáson működik, ezért a szoftver hibája nem eredményezhet korlátlan vezérlőkimenetet. A fizikai nyomáshatárok, relief megoldások és vészleállítás elsődlegesek; a szoftver kiegészítő védelmi réteg.

## Minimális interlockok

- konfigurált maximális pumpanyomás túllépése;
- differenciálnyomás `Y` határának elérése;
- szabályozási cél fölé növő nyomás konfigurált `X` eltéréssel;
- szenzorjel tartományon kívül, nem szám vagy elavult adat;
- pumpa-, NI- vagy vezérlési kommunikáció elvesztése;
- vezérlési ciklus határidejének túllépése;
- kézi vészleállítás.

## Biztonságos állapot

A pontos fizikai reakció még helyszíni kockázatelemzést igényel. Addig a kód nem feltételezheti, hogy a szelep teljes nyitása vagy zárása minden esetben biztonságos. A prototípusban a biztonságos állapot eseményt, reteszelt hibát és szimulált STOP-kérést jelent.

## Reteszelés és visszaállítás

- Vészállapot után az automatikus újraindítás tilos.
- A hibát naplózni kell a kiváltó adatokkal.
- Újraindítás csak megszűnt ok, kezelői nyugtázás és előfeltétel-ellenőrzés után lehetséges.
- A konfiguráció változtatása ne törölje automatikusan az aktív hibát.

A fizikai pumpaadapter a safe-state `STOP` kérést élvezérelten reteszeli. Egy
hibaeseményben pumpánként legfeljebb egy `STOP` kerül a soros vonalra akkor is, ha
a pumpa `PROBLEM=LOCAL MODE` választ ad vagy több felügyeleti réteg ugyanazt a
safe-state-et kéri. Új STOP csak kezelői hibanyugtázás, sikeres REMOTE módba lépés
vagy új pumpafutás után engedélyezett. Ez a parancsismétlést korlátozza, a fizikai
vészleállítás és a pumpa saját védelmei továbbra is elsődlegesek.

A prototípus `SafetyMonitor` komponense reteszeli az észlelt hibákat. A retesz csak
kezelői nyugtázással és egy aktuálisan biztonságos mérési pillanatkép ismételt
kiértékelése után oldható. A nem véges (`NaN`, pozitív vagy negatív végtelen)
mérési érték, a kézi vészleállítás és a vezérlési határidő túllépése reteszelt
hibát vált ki.

Automata szabályozásban a kiválasztott nyomásforrás értéke külön céltúllövési
interlockot kap. A konfigurált célérték plusz a `max_control_overshoot_bar` eltérés
elérése reteszelt hibát és kimenettiltást vált ki. A dashboardon ez az eltérés
pozitív, véges barértékként állítható.

## Manuális biztonsági profil

A Developer manuális vezérlés nem készít teljes mérési pillanatképet minden
parancshoz. Pumpa-RUN előtt csak a kiválasztott, hozzáadott pumpa kapcsolatát,
véges nyomás-/áramlás-/térfogatadatát és saját maximális nyomását ellenőrzi.
A manuális szelepírás a megerősítés mellett a véges 0–100%-os tartományt és
az NI kimenet hardverengedélyét ellenőrzi. Nem hozzáadott vonali vagy
differenciálnyomás-bemenet nem generál manuális reteszt. A STOP, STOP ALL és
safe-state parancsok elsőbbsége, valamint a kapcsolatvesztés véges felismerési ideje
nem lazítható.

A vezetett funkcionális teszt csak HARDVER + READY állapotban, leállított normál
runtime, álló pumpák, aktuális sikeres kapcsolatpróba, aktív/reteszelt hiba nélküli
állapot és teljes kezelői ellenőrzőlista mellett indulhat. Megszakítás, ablakbezárás
és kivétel letiltja az új parancsokat, minden pumpán STOP-ot és SAFE AO-jelet kér.

A PID nyomásszűrése csak a szabályozási ágra hat. Az NI vonali és
differenciálnyomás kemény maximum-interlockja az EMA előtti, mediánból kalibrált
nyers nyomást értékeli, ezért a szűrés késleltetése nem takarhat el veszélyes
túllépést. A szűrt érték a PID, kijelzés, grafikon és céltúllövési felügyelet
bemenete. A túl gyakori irányváltás `VALVE_OSCILLATION`
runtime hibát és reteszelt safe-state útvonalat vált ki.

## Kötelező tesztek

Minden interlockhoz tartozzon határérték alatti, pontosan határértékű, határérték feletti, hibás adat és kapcsolatvesztési teszt. Biztonsági teszt hibája blokkolja a kiadást.
